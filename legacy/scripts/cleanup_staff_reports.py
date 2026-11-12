# -*- coding: utf-8 -*-
"""清理自报 / 核对报告（演示或测试数据）。

默认 **dry-run**（只打印将删除什么）；加 `--apply` 才真删。

- 删自报：`--person <编号> --start 2026-09-01 --end 2026-09-15`
  已对账（日期 ≤ 正式数据最后一天）的默认**不动**，必须显式 `--force` 才删
  （界面上的锁定规则不变，这个开关是给"清理演示数据"用的）。
- 删报告：`--analyses` 一并删除与区间重叠的分析报告（含物化行）；
  或 `--analysis-id N` 精确删某一份。

例：
  DATABASE_URL="sqlite:///./store_settle_live.db" ./.venv/bin/python \
      scripts/cleanup_staff_reports.py --person 2188240626279038 \
      --start 2026-09-01 --end 2026-09-15 --analyses            # 先看
  … 再加 --apply --force 真删
"""
import argparse
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.db as appdb                                    # noqa: E402
from app.models import StaffDailyReport, StaffReportAnalysis  # noqa: E402
from app.services import daily_report, report_store       # noqa: E402


def _d(s):
    y, m, dd = (int(x) for x in s.split("-"))
    return date(y, m, dd)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--person", default="", help="员工编号（留空=全部）")
    ap.add_argument("--start", required=True, help="起始日期 YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="结束日期 YYYY-MM-DD")
    ap.add_argument("--analyses", action="store_true",
                    help="同时删除与区间重叠的分析报告（含物化行）")
    ap.add_argument("--analysis-id", type=int, default=0, help="只删这一份报告")
    ap.add_argument("--apply", action="store_true", help="真的执行（默认 dry-run）")
    ap.add_argument("--force", action="store_true",
                    help="连已对账（锁定）的自报也删 —— 仅用于清理演示数据")
    args = ap.parse_args()

    start, end = _d(args.start), _d(args.end)
    db = appdb.SessionLocal()
    cov_end = daily_report.coverage_end(db)

    q = db.query(StaffDailyReport).filter(
        StaffDailyReport.report_date >= start,
        StaffDailyReport.report_date <= end)
    if args.person:
        q = q.filter(StaffDailyReport.person_code == args.person)
    rows = q.order_by(StaffDailyReport.person_code,
                      StaffDailyReport.report_date).all()

    locked = [r for r in rows if daily_report.is_locked(db, r.report_date)]
    print("自报：命中 %d 条（其中已对账锁定 %d 条；正式数据覆盖到 %s）"
          % (len(rows), len(locked), cov_end or "无"))
    for r in rows:
        flag = "已对账" if r in locked else "未对账"
        print("   %s %s %s %s/%s=%s [%s]"
              % (r.report_date, r.person_code, r.area or "-", r.p1_cnt,
                 r.p2_cnt, r.total_cnt, flag))
    if locked and not args.force:
        print("   → 已对账的那些不动（要删请加 --force）")

    analyses = []
    if args.analysis_id:
        a = db.get(StaffReportAnalysis, args.analysis_id)
        analyses = [a] if a else []
    elif args.analyses:
        analyses = (db.query(StaffReportAnalysis)
                    .filter(StaffReportAnalysis.period_start <= end,
                            StaffReportAnalysis.period_end >= start)
                    .order_by(StaffReportAnalysis.id).all())
    if analyses:
        print("报告：命中 %d 份" % len(analyses))
        for a in analyses:
            print("   id=%s %s~%s %s" % (a.id, a.period_start, a.period_end,
                                         a.status))

    if not args.apply:
        print("\n（dry-run；确认后加 --apply 执行）")
        db.close()
        return 0

    removed = 0
    for r in rows:
        if r in locked and not args.force:
            continue
        if daily_report.delete_by_admin(db, person_code=r.person_code,
                                        report_date=r.report_date, force=True):
            removed += 1
    for a in analyses:
        report_store.delete_analysis(db, a.id)
    print("\n已删除：自报 %d 条 / 报告 %d 份" % (removed, len(analyses)))
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
