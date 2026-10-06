# -*- coding: utf-8 -*-
"""第一轮 515 条任务的**初始化**（把当初的派工结果按新口径重建）。

用户 2026-10-06："515 条任务的初始化…做吧。开始吧。"

两份数据来源（缺一不可）：
- **车站**：车站资产表 `bd_station`（站名+线路 → 物理车站 `bd_station_place`）—— 515 个站全在
- **队伍归属**：删旧任务前导出的归档 CSV（`data/backup_tasks_<时间>.csv`）——
  ⚠️ `bd_log` 里的派队日志是 **0 条**（当初种子脚本没写 dispatch 日志），所以归档 CSV 是唯一电子来源
  （原始 6 份 xlsx 在 `万总/team_task/`，可作人工核对）

口径（与 2026-10-05 定稿一致）：
- **1 个物理车站 = 1 个任务**（跨线站只 1 个）；分配日期 = 今天；只到队伍（担当由队长分）
- 匹配优先级：① 站名 + 线路（严格）② 站名 + 线路（宽松：去「N号線」前缀/后缀包含）
  ③ 站名唯一时退化按站名 ④ 都不行 → **列入待人工处理，不猜**
  （实测只有 `栄町`（汤静队・原串「東京さくらトラム」）需要别名消歧 → 走都電荒川線那个）

用法：
    ./.venv/bin/python scripts/bd_init_515.py                 # 试算（默认）
    DATABASE_URL="sqlite:///./store_settle_live.db" ./.venv/bin/python scripts/bd_init_515.py --apply
"""
from __future__ import annotations

import argparse
import collections
import csv
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DEF_CSV = os.path.join(ROOT, "data", "backup_tasks_20261005_011638.csv")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=DEF_CSV, help="归档 CSV（含 队伍 列）")
    ap.add_argument("--apply", action="store_true", help="真写库（默认只试算）")
    args = ap.parse_args()

    from sqlalchemy import text

    from app.db import SessionLocal
    from app.services import bd_lines, bd_tasks
    from app.services.date_plan import jst_today

    if not os.path.exists(args.csv):
        print("✗ 找不到归档 CSV：%s" % args.csv)
        return 2
    rows = [r for r in csv.DictReader(io.open(args.csv, encoding="utf-8-sig"))
            if (r.get("队伍") or "").strip()]
    print("归档 CSV：%d 条（%s）" % (len(rows), os.path.basename(args.csv)))

    db = SessionLocal()
    q = lambda s, **kw: db.execute(text(s), kw).all()          # noqa: E731

    # 线路名 → 该线的所有 (站名 → place_id)
    by_line, by_name = {}, collections.defaultdict(set)
    for pid, nm, lname, lshort in q("""SELECT s.place_id, s.name, l.name, l.operator_short
            FROM bd_station s JOIN bd_line l ON l.id = s.line_id
            WHERE s.place_id IS NOT NULL"""):
        by_line.setdefault(bd_lines.norm_line(lname), {})[nm] = pid
        by_line.setdefault(bd_lines.norm_line(lshort + lname), {})[nm] = pid
        by_name[nm].add(pid)

    plan = collections.defaultdict(list)          # 队名 → [place_id]
    how = collections.Counter()
    unresolved = []
    for r in rows:
        nm = (r.get("车站") or "").strip()
        base = bd_lines.norm_line(r.get("线路") or r.get("线路名") or "")
        teams = (r.get("队伍") or "").strip()
        pid = None
        if base:
            hit = by_line.get(base, {}).get(nm)                    # ① 严格
            if hit:
                pid, w = hit, "strict"
            else:                                                  # ② 宽松（去 N号線 / 后缀包含）
                core = base.lstrip("0123456789").replace("号線", "")
                for k, m in by_line.items():
                    kk = k.lstrip("0123456789").replace("号線", "")
                    if nm in m and (kk == core or (len(core) >= 3 and
                                                   (core in kk or kk in core))):
                        if pid and pid != m[nm]:
                            pid = None                             # 命中多个 → 不猜
                            break
                        pid = m[nm]
                if pid:
                    w = "loose"
        if not pid:                                                # ③ 站名唯一
            cand = by_name.get(nm) or set()
            if len(cand) == 1:
                pid, w = next(iter(cand)), "name-only"
            elif len(cand) > 1:
                unresolved.append((nm, teams, base, "同名多站(%d)" % len(cand)))
                continue
            else:
                unresolved.append((nm, teams, base, "车站表里找不到"))
                continue
        plan[teams].append(pid)
        how[w] += 1

    print("\n=== 匹配情况 ===")
    print("  ① 站名+线路 严格命中: %d" % how["strict"])
    print("  ② 站名+线路 宽松命中: %d" % how["loose"])
    print("  ③ 站名唯一命中: %d" % how["name-only"])
    print("  ④ 对不上（待人工）: %d %s" % (len(unresolved), unresolved or ""))
    total_places = sum(len(v) for v in plan.values())
    print("\n=== 按队（队伍 → 物理车站数）===")
    teams = {t.name: t.id for t in q("SELECT id, name FROM bd_team")}
    todo = []
    for tname, pids in sorted(plan.items(), key=lambda x: -len(x[1])):
        uniq = list(dict.fromkeys(pids))
        has = len(pids) - len(uniq)
        tid = teams.get(tname)
        flag = "" if tid else "  ⚠️ 队伍不存在，跳过"
        print("  %-8s %3d 站 → %3d 个物理车站%s%s"
              % (tname, len(pids), len(uniq), ("（去重 %d）" % has) if has else "", flag))
        if tid:
            todo.append((tname, tid, uniq))
    print("  合计 %d 个站 → **%d 个任务**（分配日期 %s）"
          % (total_places, sum(len(x[2]) for x in todo), jst_today()))

    if not args.apply:
        print("\n（dry-run；加 --apply 才写库）")
        db.close()
        return 0

    print("\n=== 开始落库 ===")
    created = skipped = 0
    for tname, tid, pids in todo:
        r = bd_tasks.create_tasks_for_places(db, pids, by="init:515", team_id=tid,
                                             actor_user=None)
        created += r["created"]
        skipped += r["skipped"]
        print("  %-8s 新建 %3d / 跳过 %3d（已有任务）" % (tname, r["created"], r["skipped"]))
    print("\n合计：**新建 %d 个任务，跳过 %d 个**（分配日期 %s）"
          % (created, skipped, jst_today()))
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
