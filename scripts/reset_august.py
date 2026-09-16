# -*- coding: utf-8 -*-
"""清理 8 月全部数据（保留 7 月、员工、店铺主档、账号），用于全流程演练。

0726-0805 跨月文件特殊处理：7/26-7/31 的 raw/formal 迁移到新建的
「0726-0731(7月保留)」记录下保留（7月正式数据不变、无需7月重建），
8/1 起的部分删除；重走时该文件整体重新上传→7月部分判重去重、
8月部分正常 valid，与原判定一致（跨文件判重为数据驱动、顺序无关）。
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
AUG_FILES = ("0726-0805.xlsx", "0806-815.xlsx", "0816-25.xlsx",
             "0826-0831.xlsx", "Alipay8月结算数据.xlsx")
imp_ids = [i.id for i in db.query(ImportFile).filter(
    ImportFile.file_name.in_(AUG_FILES)).all()]
cross = db.query(ImportFile).filter(
    ImportFile.file_name == "0726-0805.xlsx").first()
cross_id = cross.id if cross else None
print("将清理 imports:", imp_ids, "| 跨月文件:", cross_id)

# 对账：8月任务及其子表 + 相关找平
n = 0
for t in list(db.query(ReconTask).all()):
    if (t.params or {}).get("month") != "2026-08":
        continue
    for M in (ReconDayRow, ReconResult, ReconDataRow):
        n += db.query(M).filter(M.task_id == t.id).delete()
    n += db.query(ReconTask).filter(ReconTask.id == t.id).delete()
print("对账(8月任务+明细):", n)
print("找平记录:", db.query(AdjustRecord).delete())

for M, label in ((PayrollPeriodRow, "薪资找平8月"),
                 (MonthPerfRecord, "月绩效8月")):
    print(label, db.query(M).filter(M.month == "2026-08").delete())
print("人×日统计8月:", db.query(PersonDailyStat).filter(
    PersonDailyStat.ref_date >= "2026-08-01",
    PersonDailyStat.ref_date < "2026-09-01").delete())
print("正式表8月+(含9/1):", db.query(FormalRecord).filter(
    FormalRecord.japan_date >= "2026-08-01",
    FormalRecord.japan_date < "2026-10-01").delete())

# 跨月文件：7月部分迁移保留
if cross_id is not None:
    keep = ImportFile(file_name="0726-0731(7月保留).xlsx",
                      file_sha256=f"kept-{cross_id}", file_size=0,
                      stored_path="kept", status="parsed",
                      uploaded_by=cross.uploaded_by)
    db.add(keep)
    db.commit()
    from sqlalchemy import text
    db.execute(text(
        "UPDATE raw_records SET import_id=:k WHERE import_id=:c AND "
        "substr(modified_raw,1,10) < '2026-08-01'"), {"k": keep.id, "c": cross_id})
    db.execute(text(
        "UPDATE formal_records SET import_id=:k WHERE import_id=:c AND "
        "japan_date < '2026-08-01' AND japan_date >= '2026-07-01'"),
        {"k": keep.id, "c": cross_id})
    n1 = db.query(RawRecord).filter(
        RawRecord.import_id == cross_id,
        RawRecord.modified_raw < "2026-08-01").count()
    print(f"跨月文件 7月部分迁移 raw/formal → keep#{keep.id} (raw约{n1})")

# 申诉先删（FK raw），再删 raw
raw_ids = [r.id for r in db.query(RawRecord.id).filter(
    RawRecord.import_id.in_(imp_ids)).all()]
n_app = db.query(AppealRecord).filter(
    AppealRecord.raw_record_id.in_(raw_ids)).delete(synchronize_session=False) \
    if raw_ids else 0
print("申诉(8月文件):", n_app)
print("原始行(8月文件):", db.query(RawRecord).filter(
    RawRecord.import_id.in_(imp_ids)).delete(synchronize_session=False))
# 员工首次出现文件引用指向被删文件 → 置空（FK persons.first_seen_import_id）
from sqlalchemy import text as _t
db.execute(_t("UPDATE persons SET first_seen_import_id=NULL "
              "WHERE first_seen_import_id IN (SELECT id FROM imports "
              "WHERE id IN :ids)"), {"ids": tuple(imp_ids)})
for i in imp_ids:
    imp = db.get(ImportFile, i)
    if imp:
        db.delete(imp)
db.commit()
print("imports 8月文件已删除:", imp_ids)
db.close()
