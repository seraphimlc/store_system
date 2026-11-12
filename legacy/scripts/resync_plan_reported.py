# -*- coding: utf-8 -*-
"""**修复**：把"已自报"重新对齐到计划表（`staff_date_plans.reported`）。

背景（2026-10-01）：自报写透（`mark_reported`）是后加的，**上线前已存在的自报没有计划行**，
于是在日期计划矩阵里那些"其实报了"的人显示成 `–`/计划值，看着像没报。
本脚本按自报表把计划表对齐：

1. 每条自报 → `mark_reported(..., True)`（没有计划行就补一条 `source='report'`）；
2. 计划行里 `reported=True` 但**没有对应自报**的 → 置回 False（反向清理）。

默认 **dry-run 只报告**，加 `--apply` 才写库（生产可用，只动 `staff_date_plans` 这一张新表）。

用法：

```bash
# 看差多少（默认：全部日期）
DATABASE_URL="sqlite:///./store_settle_live.db" ./.venv/bin/python scripts/resync_plan_reported.py
# 只修 2026-10 起的
DATABASE_URL=... ./.venv/bin/python scripts/resync_plan_reported.py --from 2026-10-01
# 真写
DATABASE_URL=... ./.venv/bin/python scripts/resync_plan_reported.py --from 2026-10-01 --apply
```
"""
import argparse
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import SessionLocal                                  # noqa: E402
from app.models import StaffDailyReport, StaffDatePlan           # noqa: E402
from app.services import date_plan as dp                         # noqa: E402


def _as_date(s):
    try:
        return datetime.strptime((s or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", default="", help="起始日期 YYYY-MM-DD")
    ap.add_argument("--to", dest="end", default="", help="结束日期 YYYY-MM-DD")
    ap.add_argument("--apply", action="store_true", help="真的写库（默认只报告）")
    args = ap.parse_args()
    start, end = _as_date(args.start), _as_date(args.end)

    db = SessionLocal()
    try:
        q = db.query(StaffDailyReport.person_code, StaffDailyReport.report_date)
        if start:
            q = q.filter(StaffDailyReport.report_date >= start)
        if end:
            q = q.filter(StaffDailyReport.report_date <= end)
        reports = {(c, d) for c, d in q.all()}

        pq = db.query(StaffDatePlan)
        if start:
            pq = pq.filter(StaffDatePlan.plan_date >= start)
        if end:
            pq = pq.filter(StaffDatePlan.plan_date <= end)
        plans = pq.all()
        plan_keys = {(r.person_code, r.plan_date) for r in plans}

        missing = sorted(reports - plan_keys)          # 有自报、没计划行 → 要补
        stale = sorted({(r.person_code, r.plan_date) for r in plans if r.reported}
                       - reports)                      # 标了已自报、其实没有 → 要撤
        print("区间: %s ~ %s" % (start or "最早", end or "最新"))
        print("自报 %d 条 / 计划行 %d 条" % (len(reports), len(plans)))
        print("要补计划行（有自报没行）: %d 条" % len(missing))
        for c, d in missing[:10]:
            print("   + %s %s" % (c, d))
        print("要撤回 reported（没有自报）: %d 条" % len(stale))
        for c, d in stale[:10]:
            print("   - %s %s" % (c, d))

        if not args.apply:
            print("\n（dry-run：加 --apply 才写库）")
            return
        if start:
            start = start or date.min
        n1 = dp.rebuild_reported(db, start=start, end=end)   # 撤掉虚标的
        for c, d in missing:                                # 补上缺的行
            dp.mark_reported(db, c, d, True)
        print("\n已写库：补 %d 行，撤回 %d 行" % (len(missing), n1))
    finally:
        db.close()


if __name__ == "__main__":
    main()
