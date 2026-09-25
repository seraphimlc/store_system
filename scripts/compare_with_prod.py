# -*- coding: utf-8 -*-
"""本地 vs 线上 逐人对比（**线上只读**，不写任何东西）。

用途：本地用新逻辑重跑后，与线上（旧系统）数据逐人比对，确认逻辑一致。

线上取数（**只读**，在能 ssh store-prod 的机器上执行，输出 JSON）：
    ssh store-prod "docker exec deploy-db-1 psql -U store_settle -d store_settle -At -F'|' -c \"
      SELECT month, person_code, points, salary FROM month_perf_records
      WHERE month IN ('2026-08','2026-09') ORDER BY month, person_code;\"" \
      > /tmp/prod_perf.txt

    ssh store-prod "docker exec deploy-db-1 psql -U store_settle -d store_settle -At -F'|' -c \"
      SELECT month, person_code, diff_amount, prev_adjust_amount FROM payroll_period_rows
      WHERE month IN ('2026-08','2026-09') ORDER BY month, person_code;\"" \
      > /tmp/prod_period.txt

    ssh store-prod "docker exec deploy-db-1 psql -U store_settle -d store_settle -At -F'|' -c \"
      SELECT substr(japan_date::text,1,7) AS m, COUNT(*), SUM(points) FROM formal_records
      GROUP BY 1 ORDER BY 1;\"" > /tmp/prod_formal.txt

本地跑（cwd=项目根）：
    DATABASE_URL=\"sqlite:///./store_settle_live.db\" ./.venv/bin/python \\
        scripts/compare_with_prod.py /tmp/prod_perf.txt /tmp/prod_period.txt /tmp/prod_formal.txt
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.db as appdb
from app.models import FormalRecord, MonthPerfRecord, PayrollPeriodRow

if len(sys.argv) < 4:
    print(__doc__)
    sys.exit(2)

perf_f, period_f, formal_f = sys.argv[1:4]
db = appdb.SessionLocal()


def load(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(line.split("|"))
    return rows


print("=== 1) 月度总量对比（正式表）===")
prod_formal = {r[0]: (int(r[1]), int(r[2])) for r in load(formal_f)}
local_formal = {}
for m in sorted({str(r.japan_date)[:7] for r in db.query(FormalRecord).all()
                 if r.japan_date}):
    rows = db.query(FormalRecord).filter(
        FormalRecord.japan_date >= f"{m}-01",
        FormalRecord.japan_date < f"{m}-32").all()
    local_formal[m] = (len(rows), sum(r.points or 0 for r in rows))
for m in sorted(set(prod_formal) | set(local_formal)):
    p = prod_formal.get(m, ("-", "-"))
    l = local_formal.get(m, ("-", "-"))
    flag = "✅" if p == l else "⚠️"
    print(f"  {flag} {m}: 线上 {p[0]} 行/{p[1]} 点 | 本地 {l[0]} 行/{l[1]} 点")

print("=== 2) 逐人薪资对比（month_perf_records）===")
prod_perf = {(r[0], r[1]): (int(r[2] or 0), int(r[3] or 0)) for r in load(perf_f)}
local_perf = {(r.month, r.person_code): (r.points or 0, r.salary or 0)
              for r in db.query(MonthPerfRecord).all()}
diff = 0
for k in sorted(set(prod_perf) | set(local_perf)):
    p, l = prod_perf.get(k), local_perf.get(k)
    if p != l:
        diff += 1
        if diff <= 10:
            print(f"  ⚠️ {k[0]} {k[1]}: 线上 {p} vs 本地 {l}")
print(f"  {'✅ 逐人完全一致' if diff == 0 else f'⚠️ 共 {diff} 人存在差异'}"
      f"（线上 {len(prod_perf)} 人 / 本地 {len(local_perf)} 人）")

print("=== 3) 找平（差异/结转）对比（payroll_period_rows）===")
prod_pr = {(r[0], r[1]): (int(r[2] or 0), int(r[3] or 0)) for r in load(period_f)}
local_pr = {(r.month, r.person_code): (r.diff_amount or 0, r.prev_adjust_amount or 0)
            for r in db.query(PayrollPeriodRow).all()}
diff2 = 0
for k in sorted(set(prod_pr) | set(local_pr)):
    p, l = prod_pr.get(k), local_pr.get(k)
    if p != l:
        diff2 += 1
        if diff2 <= 10:
            print(f"  ⚠️ {k[0]} {k[1]}: 线上 diff/结转 {p} vs 本地 {l}")
print(f"  {'✅ 完全一致' if diff2 == 0 else f'⚠️ 共 {diff2} 人存在差异'}")
print()
print("说明：线上无「台账/找平进度/关联」三张表（新功能），这三张表用"
      "scripts/verify_payroll_logic.py 做内部自洽检查。")
db.close()
