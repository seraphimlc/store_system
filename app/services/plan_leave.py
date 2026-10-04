# -*- coding: utf-8 -*-
"""假期模式 → 出勤计划：把休假期**自动标成"不出勤"（×）**，结束/替换时**精确撤销**。

用户 2026-10-03：
- 「可以标」（开假时顺便把出勤计划标成不出勤）
- 「员工的休假状态可以标识成日期区间……然后就可以在出勤计划里连续多天是叉」
- 「不要[单独的休假一览]。可以直接在出勤计划里体现，用叉来表示就行了」

设计（**由日期计划模块拥有这张表，作业域不直接写**）：
- `apply_leave(db, person_code, leave_id, start, end, today)`：逐日标 `available=False`、`source='leave'`、
  `leave_id=本假期`；**跳过** ① 已自报的日期（事实优先）② 员工自己标的 ×（`leave_id is None`
  且 `available=False`，别抢）③ 结束日之后的日期。
- `clear_leave(db, leave_db_id)`：把 `leave_id == 该假期` 的行**删掉**（恢复默认规则），
  只删 `reported=False` 的；`reported=True` 的只清 `leave_id`（保留事实）。
- 都不 commit（由调用方统一提交）；返回改动条数，方便页面/日志显示。
"""
from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models import StaffDatePlan


def apply_leave(db: Session, person_code: str, leave_id: int,
                start: date, end: Optional[date], today: date,
                max_days: int = 400) -> dict:
    """把休假期内的日期标成"不出勤"。**只动今天及以后、未自报、不是员工亲手标的那些行。**"""
    if not person_code or leave_id is None:
        return {"marked": 0, "skipped": 0}
    d0 = max(start, today)                       # 过去的日期不用标（过去看事实）
    d1 = end if end is not None else d0 + timedelta(days=max_days)
    rows = {r.plan_date: r for r in db.query(StaffDatePlan).filter(
        StaffDatePlan.person_code == person_code,
        StaffDatePlan.plan_date >= d0,
        StaffDatePlan.plan_date <= d1).all()}
    marked = skipped = 0
    d = d0
    span = 0
    while d <= d1 and span <= max_days:
        span += 1
        row = rows.get(d)
        if row is not None and (row.reported or
                                (not row.available and row.leave_id is None)):
            skipped += 1                      # 已自报 / 员工自己标的 × → 不动
        elif row is None:
            db.add(StaffDatePlan(person_code=person_code, plan_date=d,
                                 available=False, reported=False,
                                 source="leave", leave_id=leave_id))
            marked += 1
        elif row.available or row.leave_id == leave_id:
            row.available = False
            row.source = "leave"
            row.leave_id = leave_id
            marked += 1
        else:
            skipped += 1
        d += timedelta(days=1)
    db.flush()
    return {"marked": marked, "skipped": skipped,
            "until": (end.isoformat() if end else "未定")}


def clear_leave(db: Session, leave_id: int) -> int:
    """撤销某个假期自动标的"不出勤"（结束休假 / 假期被替换时调用）。"""
    if leave_id is None:
        return 0
    rows = (db.query(StaffDatePlan)
            .filter(StaffDatePlan.leave_id == leave_id).all())
    n = 0
    for r in rows:
        if r.reported:
            r.leave_id = None                 # 实际出勤过 → 只解绑，保留事实
        else:
            db.delete(r)                      # 自动标的那一行 → 恢复默认规则
        n += 1
    db.flush()
    return n


def leave_days(db: Session, person_code: str, start: date,
               end: Optional[date]) -> int:
    """休假期里被标成"不出勤"的天数（页面提示用）。"""
    q = (db.query(StaffDatePlan)
         .filter(StaffDatePlan.person_code == person_code,
                 StaffDatePlan.leave_id.isnot(None),
                 StaffDatePlan.plan_date >= start))
    if end is not None:
        q = q.filter(StaffDatePlan.plan_date <= end)
    return int(q.count())
