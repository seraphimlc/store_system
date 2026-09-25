# -*- coding: utf-8 -*-
"""清空 8/9 月全部业务数据，用于「重新上传两个月文件、走完整流程」的演练。

保留：账号(users)、人员名录(persons)、店铺主档(store_entities/store_pairs)、
      迁移版本、api_tokens、审计日志。

同时处理两处会挡住重新上传的配置：
  1. `sealed_months` 的 2026-08 —— 不解除则上传 8 月文件会被封账闸门拒绝；
  2. `sys_configs` 的全局单值行 —— 它会**全局覆盖**按月的 env schedule
     （.env: BONUS_GROUP_SCHEDULE=2026-09=75 / BONUS_AMOUNT_SCHEDULE=2026-09=1250），
     导致 9 月工资按 68/3000 计算。清空后回退到按月 schedule：
     8 月 68/3000、9 月 75/1250（与历史数据口径一致）。

用法：./.venv/bin/python scripts/reset_p1_reupload.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.db as appdb
from app.models import (AdjustRecord, AppealRecord, DashMetric, FormalRecord,
                        ImportFile, MonthPerfRecord, PayrollPeriodRow,
                        PersonDailyStat, RawRecord, ReconDataRow, ReconDayRow,
                        ReconResult, ReconTask, SealedMonth, StaffAnalysis,
                        SysConfig)

MONTHS = ("2026-08", "2026-09")

db = appdb.SessionLocal()


def _count(model, *crit):
    return db.query(model).filter(*crit).count()


print("=== 清理前 ===")
print(" imports:", db.query(ImportFile).count(),
      "| raw:", db.query(RawRecord).count(),
      "| formal:", db.query(FormalRecord).count(),
      "| 月绩效:", db.query(MonthPerfRecord).count(),
      "| 找平表:", db.query(PayrollPeriodRow).count(),
      "| 人日:", db.query(PersonDailyStat).count(),
      "| 看板:", db.query(DashMetric).count())

n = 0
# 1) 对账（子表 → 主表）
for t in list(db.query(ReconTask).all()):
    for M in (ReconDayRow, ReconResult, ReconDataRow):
        n += db.query(M).filter(M.task_id == t.id).delete()
    n += db.query(ReconTask).filter(ReconTask.id == t.id).delete()
print(" 对账任务及明细:", n)

# 2) 找平记录 / 申诉 / 员工分析
print(" 找平记录:", db.query(AdjustRecord).delete())
print(" 申诉:", db.query(AppealRecord).delete())
print(" 员工分析:", db.query(StaffAnalysis).delete())

# 3) 月维度派生表
for M, label in ((PayrollPeriodRow, "薪资找平"),
                 (MonthPerfRecord, "月绩效"),
                 (DashMetric, "看板统计")):
    print(f" {label}:", db.query(M).filter(M.month.in_(MONTHS)).delete(
        synchronize_session=False))

# 4) 人×日统计（按日期段）
print(" 人×日统计:", db.query(PersonDailyStat).filter(
    PersonDailyStat.ref_date >= "2026-08-01",
    PersonDailyStat.ref_date < "2026-10-01").delete(synchronize_session=False))

# 5) 正式表 → 源记录 → 文件
print(" 正式表:", db.query(FormalRecord).filter(
    FormalRecord.japan_date >= "2026-08-01",
    FormalRecord.japan_date < "2026-10-01").delete(synchronize_session=False))
print(" 源记录:", db.query(RawRecord).delete())
print(" 导入文件:", db.query(ImportFile).delete())
db.commit()

# 6) 解除 8 月封账（重新上传需要）
print(" 解除封账:", db.query(SealedMonth).filter(
    SealedMonth.month.in_(MONTHS)).delete(synchronize_session=False))

# 7) 清空全局单值配置 → 回退到按月 env schedule
print(" 系统配置(回退按月 schedule):", db.query(SysConfig).delete())
db.commit()

print("\n=== 清理后 ===")
print(" imports:", db.query(ImportFile).count(),
      "| raw:", db.query(RawRecord).count(),
      "| formal:", db.query(FormalRecord).count(),
      "| 月绩效:", db.query(MonthPerfRecord).count(),
      "| 找平表:", db.query(PayrollPeriodRow).count(),
      "| 人日:", db.query(PersonDailyStat).count(),
      "| 看板:", db.query(DashMetric).count(),
      "| 封账:", db.query(SealedMonth).count(),
      "| 系统配置:", db.query(SysConfig).count())
print(" 保留：users=", end="")
from app.models import User, Person, StoreEntity
print(db.query(User).count(), "persons=", db.query(Person).count(),
      "store_entities=", db.query(StoreEntity).count())
db.close()
