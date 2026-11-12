# -*- coding: utf-8 -*-
"""从 raw_records 重建 2026-08 正式表与统计（QA 探针误触旧代码清空后的恢复）。
   注意：必须用【修复后】代码运行（工作区已含听云修复）；离线执行，勿经旧服务。"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app.db as appdb
from app.models import FormalRecord, PersonDailyStat
from app.services import flow

db = appdb.SessionLocal()
print("恢复前 formal8:", db.query(FormalRecord).filter(
    FormalRecord.japan_date >= "2026-08-01",
    FormalRecord.japan_date < "2026-09-01").count())
res = flow.rebuild_month(db, "2026-08")
print("rebuild_month:", {k: res.get(k) for k in ("ok", "formal_before", "formal_after", "points_before", "points_after", "msg") if k in res})
db.commit()
f8 = db.query(FormalRecord).filter(
    FormalRecord.japan_date >= "2026-08-01",
    FormalRecord.japan_date < "2026-09-01").all()
print("恢复后 formal8:", len(f8), "点数:", sum(x.points or 0 for x in f8))
s8 = db.query(PersonDailyStat).filter(
    PersonDailyStat.ref_date >= "2026-08-01",
    PersonDailyStat.ref_date < "2026-09-01").count()
print("恢复后 stats8:", s8)
db.close()
