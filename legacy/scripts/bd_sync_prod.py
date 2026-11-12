# -*- coding: utf-8 -*-
"""作业域数据同步：**本地 → 线上**（车站/线路/物理车站/队伍/成员/任务）。

用户 2026-10-06 发布要求：
    1. "车站相关的数据都同步到线上"；
    2. "已经分配给6个队伍的任务也建好，队长能看得到任务，并做好这些存量任务的进度设置"

设计取舍（为什么这么做）：
- **保留主键**：线上作业域表是**空的**（从没上过线）→ 直接带 id 灌进去最简单、最不容易错，
  `bd_task.place_id` / `team_id` / `bd_team_member.team_id` 这些引用天然对齐。
  ⚠️ PG 上显式插 id **不会推进序列** → 灌完必须 `setval`（脚本里做了，否则后续插入撞主键）。
- **进度清零**（默认）：本地是试跑数据（我点过"标记完成"、调过进度）→
  线上要"干净开局"，由队长按真实情况补录。`--keep-progress` 可保留。
- **只同步结构 + 队伍 + 任务**：不同步 `bd_task_assign` / `bd_task_progress` /
  `bd_message` / `bd_log`（都是试跑痕迹）。
- **人员不搬**：`persons` / `users` 线上已有（结算域在用）→ 脚本只**核对**被引用的人是否都在，
  并报告 6 个队长的 `role` 是不是 `leader`（不是的话队长看不到"任务管理"）。

用法：
    # ① 本地导出（默认读 DATABASE_URL）
    DATABASE_URL="sqlite:///./store_settle_live.db" ./.venv/bin/python \\
        scripts/bd_sync_prod.py --export /tmp/bd_ops.json

    # ② 线上导入（**在容器里**，DATABASE_URL 指向 PG）
    docker compose exec web python scripts/bd_sync_prod.py --import /tmp/bd_ops.json --dry-run
    docker compose exec web python scripts/bd_sync_prod.py --import /tmp/bd_ops.json
"""
import argparse
import json
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.db as appdb  # noqa: E402
from sqlalchemy import func, inspect, text  # noqa: E402

#: 同步的表（顺序 = 外键依赖顺序）
TABLES = ("bd_area", "bd_line", "bd_station_place", "bd_station",
          "bd_team", "bd_team_member", "bd_task")
#: 任务表里"要清零"的进度字段（线上干净开局，队长补录真实进度）
TASK_PROGRESS_COLS = ("pct", "start_date", "done_date", "note", "store_count")
#: 显式插 id 后需要修序列的表（PG）
SEQ_TABLES = TABLES


def _dump_scalar(v):
    if isinstance(v, (datetime, date)):
        return {"__dt__": v.isoformat(), "__d__": isinstance(v, date)
                and not isinstance(v, datetime)}
    return v


def _load_scalar(v):
    if isinstance(v, dict) and "__dt__" in v:
        s = v["__dt__"]
        return date.fromisoformat(s) if v.get("__d__") else datetime.fromisoformat(s)
    return v


def export(path: str, with_progress: bool = False) -> dict:
    from sqlalchemy import select
    db = appdb.SessionLocal()
    try:
        meta = inspect(appdb.get_engine())
        out = {"tables": {}, "counts": {}, "version": 1,
               "exported_at": datetime.utcnow().isoformat()}
        for t in TABLES:
            if t not in meta.get_table_names():
                out["tables"][t] = []
                out["counts"][t] = 0
                continue
            rows = [dict(r._mapping) for r in db.execute(select(text("*")).select_from(
                text(t)))]
            rows = [{k: _dump_scalar(v) for k, v in r.items()} for r in rows]
            out["tables"][t] = rows
            out["counts"][t] = len(rows)
    finally:
        db.close()
    if not with_progress:
        for r in out["tables"].get("bd_task", []):
            # ⚠️ `note` 是 NOT NULL → 清成 ""（不是 None，实测踩过 IntegrityError）
            for c in TASK_PROGRESS_COLS:
                if c in r:
                    r[c] = 0 if c == "pct" else ("" if c == "note" else None)
                    # store_count 也清空（线上由队员完成时填，2026-10-06）
            if "state" in r:
                r["state"] = "unassigned"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    return out


def _report_referenced(db, payload: dict) -> dict:
    """核对：被引用的人/账号在不在？6 个队长的 role 是不是 leader？"""
    from app.models import Person, User
    codes = {r.get("person_code") for t in ("bd_team_member",)
             for r in payload["tables"].get(t, []) if r.get("person_code")}
    have = {c for (c,) in db.query(Person.code).filter(Person.code.in_(codes)).all()} \
        if codes else set()
    missing = sorted(codes - have)
    leaders = [r.get("person_code") for r in payload["tables"].get("bd_team_member", [])
               if (r.get("role") or "") == "leader" and not r.get("end_date")]
    users = {u.person_code: u for u in db.query(User)
             .filter(User.person_code.in_([c for c in leaders if c])).all()}
    leader_report = []
    for c in leaders:
        u = users.get(c)
        leader_report.append({
            "person_code": c,
            "account": (u.username if u else None),
            "role": (u.role if u else None),
            "ok": bool(u is not None and u.role == "leader"),
        })
    return {"missing_persons": missing, "leaders": leader_report}


def do_import(path: str, dry_run: bool = False, replace: bool = False) -> int:
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    db = appdb.SessionLocal()
    engine = appdb.get_engine()
    meta = inspect(engine)
    print("== 目标库：%s" % str(engine.url).split("@")[-1])
    # 0) 表是否存在（迁移跑没跑）
    absent = [t for t in TABLES if t not in meta.get_table_names()]
    if absent:
        print("  ❌ 目标库缺表：%s" % ", ".join(absent))
        print("     → 先在容器里跑 alembic：docker compose exec web alembic upgrade head")
        db.close()
        return 2
    # 1) 现状
    cur = {t: int(db.execute(text("SELECT count(*) FROM %s" % t)).scalar() or 0)
           for t in TABLES}
    print("  当前行数：" + " ".join("%s=%d" % (k, v) for k, v in cur.items()))
    print("  待导入：" + " ".join("%s=%d" % (k, payload["counts"].get(k, 0))
                                 for k in TABLES))
    if any(cur.values()) and not replace:
        print("  ❌ 目标表非空 → 拒绝导入（防重复）。要清空重灌请加 --replace")
        db.close()
        return 2
    # 2) 核对引用的人 + 队长 role
    rep = _report_referenced(db, payload)
    if rep["missing_persons"]:
        print("  ⚠️ 目标库缺 %d 个人员（成员）: %s"
              % (len(rep["missing_persons"]), ", ".join(rep["missing_persons"][:8])))
    bad = [x for x in rep["leaders"] if not x["ok"]]
    print("  队长账号核对：%d 个队长，%d 个有问题" % (len(rep["leaders"]), len(bad)))
    for x in rep["leaders"]:
        flag = "✅" if x["ok"] else "❌"
        print("    %s %-18s 账号=%-14s role=%s"
              % (flag, x["person_code"], x["account"] or "（无账号）", x["role"] or "-"))
    if dry_run:
        print("  [dry-run] 不写库。")
        db.close()
        return 0
    # 3) 清空（--replace）
    if replace:
        for t in reversed(TABLES):
            db.execute(text("DELETE FROM %s" % t))
        db.commit()
        print("  已清空目标表")
    # 4) 插入（显式带 id）
    for t in TABLES:
        rows = payload["tables"].get(t, [])
        if not rows:
            continue
        cols = list(rows[0].keys())
        collist = ", ".join('"%s"' % c for c in cols)
        ph = ", ".join(":%s" % c for c in cols)
        sql = text('INSERT INTO %s (%s) VALUES (%s)' % (t, collist, ph))
        for r in rows:
            db.execute(sql, {k: _load_scalar(v) for k, v in r.items()})
        db.commit()
        print("  已导入 %-18s %d 行" % (t, len(rows)))
    # 5) PG：显式插 id 不推进序列 → 必须 setval，否则后续插入撞主键
    if engine.dialect.name == "postgresql":
        for t in SEQ_TABLES:
            try:
                db.execute(text(
                    "SELECT setval(pg_get_serial_sequence('%s','id'), "
                    "COALESCE((SELECT max(id) FROM %s), 1))" % (t, t)))
            except Exception as e:                       # noqa: BLE001
                print("  ⚠️ 序列修正失败 %s: %s" % (t, str(e)[:60]))
        db.commit()
        print("  已修正 PG 自增序列")
    # 6) 核对
    print("== 导入后核对")
    ok = True
    for t in TABLES:
        n = int(db.execute(text("SELECT count(*) FROM %s" % t)).scalar() or 0)
        want = payload["counts"].get(t, 0)
        flag = "✅" if n == want else "❌"
        ok = ok and n == want
        print("  %s %-18s %d / %d" % (flag, t, n, want))
    db.close()
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="作业域数据同步（本地 → 线上）")
    ap.add_argument("--export", metavar="PATH", help="导出到 JSON（本地跑）")
    ap.add_argument("--import", dest="imp", metavar="PATH",
                    help="从 JSON 导入（在线上容器里跑）")
    ap.add_argument("--dry-run", action="store_true", help="只统计与核对，不写库")
    ap.add_argument("--replace", action="store_true", help="先清空目标表再灌（幂等重灌）")
    ap.add_argument("--keep-progress", action="store_true",
                    help="保留任务进度/完成日（默认清零，线上由队长补录）")
    a = ap.parse_args()
    if a.export:
        out = export(a.export, with_progress=a.keep_progress)
        print("== 已导出 %s" % a.export)
        for k, v in out["counts"].items():
            print("  %-18s %d" % (k, v))
        if not a.keep_progress:
            print("  （任务进度已清零：pct=0 / start_date=done_date=None / state=unassigned）")
        return 0
    if a.imp:
        return do_import(a.imp, dry_run=a.dry_run, replace=a.replace)
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
