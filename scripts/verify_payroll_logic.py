# -*- coding: utf-8 -*-
"""数据自洽检查（命令行入口）——逻辑在 app/services/integrity.py（系统内置，
MCP 工具 visit_verify_integrity 与它同源）。

用法（cwd=项目根）：
    DATABASE_URL="sqlite:///./store_settle_live.db" ./.venv/bin/python scripts/verify_payroll_logic.py
退出码：0=全部通过（info 不算失败）；1=有不通过项
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.db as appdb
from app.models import FormalRecord, MonthPerfRecord
from app.services import integrity

db = appdb.SessionLocal()
fails = []


def check(name, bad, extra=""):
    ok = not bad
    print(f"  {'✅' if ok else '❌'} {name}{(' — ' + extra) if extra else ''}")
    if bad:
        fails.append(name)
        for b in bad[:5]:
            print(f"       · {b}")


res = integrity.check_all(db)
print("=== 数据概览 ===")
for m in res["summary"]["months_with_data"]:
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
sc = res["summary"]["scale"]
print(f"  找平表 {sc['adjust_rows']} 行 | 台账 {sc['payment_rows']} 行 | "
      f"关联 {sc['link_rows']} 行 | 找平行 {sc['findiff_rows']} 行")
print("=== 检查项 ===")
icon = {"pass": "✅", "fail": "❌", "info": "ℹ️ "}
for c in res["checks"]:
    print(f"  {icon[c['status']]} {c['id']} {c['name']}")
    for smp in c["samples"]:
        print(f"       · {smp}")
print()
if res["ok"]:
    print(f"✅ 通过 {res['summary']['passed']} 项"
          + (f"（另有 {res['summary']['info']} 项提示）" if res["summary"]["info"] else "")
          + "：4 张表数字互相印证，抵扣均有来源可反查")
else:
    print(f"❌ 未通过 {res['summary']['failed']} 项")
db.close()
sys.exit(0 if res["ok"] else 1)
