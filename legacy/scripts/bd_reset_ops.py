# -*- coding: utf-8 -*-
"""作业域**重置**：清掉旧任务层 / 清掉指定团队（2026-10-05 用户要求）。

用户口径：
- "之前的 excel 文件导入的任务，全都可以删除。后面我们再做任务分配，没问题的。"
- "测试队/测试2队可以清掉。"

新流程（本案）：**车站数据资产 → 自动出任务 → Excel 当派工单**。
所以旧任务层（当初按"站×线"建的 1,920 个）要清掉，改以**物理车站**重建（另一个脚本）。

⚠️ 这是**破坏性操作**，所以：
1. 默认 **dry-run**，`--apply` 才真删；
2. 自动备份整个库文件；
3. 删任务前先把 `bd_task` 全量**归档成 CSV**（`data/backup_tasks_<时间>.csv`）；
4. 写 `bd_log` 留痕（日志是追加表，**不删**——清任务后旧日志仍指向旧 id，这是硬删的代价，
   要彻底干净得另跑日志清理）；
5. 清队会连带删成员关系（那些人变回自由人）并 `sync_account_roles`（不当队长的人自动退回 staff）。

用法：
    ./.venv/bin/python scripts/bd_reset_ops.py                                  # 试算
    ./.venv/bin/python scripts/bd_reset_ops.py --apply                           # 清任务
    ./.venv/bin/python scripts/bd_reset_ops.py --apply --teams "测试队,测试2队"    # 清任务 + 清队
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真删（默认只试算）")
    ap.add_argument("--tasks", action="store_true", help="清空任务层（bd_task*）")
    ap.add_argument("--teams", default="", help="要清掉的队名，逗号分隔")
    ap.add_argument("--out", default=os.path.join(ROOT, "data"))
    args = ap.parse_args()

    from sqlalchemy import text

    from app.config import get_settings
    from app.db import SessionLocal, get_engine
    from app.services import bd_log, bd_teams

    url = get_settings().database_url
    print("库:", url)
    db = SessionLocal()
    names = [x.strip() for x in args.teams.split(",") if x.strip()]

    n_task = db.execute(text("SELECT COUNT(*) FROM bd_task")).scalar()
    n_prog = db.execute(text("SELECT COUNT(*) FROM bd_task_progress")).scalar()
    n_asg = db.execute(text("SELECT COUNT(*) FROM bd_task_assign")).scalar()
    print("\n将要做的事：")
    if args.tasks:
        print("  清任务层：bd_task %d 行 / 进展 %d 行 / 担当 %d 行" % (n_task, n_prog, n_asg))
    for nm in names:
        rows = db.execute(text("SELECT id FROM bd_team WHERE name=:n"), {"n": nm}).all()
        if not rows:
            print("  ⚠️ 队不存在（跳过）：%s" % nm)
            continue
        for (tid,) in rows:
            n_mem = db.execute(text(
                "SELECT COUNT(*) FROM bd_team_member WHERE team_id=:t"), {"t": tid}).scalar()
            n_act = db.execute(text(
                "SELECT COUNT(*) FROM bd_team_member WHERE team_id=:t AND end_date IS NULL"),
                {"t": tid}).scalar()
            print("  清队「%s」(id=%d)：成员关系 %d 行（现役 %d）→ 这些人变自由人"
                  % (nm, tid, n_mem, n_act))
    if not args.apply:
        print("\n（dry-run；加 --apply 才真做）")
        db.close()
        return 0

    # ---------- 备份 + 归档 ----------
    path = url.replace("sqlite:///", "").split("?")[0]
    if os.path.exists(path):
        bak = "%s.pre_reset_%s" % (path, time.strftime("%Y%m%d_%H%M%S"))
        shutil.copy2(path, bak)
        print("\n已备份库 →", bak)
    if args.tasks and n_task:
        os.makedirs(args.out, exist_ok=True)
        csvp = os.path.join(args.out, "backup_tasks_%s.csv" % time.strftime("%Y%m%d_%H%M%S"))
        rows = db.execute(text("""
            SELECT t.id, s.name, s.line, l.operator_short, l.name, tm.name,
                   t.assign_date, t.state, t.pct, t.start_date, t.done_date,
                   t.source_type, t.created_by, t.note
              FROM bd_task t LEFT JOIN bd_station s ON s.id = t.station_id
              LEFT JOIN bd_line l ON l.id = s.line_id
              LEFT JOIN bd_team tm ON tm.id = t.team_id
             ORDER BY t.id""")).all()
        with io.open(csvp, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["任务id", "车站", "线路", "运营公司", "线路名", "队伍",
                        "分配日期", "状态", "进度", "开始日", "完成日", "来源", "建者", "备注"])
            for r in rows:
                w.writerow(["" if x is None else
                            (x.strftime("%Y-%m-%d") if hasattr(x, "strftime") else x)
                            for x in r])
        print("已归档 %d 个任务 → %s" % (len(rows), csvp))

    # ---------- 清任务层 ----------
    if args.tasks:
        db.execute(text("DELETE FROM bd_task_progress"))
        db.execute(text("DELETE FROM bd_task_assign"))
        db.execute(text("DELETE FROM bd_task"))
        bd_log.log(db, "task", "wipe", ref_label="全部任务", field="任务数",
                   old=str(n_task), new="0", actor="admin", actor_name="管理员",
                   note="旧任务层按用户要求清空，改为以车站数据资产重建后重新派工")
        print("已清任务层 %d 行" % n_task)

    # ---------- 清队 ----------
    for nm in names:
        for (tid,) in db.execute(text("SELECT id FROM bd_team WHERE name=:n"),
                                 {"n": nm}).all():
            n_mem = db.execute(text("SELECT COUNT(*) FROM bd_team_member "
                                    "WHERE team_id=:t"), {"t": tid}).scalar()
            bd_log.log(db, "team", "wipe", ref_id=tid, ref_label=nm,
                       field="成员关系", old=str(n_mem), new="0",
                       actor="admin", actor_name="管理员",
                       note="测试队清理（用户要求）")
            db.execute(text("DELETE FROM bd_team_member WHERE team_id=:t"), {"t": tid})
            db.execute(text("DELETE FROM bd_team WHERE id=:t"), {"t": tid})
            print("已清队「%s」(id=%d)：成员关系 %d 行" % (nm, tid, n_mem))
    db.flush()
    roles = bd_teams.sync_account_roles(db)      # 不再是队长的人退回 staff
    db.commit()

    left_t = db.execute(text("SELECT COUNT(*) FROM bd_task")).scalar()
    left_team = db.execute(text("SELECT COUNT(*) FROM bd_team")).scalar()
    left_st = db.execute(text("SELECT COUNT(*) FROM bd_station")).scalar()
    left_pl = db.execute(text("SELECT COUNT(*) FROM bd_station_place")).scalar()
    print("\n=== 完成 ===")
    print("任务 %d → %d；队 %d 个；**车站资产保留**：站×线 %d / 物理车站 %d"
          % (n_task, left_t, left_team, left_st, left_pl))
    print("账号角色同步：", roles)
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
