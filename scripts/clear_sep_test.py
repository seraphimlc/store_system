# -*- coding: utf-8 -*-
"""清理本地 9 月测试数据（恢复 8 月封账演示状态）。

删除：scripts/2026-09_*.xlsx（测试文件）、9 月 imports/raw/formal/统计/月绩效/
薪资找平、9 月对账任务与明细。8 月数据不动。
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app.db as appdb
from app.models import (FormalRecord, ImportFile, MonthPerfRecord,
                        PayrollPeriodRow, PersonDailyStat, RawRecord,
                        ReconDataRow, ReconResult, ReconTask)

# 1) 删测试文件
import glob
for p in glob.glob(os.path.join(os.path.dirname(__file__), "2026-09_*.xlsx")):
    os.remove(p)
    print("删除测试文件:", os.path.basename(p))

db = appdb.SessionLocal()
# 2) 9 月对账任务与明细
t9 = [t.id for t in db.query(ReconTask).all()
      if (t.params or {}).get("month") == "2026-09"]
for tid in t9:
    db.query(ReconDataRow).filter(ReconDataRow.task_id == tid).delete(
        synchronize_session=False)
    db.query(ReconResult).filter(ReconResult.task_id == tid).delete(
        synchronize_session=False)
    db.query(ReconTask).filter(ReconTask.id == tid).delete(
        synchronize_session=False)
print("9月对账任务清理:", t9)

# 3) 9 月正式/统计/月绩效/找平
db.query(FormalRecord).filter(
    FormalRecord.japan_date >= "2026-09-01",
    FormalRecord.japan_date < "2026-10-01").delete(synchronize_session=False)
db.query(PersonDailyStat).filter(
    PersonDailyStat.ref_date >= "2026-09-01",
    PersonDailyStat.ref_date < "2026-10-01").delete(synchronize_session=False)
db.query(MonthPerfRecord).filter(
    MonthPerfRecord.month == "2026-09").delete(synchronize_session=False)
db.query(PayrollPeriodRow).filter(
    PayrollPeriodRow.month == "2026-09").delete(synchronize_session=False)

# 4) 9 月 imports + raw（测试上传的文件 #11/#12 等）
sep_imp = [i.id for i in db.query(ImportFile).all()
           if i.file_name.startswith("2026-09_")]
print("9月测试 imports:", sep_imp)
if sep_imp:
    n = db.query(RawRecord).filter(
        RawRecord.import_id.in_(sep_imp)).delete(synchronize_session=False)
    print("删除 9 月 raw:", n)
    for i in sep_imp:
        imp = db.get(ImportFile, i)
        if imp:
            db.delete(imp)
db.commit()
db.close()
print("=== 9 月测试数据清理完成，8 月封账数据保留 ===")
