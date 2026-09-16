# -*- coding: utf-8 -*-
"""清理 7 月全部数据（保留 8 月、员工、店铺主档），用于让 8 月按新奖金规则重算。
   7月文件 = imports 中文件名含 7月/闫总/202607 的 + keep(0726-0731)。
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app.db as appdb
from app.models import (AdjustRecord, AppealRecord, FormalRecord,
                        ImportFile, MonthPerfRecord, PayrollPeriodRow,
                        PersonDailyStat, RawRecord, ReconDataRow,
                        ReconDayRow, ReconResult, ReconTask)

db = appdb.SessionLocal()
# 7月文件：闫总(#5) + keep(0726-0731 保留) 等 import_id 归属 7 月正式行的
JUL_IMP = [i.id for i in db.query(ImportFile).all()
           if i.file_name in ("7月_闫总1-31原始.xlsx",
                              "0726-0731(7月保留).xlsx") or
           i.file_name.startswith(("7月", "202607"))]
print("7月文件:", JUL_IMP)
# 对账：7月任务
for t in list(db.query(ReconTask).all()):
    if (t.params or {}).get("month") != "2026-07":
        continue
    for M in (ReconDayRow, ReconResult, ReconDataRow):
        db.query(M).filter(M.task_id == t.id).delete(synchronize_session=False)
    db.delete(t)
print("对账(7月任务) 已删")
print("找平:", db.query(AdjustRecord).filter(
    AdjustRecord.applied_to_month == "2026-07").update(
    {"applied_to_month": "2026-08"}, synchronize_session=False))  # 不影响
print("薪资找平7月:", db.query(PayrollPeriodRow).filter(
    PayrollPeriodRow.month == "2026-07").delete(synchronize_session=False))
print("月绩效7月:", db.query(MonthPerfRecord).filter(
    MonthPerfRecord.month == "2026-07").delete(synchronize_session=False))
print("统计7月:", db.query(PersonDailyStat).filter(
    PersonDailyStat.ref_date >= "2026-07-01",
    PersonDailyStat.ref_date < "2026-08-01").delete(synchronize_session=False))
print("正式表7月:", db.query(FormalRecord).filter(
    FormalRecord.japan_date >= "2026-07-01",
    FormalRecord.japan_date < "2026-08-01").delete(synchronize_session=False))
# 7月 raw + appeals（FK 顺序：appeals 先）
raw7 = [r.id for r in db.query(RawRecord.id).filter(
    RawRecord.import_id.in_(JUL_IMP)).all()]
print("申诉(7月文件):", db.query(AppealRecord).filter(
    AppealRecord.raw_record_id.in_(raw7)).delete(synchronize_session=False)
    if raw7 else 0)
print("原始行(7月文件):", db.query(RawRecord).filter(
    RawRecord.import_id.in_(JUL_IMP)).delete(synchronize_session=False))
for i in JUL_IMP:
    imp = db.get(ImportFile, i)
    if imp:
        db.delete(imp)
db.commit()
db.close()
print("7月数据已清理; imports =", [
    i.id for i in appdb.SessionLocal().query(ImportFile).all()])
