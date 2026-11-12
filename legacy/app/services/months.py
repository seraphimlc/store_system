# -*- coding: utf-8 -*-
"""**有哪些月份** —— 各表的月份清单，一律走 `SELECT DISTINCT`。

**为什么单独一个模块**（2026-10-17）：这几个"算月份"的地方原来全写的是

    sorted({(str(r.japan_date or ""))[:7] for r in db.query(FormalRecord).all()})

—— 为了拿到 3 个月份，把 **7 万行整表**拉进 Python 再水合成 ORM 对象。
线上实测：`/perf` 一页 2,300ms 里 **1,766ms 全花在这一句**（真正的业务逻辑只 24ms）；
`/perf/export` 同样白花 1.7 秒。口径完全一致（都是"值非空则取前 7 位"），
只是从"拉全表"改成"数据库算 DISTINCT"。

⚠️ 日期列（`japan_date` / `ref_date`）是 `Date` 类型，**必须显式 CAST**：
PG 不会把 date 隐式转 text（`substr(date, …)` 在 PG 上直接 "no function matches"），
SQLite 因为动态类型"看着能用" —— 典型的**单测全绿、线上 500**。同理**不能**拿
date 列跟 `''` 比（PG 会说 '' 不是合法日期）。前缀比较（`>= "2026-07"`）与 Python 里
`(str(date))[:7] >= "2026-07"` 一致 —— 别改成 `LIKE`（`_` 是通配符）。
"""
from __future__ import annotations

from typing import List, Optional

from sqlalchemy import String, cast, func
from sqlalchemy.orm import Session

from app.models import (FormalRecord, MonthPerfRecord, PayrollPeriodRow,
                        PersonDailyStat)


def _clean(values) -> List[str]:
    return sorted({v for v in values if v})


def _ym(col):
    """日期列 → "YYYY-MM" 前缀（**显式 CAST**，见模块开头 ⚠️）。"""
    return func.substr(cast(col, String(10)), 1, 7)


def formal_months(db: Session) -> List[str]:
    """`formal_records.japan_date` 里出现过的 "YYYY-MM"（升序）。"""
    rows = (db.query(_ym(FormalRecord.japan_date))
            .filter(FormalRecord.japan_date.isnot(None))
            .distinct().all())
    return _clean(r[0] for r in rows)


def perf_months(db: Session, since: Optional[str] = None) -> List[str]:
    """`month_perf_records.month` 里出现过的月份（升序；可只取 `since` 之后）。"""
    q = db.query(MonthPerfRecord.month).filter(MonthPerfRecord.month.isnot(None))
    if since:
        q = q.filter(MonthPerfRecord.month >= since)
    return _clean(r[0] for r in q.distinct().all())


def payroll_months(db: Session, since: Optional[str] = None) -> List[str]:
    """`payroll_period_rows.month`（升序）。"""
    q = db.query(PayrollPeriodRow.month).filter(PayrollPeriodRow.month.isnot(None))
    if since:
        q = q.filter(PayrollPeriodRow.month >= since)
    return _clean(r[0] for r in q.distinct().all())


def stat_months(db: Session, since: Optional[str] = None) -> List[str]:
    """`person_daily_stats.ref_date` 的 "YYYY-MM"（升序）。"""
    q = (db.query(_ym(PersonDailyStat.ref_date))
         .filter(PersonDailyStat.ref_date.isnot(None)))
    if since:
        q = q.filter(_ym(PersonDailyStat.ref_date) >= since)
    return _clean(r[0] for r in q.distinct().all())


def settle_months(db: Session, since: Optional[str] = None) -> List[str]:
    """发薪结算页要的月份 = 日统计 ∪ 发薪行（两边都只看 `since` 之后）。"""
    return sorted(set(stat_months(db, since)) | set(payroll_months(db, since)))
