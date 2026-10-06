# -*- coding: utf-8 -*-
"""员工「假期模式」（休假期）+ 派工可用性提醒（用户 2026-10-03 要求）。

用户原话（要点）：
- 「派工的时候，要看出勤计划。如果员工该日不出勤或者请假，在派工的时候要有提醒」
- 「员工端再加一个功能，假期模式。也就是休假状态。如果员工在休假状态，派工时要
  有提醒，但**不强制约束**」

设计：
- 休假期落 `bd_staff_leave`（作业域新表），**一人同时只有一条 active**；
  员工自己开/结束，管理员可代改。
- **可用性检查是唯一入口** `availability()/availability_map()`：把三处信号合成一个
  `level`（`ok` / `warn` / `block`）+ 原因列表，页面与写端点共用一份：
  | 来源 | 级别 | 说明 |
  |---|---|---|
  | `users.status` = 离职/停用 | **block** | 账号都进不来，执行不了（沿用 2026-10-03 硬拒绝） |
  | `bd_staff_leave` 覆盖该日 | warn | 休假中（至 X） |
  | `users.status` = 请假 | warn | 员工状态请假 |
  | `staff_date_plans` 该日不出勤 | warn | 出勤计划说这天不来 |
- ⚠️ **跨域只读**：本模块**只读** `staff_date_plans`（出勤计划）与 `users`，
  **绝不写**它们中的任何一张（写只写 `bd_staff_leave`）。
"""
from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence

from sqlalchemy.orm import Session

from app.models import BdStaffLeave, StaffDatePlan, User

BLOCK_STATUS = ("resigned", "disabled")
LEAVE_STATUS = ("active", "ended")


class LeaveError(Exception):
    """假期模式的操作错误（页面直接显示）。"""


def today() -> date:
    from app.services import date_plan
    return date_plan.jst_today()


# ---------------- 假期模式（写） ----------------

def active_leave(db: Session, person_code: str) -> Optional[BdStaffLeave]:
    """该人当前**未结束**的休假期（一人最多一条）。"""
    if not person_code:
        return None
    return (db.query(BdStaffLeave)
            .filter(BdStaffLeave.person_code == person_code,
                    BdStaffLeave.status == "active")
            .order_by(BdStaffLeave.start_date.desc(),
                      BdStaffLeave.id.desc()).first())


def start_leave(db: Session, person_code: str, start_date: date,
                end_date: Optional[date] = None, reason: str = "",
                by: str = "", actor_user=None) -> BdStaffLeave:
    """开启假期模式（新开一条 → 原来的 active 自动结束）。"""
    from app.services import bd_log
    code = (person_code or "").strip()
    if not code:
        raise LeaveError("没有人员编号，无法开启假期模式")
    if end_date is not None and end_date < start_date:
        raise LeaveError("结束日不能早于开始日")
    if actor_user is not None:
        by = getattr(actor_user, "username", "") or by
    from app.services import plan_leave
    old = active_leave(db, code)
    if old is not None:
        old.status = "ended"
        if old.end_date is None or old.end_date > start_date - timedelta(days=1):
            old.end_date = start_date - timedelta(days=1)
        old.updated_at = _now()
        plan_leave.clear_leave(db, old.id)      # 旧假期标的 × 先撤掉
    row = BdStaffLeave(person_code=code, start_date=start_date,
                       end_date=end_date, reason=(reason or "").strip(),
                       status="active", created_by=(by or ""))
    db.add(row)
    db.flush()
    # 休假期 → 出勤计划**自动标成"不出勤"（×）**（用户 2026-10-03"可以标"）
    st = plan_leave.apply_leave(db, code, row.id, start_date, end_date,
                                today())
    note = (reason or "")
    if st["marked"]:
        note = "%s（已标出勤计划 %d 天不出勤）" % (note, st["marked"])
    bd_log.log_op(db, actor_user, "member", "status", ref_id=row.id,
                  ref_label=code, field="假期模式",
                  old=("休假中" if old is not None else "正常"),
                  new="休假 %s ~ %s" % (start_date.isoformat(),
                                       end_date.isoformat() if end_date
                                       else "未定"),
                  note=note)
    return row

def end_leave(db: Session, person_code: str, on_date: Optional[date] = None,
              by: str = "", actor_user=None) -> Optional[BdStaffLeave]:
    """结束假期模式（**从 `on_date` 当天起就不再是休假**）。"""
    from app.services import bd_log
    code = (person_code or "").strip()
    if not code:
        return None
    if actor_user is not None:
        by = getattr(actor_user, "username", "") or by
    row = active_leave(db, code)
    if row is None:
        return None
    from app.services import plan_leave
    d = on_date or today()
    row.status = "ended"
    row.end_date = max(row.start_date, d - timedelta(days=1))
    row.updated_at = _now()
    db.flush()
    plan_leave.clear_leave(db, row.id)          # 撤销休假自动标的 ×（恢复默认）
    bd_log.log_op(db, actor_user, "member", "status", ref_id=row.id,
                  ref_label=code, field="假期模式", old="休假中", new="已结束",
                  note="员工自己/管理员结束")
    return row


def _now():
    from datetime import datetime
    return datetime.utcnow()


# ---------------- 可用性（读；页面与写端点共用） ----------------

def leave_covers(row: Optional[BdStaffLeave], d: date) -> bool:
    if row is None or row.status != "active":
        return False
    if row.start_date > d:
        return False
    return row.end_date is None or row.end_date >= d


def current(db: Session, person_code: str,
            on_date: Optional[date] = None) -> Optional[BdStaffLeave]:
    """该人在某日是否休假 → 返回那条休假期。"""
    d = on_date or today()
    return db.query(BdStaffLeave).filter(
        BdStaffLeave.person_code == person_code,
        BdStaffLeave.status == "active",
        BdStaffLeave.start_date <= d,
    ).filter((BdStaffLeave.end_date.is_(None))
             | (BdStaffLeave.end_date >= d)).first()


def is_on_leave(db: Session, person_code: str,
                on_date: Optional[date] = None) -> bool:
    return current(db, person_code, on_date) is not None


def availability_map(db: Session, person_codes: Sequence[str],
                     on_date: Optional[date] = None) -> Dict[str, dict]:
    """批量可用性（**一次 3 个查询，避免 N+1**）→ `{person_code: {...}}`。

    ⚠️ 只读 `users` / `staff_date_plans` / `bd_staff_leave`，**不写任何表**。
    """
    d = on_date or today()
    codes = [c for c in dict.fromkeys(person_codes or []) if c]
    out: Dict[str, dict] = {c: {"level": "ok", "reasons": [], "tags": [],
                                "leave": None, "status": ""} for c in codes}
    if not codes:
        return out
    for code, status in (db.query(User.person_code, User.status)
                         .filter(User.person_code.in_(codes)).all()):
        if code in out:
            out[code]["status"] = status or "active"
    for code, avail in (db.query(StaffDatePlan.person_code,
                                 StaffDatePlan.available)
                        .filter(StaffDatePlan.person_code.in_(codes),
                                StaffDatePlan.plan_date == d).all()):
        if code in out and not avail:
            out[code]["reasons"].append("出勤计划：该日不出勤")
            out[code]["tags"].append("计划休")
    for row in (db.query(BdStaffLeave)
                .filter(BdStaffLeave.person_code.in_(codes),
                        BdStaffLeave.status == "active",
                        BdStaffLeave.start_date <= d)
                .filter((BdStaffLeave.end_date.is_(None))
                        | (BdStaffLeave.end_date >= d)).all()):
        if row.person_code in out:
            out[row.person_code]["leave"] = row
            until = row.end_date.isoformat() if row.end_date else "未定"
            out[row.person_code]["reasons"].append("休假中（至 %s）" % until)
            out[row.person_code]["tags"].append("休假")
    for code, a in out.items():
        if a["status"] in BLOCK_STATUS:
            a["level"] = "block"
            a["reasons"].insert(
                0, "已%s（不能派工）" % ("离职" if a["status"] == "resigned"
                                        else "停用"))
            a["tags"].insert(0, "离职" if a["status"] == "resigned" else "停用")
        elif a["status"] == "leave":
            a["level"] = "warn"
            a["reasons"].insert(0, "员工状态：请假")
            a["tags"].insert(0, "请假")
        elif a["reasons"]:
            a["level"] = "warn"
    return out


def availability(db: Session, person_code: str,
                 on_date: Optional[date] = None) -> dict:
    """单人可用性（页面用）。"""
    return availability_map(db, [person_code], on_date).get(
        person_code, {"level": "ok", "reasons": [], "tags": [],
                      "leave": None, "status": ""})


def warn_text(avail: dict, name: str = "") -> str:
    """提醒文案（派工后拼消息用）。"""
    reasons = (avail or {}).get("reasons") or []
    if not reasons:
        return ""
    return "%s%s" % (("%s：" % name) if name else "", "；".join(reasons))


def active_map(db: Session, on_date: Optional[date] = None,
               person_codes: Optional[Sequence[str]] = None
               ) -> Dict[str, BdStaffLeave]:
    """**当日休假中**的人 → `{person_code: 休假期行}`（批量，管理端列表用）。"""
    d = on_date or today()
    q = (db.query(BdStaffLeave)
         .filter(BdStaffLeave.status == "active",
                 BdStaffLeave.start_date <= d)
         .filter((BdStaffLeave.end_date.is_(None))
                 | (BdStaffLeave.end_date >= d)))
    if person_codes:
        codes = [c for c in dict.fromkeys(person_codes) if c]
        if not codes:
            return {}
        q = q.filter(BdStaffLeave.person_code.in_(codes))
    return {r.person_code: r for r in q.all()}


def label_of(row: Optional[BdStaffLeave]) -> str:
    """「休假中（至 X）」标签（页面用）。"""
    if row is None:
        return ""
    until = row.end_date.isoformat() if row.end_date else "未定"
    return "休假中（至 %s）" % until
