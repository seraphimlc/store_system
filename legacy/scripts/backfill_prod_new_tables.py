# -*- coding: utf-8 -*-
"""线上新表数据回填（**不重导**：只用线上现有数据重建新表）。

背景与设计见 docs/发布计划-线上新表迁移.md。核心：
1. `payroll_adjusts`（找平表）← 由 `payroll_period_rows.diff_amount` 逐人生成
2. `payroll_payments`（发放台账）← 由**已发月份**的分期表金额逐人逐期重建
   （用户口径：导出即发放；8 月两期已发完、9 月上半月已发、7 月不管）
3. `payroll_settlement_links` + 找平进度 ← 对重建的台账跑 FIFO 分配

**幂等**：先清空这 4 张新表再重建；只写新表，**老表零改动**。

用法（cwd=项目根）：
    # 本地演练（默认：8 月两期 + 9 月上半月）
    DATABASE_URL="sqlite:///./store_settle_live.db" ./.venv/bin/python \\
        scripts/backfill_prod_new_tables.py

    # 线上（在容器内执行，DATABASE_URL 指向 PG）
    docker compose exec web python scripts/backfill_prod_new_tables.py --dry-run
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.db as appdb
from app.models import (PayrollAdjust, PayrollPaidMark, PayrollPayment,
                        PayrollPeriodRow, PayrollSettlementLink, Person)
from app.services import period

# 已发薪的（月, 期）——用户口径 2026-09-25：8 月两期已发完、9 月上半月已发、7 月不管
PAID_INSTALLMENTS = [
    ("2026-08", 1), ("2026-08", 2),
    ("2026-09", 1),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只统计不写入")
    ap.add_argument("--months", default="",
                    help="参与找平表生成的月份（逗号分隔；默认=有正式表的月份）")
    args = ap.parse_args()

    db = appdb.SessionLocal()
    names = {p.code: p.display_name for p in db.query(Person).all()}

    months = ([m.strip() for m in args.months.split(",") if m.strip()]
              or sorted({r.month for r in db.query(PayrollPeriodRow).all()}))
    print(f"=== 参与回填的月份（找平表）：{months} ===")
    print(f"=== 已发薪的期：{PAID_INSTALLMENTS} ===")

    if args.dry_run:
        for m in months:
            rows = db.query(PayrollPeriodRow).filter_by(month=m).all()
            diff = sum(r.diff_amount or 0 for r in rows)
            has_recon = period.month_has_recon(db, m)
            note = ("" if has_recon else
                    "  ← **无对账**：将按当前规则归零（旧规则遗留值不写入新表）")
            print(f"  {m}: {len(rows)} 人 | 线上差异合计 {diff:,}{note}")
            if not has_recon:
                n = sum(1 for r in rows if (r.diff_amount or 0) != 0)
                print(f"       （将修正 {n} 行差异为 0 → 找平表该月 0 笔）")
        for m, seq in PAID_INSTALLMENTS:
            rows = db.query(PayrollPeriodRow).filter_by(month=m).all()
            amt = sum((r.half1_amount if seq == 1 else r.half2_amount) or 0
                      for r in rows)
            print(f"  将登记台账 {m} seq{seq}: {len(rows)} 人 | 应发合计 {amt:,}")
        print("\n(dry-run：未写入任何数据)")
        db.close()
        return

    # ---- 1) 清空新表（幂等：先清后建）----
    print("\n=== 1) 清空新表（只动新表，老表零改动）===")
    print("  links:", db.query(PayrollSettlementLink).delete())
    print("  payments:", db.query(PayrollPayment).delete())
    print("  adjusts:", db.query(PayrollAdjust).delete())
    print("  paid_marks:", db.query(PayrollPaidMark).delete())
    db.commit()

    # ---- 1.5) **按当前规则修正差异**：无对账月份 → 差异归零 ----
    # 为什么必须做：线上 payroll_period_rows.diff_amount 里可能有**旧规则**留下的值
    # （旧规则"无对账 = 全额扣" → 差异 = −整月工资；实测线上 2026-09 为 −5,173,750）。
    # 若照抄进找平表，会给每个人造出巨额假欠款，并从未发工资里扣。
    # 当前规则：该月**无当前对账任务** → 差异 = 0（页面标「待对账」）。
    print("\n=== 1.5) 按当前规则修正差异（无对账 → 0）===")
    fixed = 0
    for m in months:
        if period.month_has_recon(db, m):
            print("  %s: 有对账任务 → 保留线上差异值" % m)
            continue
        rows = db.query(PayrollPeriodRow).filter_by(month=m).all()
        n = sum(1 for r in rows if (r.diff_amount or 0) != 0)
        for r in rows:
            if (r.diff_amount or 0) != 0:
                r.diff_amount = 0
            # 遗留列同步（该列被页面/报表消费；不同步会出现"双源"分叉）
            if (r.adjust_amount or 0) != (r.diff_amount or 0):
                r.adjust_amount = r.diff_amount
        if n:
            db.commit()
            print("  %s: **无对账** → 差异归零（修正 %d 行，旧规则遗留）" % (m, n))
        else:
            print("  %s: 无对账 → 差异本已为 0" % m)
        fixed += n
    if fixed:
        print("  （共修正 %d 行；回退方式：从发布前备份恢复 payroll_period_rows.diff_amount）" % fixed)

    # ---- 2) 找平表：按各月差异逐人生成 ----
    print("\n=== 2) 生成找平表（payroll_adjusts）===")
    total_adj = 0
    for m in months:
        n = period.sync_adjusts(db, m)
        s = sum(a.adjust_amount or 0 for a in
                db.query(PayrollAdjust).filter_by(source_month=m).all())
        total_adj += s
        print(f"  {m}: {n} 笔 | 金额合计 {s:,}")
    print(f"  合计 {db.query(PayrollAdjust).count()} 笔 / {total_adj:,} 円")

    # ---- 3) 台账：按已发期重建（导出即发放）----
    print("\n=== 3) 重建发放台账（payroll_payments）===")
    tot_pay = tot_adj_applied = 0
    for m, seq in sorted(PAID_INSTALLMENTS):
        rows = db.query(PayrollPeriodRow).filter_by(month=m).all()
        n = 0
        for r in rows:
            amt = (r.half1_amount if seq == 1 else r.half2_amount) or 0
            pts = (r.half1_points if seq == 1 else r.half2_points) or 0
            if amt == 0 and pts == 0:
                continue          # 该期无数据/未发 → 不登记，避免把"未发"记成"已发"
            if period.record_payment(db, m, r.person_code, seq):
                n += 1
        pay = sum(p.amount or 0 for p in
                  db.query(PayrollPayment).filter_by(month=m, seq=seq).all())
        adj = sum(p.adjust_applied or 0 for p in
                  db.query(PayrollPayment).filter_by(month=m, seq=seq).all())
        tot_pay += pay
        tot_adj_applied += adj
        print(f"  {m} seq{seq}: {n} 人 | 应发 {pay:,} | 抵扣 {adj:,}")
    print(f"  台账合计 {db.query(PayrollPayment).count()} 行 / 应发 {tot_pay:,} / 抵扣 {tot_adj_applied:,}")

    # ---- 4) 结果核对 ----
    print("\n=== 4) 找平进度（FIFO 分配结果）===")
    for a in db.query(PayrollAdjust).order_by(PayrollAdjust.source_month).all():
        pass
    for m in sorted({a.source_month for a in db.query(PayrollAdjust).all()}):
        rows = db.query(PayrollAdjust).filter_by(source_month=m).all()
        settled = sum(1 for r in rows if r.status == "settled")
        print(f"  {m}: {len(rows)} 笔 | 已结清 {settled} | "
              f"原始 {sum(r.adjust_amount or 0 for r in rows):,} | "
              f"已找平 {sum(r.settled_amount or 0 for r in rows):,} | "
              f"剩余 {sum(r.remaining or 0 for r in rows):,}")
    print(f"  关联行 {db.query(PayrollSettlementLink).count()} | "
          f"冲抵合计 {sum(l.amount or 0 for l in db.query(PayrollSettlementLink).all()):,}")

    # ---- 5) 自洽检查 ----
    print("\n=== 5) 自洽检查 ===")
    from app.services import integrity
    res = integrity.check_all(db)
    for c in res["checks"]:
        icon = {"pass": "✅", "fail": "❌", "info": "ℹ️ "}[c["status"]]
        print(f"  {icon} {c['id']} {c['name']}" + (f"（{c['count']}）" if c["count"] else ""))
        for smp in c["samples"][:3]:
            print(f"       · {smp}")
    print(f"\n{'✅ 自洽检查全过' if res['ok'] else '❌ 有不通过项，请检查'}")
    db.close()
    sys.exit(0 if res["ok"] else 1)


if __name__ == "__main__":
    main()
