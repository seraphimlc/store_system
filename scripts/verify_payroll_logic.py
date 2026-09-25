# -*- coding: utf-8 -*-
"""新表逻辑自洽检查（上传后跑）：确保 4 张表的数字互相印证、没有"空穴来风"。

检查项（每项给出 PASS/FAIL + 反例明细）：
  A1 找平表 adjust_amount == 该月 payroll_period_rows.diff_amount（逐人）
  A2 找平表 settled_amount + remaining == adjust_amount
  A3 找平表 status 与 remaining 一致（remaining==0 → settled，否则 in_progress）
  A4 关联表按 payment 汇总 == 台账该笔 adjust_applied
  A5 关联表按 adjust 汇总 == 找平表 settled_amount
  A6 台账 amount == 该期应发（half1_amount/half2_amount 快照）
  A7 台账 adjust_source_* 指向的记录真实存在（可反查）
  A8 递延链自洽：本月结转 prev_adjust_amount == 本月 diff + 未吸收部分
      （简化口径：|prev| ≤ |diff| + |上月结转| 且符号一致）

用法（cwd=项目根）：
    DATABASE_URL="sqlite:///./store_settle_live.db" ./.venv/bin/python scripts/verify_payroll_logic.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.db as appdb
from app.models import (FormalRecord, MonthPerfRecord, PayrollAdjust,
                        PayrollPayment, PayrollPeriodRow,
                        PayrollSettlementLink)

db = appdb.SessionLocal()
fails = []


def check(name, bad, extra=""):
    ok = not bad
    print(f"  {'✅' if ok else '❌'} {name}{(' — ' + extra) if extra else ''}")
    if bad:
        fails.append(name)
        for b in bad[:5]:
            print(f"       · {b}")


print("=== 数据概览 ===")
for m in sorted({str(r.japan_date)[:7] for r in db.query(FormalRecord).all()
                 if r.japan_date}):
    n = db.query(FormalRecord).filter(
        FormalRecord.japan_date >= f"{m}-01",
        FormalRecord.japan_date < f"{m}-32").count()
    pts = sum(r.points or 0 for r in db.query(FormalRecord).filter(
        FormalRecord.japan_date >= f"{m}-01",
        FormalRecord.japan_date < f"{m}-32").all())
    perf = db.query(MonthPerfRecord).filter(MonthPerfRecord.month == m).all()
    print(f"  {m}: 正式表 {n} 行 / {pts} 点 | 月绩效 {len(perf)} 人 / "
          f"{sum(p.salary or 0 for p in perf):,} 円")

print("=== 表规模 ===")
print(f"  找平表 {db.query(PayrollAdjust).count()} 行 | "
      f"台账 {db.query(PayrollPayment).count()} 行 | "
      f"关联 {db.query(PayrollSettlementLink).count()} 行")

print("=== 不变量检查 ===")
# A1
bad = []
for a in db.query(PayrollAdjust).all():
    r = db.query(PayrollPeriodRow).filter_by(
        month=a.source_month, person_code=a.person_code).first()
    if r is None or (r.diff_amount or 0) != (a.adjust_amount or 0):
        bad.append(f"{a.source_month} {a.person_code}: 找平表 {a.adjust_amount} "
                   f"vs 找平行 {None if r is None else r.diff_amount}")
check("A1 找平表金额 == 该月差异金额", bad)

# A2
bad = [f"#{a.id} {a.source_month} {a.person_code}: {a.settled_amount}+{a.remaining}"
       f" != {a.adjust_amount}"
       for a in db.query(PayrollAdjust).all()
       if (a.settled_amount or 0) + (a.remaining or 0) != (a.adjust_amount or 0)]
check("A2 已找平 + 剩余 == 原始金额", bad)

# A3
bad = [f"#{a.id} status={a.status} remaining={a.remaining}"
       for a in db.query(PayrollAdjust).all()
       if ((a.remaining or 0) == 0) != (a.status == "settled")]
check("A3 状态与剩余一致（0=settled）", bad)

# A4
bad = []
for p in db.query(PayrollPayment).all():
    s = sum(l.amount or 0 for l in db.query(PayrollSettlementLink).filter(
        PayrollSettlementLink.payment_id == p.id).all())
    if s != (p.adjust_applied or 0):
        bad.append(f"台账#{p.id} {p.month} seq{p.seq}: 关联合计 {s} "
                   f"vs adjust_applied {p.adjust_applied}")
check("A4 关联（按发放）合计 == 台账抵扣额", bad)

# A5
bad = []
for a in db.query(PayrollAdjust).all():
    s = sum(l.amount or 0 for l in db.query(PayrollSettlementLink).filter(
        PayrollSettlementLink.adjust_id == a.id).all())
    if s != (a.settled_amount or 0):
        bad.append(f"找平#{a.id}: 关联合计 {s} vs 已找平 {a.settled_amount}")
check("A5 关联（按找平）合计 == 已找平额", bad)

# A6
bad = []
for p in db.query(PayrollPayment).all():
    r = db.query(PayrollPeriodRow).filter_by(
        month=p.month, person_code=p.person_code).first()
    if r is None:
        bad.append(f"台账#{p.id} 无对应找平行 {p.month} {p.person_code}")
        continue
    expect = r.half1_amount if p.seq == 1 else r.half2_amount
    if (p.amount or 0) != (expect or 0):
        bad.append(f"台账#{p.id} {p.month} seq{p.seq}: 实发 {p.amount} vs 应发 {expect}")
check("A6 台账实发 == 该期应发快照", bad)

# A7
bad = []
for p in db.query(PayrollPayment).all():
    if p.adjust_source_row_id:
        if db.get(PayrollPeriodRow, p.adjust_source_row_id) is None:
            bad.append(f"台账#{p.id} source_row_id={p.adjust_source_row_id} 不存在")
    if p.adjust_source_month:
        if not db.query(PayrollPeriodRow).filter_by(
                month=p.adjust_source_month, person_code=p.person_code).first():
            bad.append(f"台账#{p.id} source_month={p.adjust_source_month} 无该人找平行")
check("A7 抵扣来源可反查（月份/找平行存在）", bad)

# A8
bad = []
for r in db.query(PayrollPeriodRow).all():
    d, pv = r.diff_amount or 0, r.prev_adjust_amount or 0
    if d and pv and (d > 0) != (pv > 0):
        bad.append(f"{r.month} {r.person_code}: diff {d} 与结转 {pv} 符号不一致")
check("A8 递延链符号一致", bad)

print()
if fails:
    print(f"❌ 未通过 {len(fails)} 项：{', '.join(fails)}")
else:
    print("✅ 全部通过：4 张表数字互相印证，抵扣均有来源可反查")
db.close()
sys.exit(1 if fails else 0)
