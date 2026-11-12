# -*- coding: utf-8 -*-
"""只读核对（线上）：2026-10 绩效页/发薪表的「找平/应付」是否等于台账。

只调用纯读函数：carry_map / period_rows / pay_plan + 一条 SELECT 汇总。
不写任何表。（AGENTS.md 铁律：演示期不做生产写操作。）
"""
from sqlalchemy import func

from app.db import SessionLocal
from app.models import PayrollPayment
from app.services import period

MONTH = "2026-10"
CODE = "2188240645887825"          # 王俊岩

db = SessionLocal()
cm = period.carry_map(db, MONTH)
print("carry_map：人数 =", len(cm),
      " 找平合计 =", sum(v[1] for v in cm.values()))
print("王俊岩 找平 =", cm.get(CODE))

rows = period.period_rows(db, MONTH)
half_map = {r["code"]: (r["half1"], r["half1_amt"], r["half1_bonus"],
                        r["half2"], r["half2_amt"], r["half2_bonus"])
            for r in rows}
plan = period.pay_plan(db, MONTH, half_map)
gross = sum(h[1] for h in half_map.values())
carry = sum(v["h1"][0] for v in plan.values())
pay = sum(v["h1"][1] for v in plan.values())
print("绩效页(上半月)：毛额 =", gross, " 找平 =", carry, " 应付 =", pay)

h2carry = sum(v["h2"][0] for v in plan.values())
h2pay = sum(v["h2"][1] for v in plan.values())
print("绩效页(下半月)：找平 =", h2carry, " 应付 =", h2pay)

led = db.query(func.sum(func.coalesce(PayrollPayment.amount, 0)
                        + func.coalesce(PayrollPayment.adjust_applied, 0))) \
    .filter(PayrollPayment.month == MONTH, PayrollPayment.seq == 1).scalar()
print("台账：上半月实付合计 =", led)
print(">>> 一致 =", pay == led)

# 找平页那一列（period_rows 的「上月修正」）现在必须与绩效页同源
pr = sum(r["prev_amt"] for r in rows)
cm_onrows = sum(cm.get(r["code"], [0, 0])[1] for r in rows)
print("找平页「上月修正」合计 =", pr, " 绩效页在同样这些人身上 =", cm_onrows,
      " >>> 两页一致 =", pr == cm_onrows)
db.close()
