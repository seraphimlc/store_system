"""Read-only legacy payroll oracle for the isolated P4 history clone.
Emits comparison data to the calling verifier, never logs identities or credentials.
"""
import json
import os
from sqlalchemy import text
from app.db import SessionLocal
from app.services import period

if '/store_settle_refactor_p4_history_' not in os.environ.get('DATABASE_URL', ''):
    raise RuntimeError('Isolated P4 history database required')
db = SessionLocal()
try:
    db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
    months = [row[0] for row in db.execute(text('SELECT month FROM month_perf_records UNION SELECT month FROM payroll_period_rows ORDER BY month'))]
    out = {}
    for month in months:
        rows = period.period_rows(db, month)
        half_map = {r['code']: (r['half1'], r['half1_amt'], r['half1_bonus'], r['half2'], r['half2_amt'], r['half2_bonus']) for r in rows}
        plan = period.pay_plan(db, month, half_map)
        out[month] = {'rows': [{'code': r['code'], 'half1': list(plan[r['code']]['h1']), 'half2': list(plan[r['code']]['h2']), 'gross1': r['half1_amt'], 'gross2': r['half2_amt'], 'points1': r['half1'], 'points2': r['half2'], 'settle': r['settle'], 'settleAmount': r['settle_amt'], 'diff': r['diff'], 'diffAmount': r['diff_amt']} for r in rows], 'carry': period.carry_map(db, month)}
    print(json.dumps(out, ensure_ascii=True))
finally:
    db.rollback()
    db.close()
