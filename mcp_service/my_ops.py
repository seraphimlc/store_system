# -*- coding: utf-8 -*-
"""“我的”系列只读工具（规格：docs/specs-mcp-identity.md 第二节）。

- visit_my_perf(month=None)     我的月绩效（点数/1点/2点/工资；month 省略=最新有数据的月）
- visit_my_daily(month)         我的日明细（person_daily_stats 逐日：日期/点数/店数）
- visit_my_settlement()         我的找平状态与发放（payroll_adjusts 未结清/已结清 +
                                 payroll_payments 各期实发与抵扣 +
                                 payroll_settlement_links 双向明细），含 remaining 合计

安全设计：
- **无 person 参数**（不给越权留入口）；服务端强制按 actor.person_code 过滤，
  显式传他人 person → 在授权层（authz.enforce）直接 FORBIDDEN_TOOL。
- staff 与 admin 均可调用（admin 按 admin 自己绑定的 person_code；
  若 admin 未绑定人员 → BAD_PARAM + hint「管理员账号未绑定人员，请用公司级工具」）。
- 只读：本模块不得出现 db.commit()/任何写语句（审计行由统一入口负责）。
"""
from datetime import date
from typing import Annotated, Any

from pydantic import Field
from sqlalchemy import func

from mcp.server.mcpserver import Context, MCPServer

from mcp_service.capability import MONTH_PATTERN
from mcp_service.annotations import read as read_ann
from mcp_service.read_ops import BadMonth, _envelope_error


def _validate_month(month: str) -> str:
    import re
    if not re.fullmatch(MONTH_PATTERN, month or ""):
        raise BadMonth(f"月份格式非法：{month!r}，应为 YYYY-MM")
    return month


def _month_range(month: str) -> tuple[date, date]:
    y, m = int(month[:4]), int(month[5:7])
    if m == 12:
        return date(y, 12, 1), date(y + 1, 1, 1)
    return date(y, m, 1), date(y, m + 1, 1)


def _iso(dt) -> str | None:
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%S") if hasattr(dt, "strftime") \
        else str(dt)


def _names(db) -> dict[str, str]:
    from app.models import Person
    return {p.code: p.display_name for p in db.query(Person).all()}


# ---------------------------------------------------------------------------
# 能力层：f(db, person_code, ...) -> dict
# ---------------------------------------------------------------------------

def _latest_month(db, person_code: str) -> str | None:
    """该人最新有数据的月：month_perf_records 优先，person_daily_stats 兜底。"""
    from app.models import MonthPerfRecord, PersonDailyStat
    m = (db.query(func.max(MonthPerfRecord.month))
         .filter(MonthPerfRecord.person_code == person_code).scalar())
    if m:
        return m
    day = (db.query(func.max(PersonDailyStat.ref_date))
           .filter(PersonDailyStat.person_code == person_code).scalar())
    return str(day)[:7] if day is not None else None


def _visible_from(db) -> str:
    """员工可见起始月（与网页 /my/perf 同源：perf.staff_visible_from；空=不限制）。"""
    from app.services import perf
    return (perf.staff_visible_from(db) or "").strip()


def _month_blocked(db, month: str) -> str | None:
    """该月是否对员工隐藏；隐藏则返回提示语（空=可见）。"""
    vf = _visible_from(db)
    if vf and month < vf:
        return (f"该月（{month}）未对员工开放：员工端当前可见起始月为 {vf}。"
                f"如需查看请选择 {vf} 及之后的月份。")
    return None


def my_perf(db, person_code: str, month: str | None = None) -> dict[str, Any]:
    """我的月绩效：月汇总（记录数/1点/2点/点数/工资/单价/对账偏差）+ 该月日统计行。"""
    from app.models import MonthPerfRecord, PersonDailyStat

    name = _names(db).get(person_code, person_code)
    month = (month or "").strip() or None
    if month is None:
        month = _latest_month(db, person_code)
        # 未指定月份时，若最新月对员工隐藏 → 回退到可见起始月之后的最新月
        vf = _visible_from(db)
        if month and vf and month < vf:
            month = vf
    if month:
        blocked = _month_blocked(db, month)
        if blocked:
            return {"month": month, "person_code": person_code, "name": name,
                    "summary": None, "daily": [], "currency": "JPY",
                    "hint": blocked}
    if not month:
        return {"month": None, "person_code": person_code, "name": name,
                "summary": None, "daily": [], "currency": "JPY",
                "hint": "该员工暂无绩效数据（合法结果，不是错误）"}
    _validate_month(month)

    mp = db.query(MonthPerfRecord).filter(
        MonthPerfRecord.month == month,
        MonthPerfRecord.person_code == person_code).first()
    lo, hi = _month_range(month)
    stats = db.query(PersonDailyStat).filter(
        PersonDailyStat.person_code == person_code,
        PersonDailyStat.ref_date >= lo,
        PersonDailyStat.ref_date < hi).order_by(PersonDailyStat.ref_date).all()
    daily = [{"date": str(s.ref_date), "records": s.records or 0,
              "p1": s.p1 or 0, "p2": s.p2 or 0,
              "points": s.points or 0} for s in stats]

    if mp is not None:
        summary = {
            "records": mp.records or 0, "p1": mp.p1 or 0, "p2": mp.p2 or 0,
            "points": mp.points or 0, "salary": mp.salary or 0,
            "per_point": mp.per_point,
            "settle_points": mp.settle_points or 0,
            "settle_amount": mp.settle_amount or 0,
            "diff_points": mp.diff_points or 0,
            "diff_amount": mp.diff_amount or 0,
            "rate37": mp.rate37, "pass37": mp.pass37,
        }
    else:
        summary = {
            "records": sum(d["records"] for d in daily),
            "p1": sum(d["p1"] for d in daily),
            "p2": sum(d["p2"] for d in daily),
            "points": sum(d["points"] for d in daily),
            "salary": None, "per_point": None,
            "settle_points": None, "settle_amount": None,
            "diff_points": None, "diff_amount": None,
            "rate37": None, "pass37": None,
        }

    data = {"month": month, "person_code": person_code, "name": name,
            "summary": summary, "daily": daily, "currency": "JPY"}
    if not daily and not summary["points"]:
        data["hint"] = "该月此人暂无明细（合法结果，不是错误）"
    return data


def my_daily(db, person_code: str, month: str) -> dict[str, Any]:
    """我的日明细：person_daily_stats 逐日（日期/店数/1点/2点/点数）+ 月汇总。"""
    from app.models import MonthPerfRecord, PersonDailyStat

    _validate_month(month)
    blocked = _month_blocked(db, month)
    if blocked:
        return {"month": month, "person_code": person_code,
                "name": _names(db).get(person_code, person_code),
                "daily": [], "summary": None, "currency": "JPY", "hint": blocked}
    name = _names(db).get(person_code, person_code)
    lo, hi = _month_range(month)
    stats = db.query(PersonDailyStat).filter(
        PersonDailyStat.person_code == person_code,
        PersonDailyStat.ref_date >= lo,
        PersonDailyStat.ref_date < hi).order_by(PersonDailyStat.ref_date).all()
    daily = [{"date": str(s.ref_date), "records": s.records or 0,
              "p1": s.p1 or 0, "p2": s.p2 or 0,
              "points": s.points or 0} for s in stats]
    mp = db.query(MonthPerfRecord).filter(
        MonthPerfRecord.month == month,
        MonthPerfRecord.person_code == person_code).first()
    summary = {
        "records": sum(d["records"] for d in daily),
        "p1": sum(d["p1"] for d in daily),
        "p2": sum(d["p2"] for d in daily),
        "points": sum(d["points"] for d in daily),
        "salary": (mp.salary or 0) if mp is not None else None,
        "per_point": mp.per_point if mp is not None else None,
    }
    data = {"month": month, "person_code": person_code, "name": name,
            "daily": daily, "summary": summary, "currency": "JPY"}
    if not daily:
        data["hint"] = "该月此人暂无日明细（合法结果，不是错误）"
    return data


def my_settlement(db, person_code: str) -> dict[str, Any]:
    """我的找平状态与发放：找平表（未结清/已结清）+ 发放台账 + 双向回收明细。"""
    from app.models import (PayrollAdjust, PayrollPayment,
                            PayrollSettlementLink)

    name = _names(db).get(person_code, person_code)
    adjusts = db.query(PayrollAdjust).filter(
        PayrollAdjust.person_code == person_code).order_by(
        PayrollAdjust.source_month, PayrollAdjust.id).all()
    payments = db.query(PayrollPayment).filter(
        PayrollPayment.person_code == person_code).order_by(
        PayrollPayment.month, PayrollPayment.seq, PayrollPayment.id).all()
    links = db.query(PayrollSettlementLink).filter(
        PayrollSettlementLink.person_code == person_code).order_by(
        PayrollSettlementLink.month, PayrollSettlementLink.id).all()

    pay_map = {p.id: p for p in payments}
    adj_map = {a.id: a for a in adjusts}
    by_adjust: dict[int, list] = {}
    by_payment: dict[int, list] = {}
    for lk in links:
        p = pay_map.get(lk.payment_id)
        by_adjust.setdefault(lk.adjust_id, []).append({
            "payment_id": lk.payment_id, "month": lk.month,
            "seq": p.seq if p else None,
            "payment_amount": p.amount if p else None,
            "amount": lk.amount,
        })
        a = adj_map.get(lk.adjust_id)
        by_payment.setdefault(lk.payment_id, []).append({
            "adjust_id": lk.adjust_id,
            "source_month": (a.source_month if a else lk.month),
            "adjust_amount": a.adjust_amount if a else None,
            "amount": lk.amount,
        })

    unsettled, settled = [], []
    for a in adjusts:
        rec = {
            "adjust_id": a.id, "source_month": a.source_month,
            "person_code": a.person_code,
            "adjust_amount": a.adjust_amount or 0,
            "settled_amount": a.settled_amount or 0,
            "remaining": a.remaining or 0,
            "status": a.status,
            "settled_at": _iso(a.settled_at),
            "source_task_id": a.source_task_id,
            "source_row_id": a.source_row_id,
            "recovered_by": by_adjust.get(a.id, []),
        }
        (settled if a.status == "settled" else unsettled).append(rec)

    payments_out = [{
        "payment_id": p.id, "month": p.month, "seq": p.seq,
        "points": p.points or 0, "amount": p.amount or 0,
        "bonus": p.bonus or 0, "adjust_applied": p.adjust_applied or 0,
        "adjust_leftover": p.adjust_leftover,
        "paid_at": _iso(p.paid_at),
        "applied_to_adjusts": by_payment.get(p.id, []),
    } for p in payments]

    remaining_total = sum((a.remaining or 0) for a in adjusts)
    data = {
        "person_code": person_code, "name": name, "currency": "JPY",
        "adjusts": {
            "unsettled": unsettled, "settled": settled,
            "count": len(adjusts),
            "unsettled_count": len(unsettled),
            "settled_count": len(settled),
            "original_total": sum((a.adjust_amount or 0) for a in adjusts),
            "settled_total": sum((a.settled_amount or 0) for a in adjusts),
            "remaining_total": remaining_total,
        },
        "payments": payments_out,
        "links": {"by_adjust": by_adjust, "by_payment": by_payment},
        "summary": {
            "remaining_total": remaining_total,
            "unsettled_count": len(unsettled),
            "settled_count": len(settled),
            "paid_total": sum((p.amount or 0) for p in payments),
            "adjust_applied_total": sum((p.adjust_applied or 0)
                                        for p in payments),
        },
    }
    if not adjusts and not payments:
        data["hint"] = "该员工暂无找平/发放记录（合法结果，不是错误）"
    return data


# ---------------------------------------------------------------------------
# 工具函数（模块级；走统一入口 authz.dispatch：授权 + 审计 + 执行）
# ---------------------------------------------------------------------------

def _person_code_of(actor) -> str | None:
    return actor.person_code if actor is not None else None


def _unbound_hint() -> dict:
    return _envelope_error(
        "BAD_PARAM", "账号未绑定人员",
        "管理员账号未绑定人员，请用公司级工具（visit_month_salary / "
        "visit_payroll_rows 等）查询", retryable=False)


def _guarded(ctx: Context, tool: str, params: dict, fn) -> dict:
    from mcp_service import authz
    from mcp_service.tools import actor_from_ctx, client_info_of

    actor = actor_from_ctx(ctx)
    from app.db import SessionLocal
    db = SessionLocal()
    try:
        return authz.dispatch(db, tool=tool, actor=actor, params=params,
                              client_info=client_info_of(ctx),
                              fn=fn, retryable=True, fn_args=2)
    finally:
        db.close()


def visit_my_perf(ctx: Context, month: str | None = None) -> dict[str, Any]:
    """我的月绩效（只读；无 person 参数）。"""
    params = {"month": month}

    def run(db, actor):
        pc = _person_code_of(actor)
        if not pc:
            return _unbound_hint()
        try:
            return {"ok": True, "data": my_perf(db, pc, month=month)}
        except BadMonth as exc:
            return _envelope_error("BAD_MONTH", str(exc),
                                   "月份必须是 YYYY-MM，例如 2026-09")
        except Exception as exc:  # noqa: BLE001
            return _envelope_error("INTERNAL", repr(exc),
                                   "系统内部错误，已记录；可重试")

    return _guarded(ctx, "visit_my_perf", params, run)


def visit_my_daily(ctx: Context,
                   month: Annotated[str, Field(pattern=MONTH_PATTERN)]) -> dict[str, Any]:
    """我的日明细（只读；无 person 参数）。"""
    params = {"month": month}

    def run(db, actor):
        pc = _person_code_of(actor)
        if not pc:
            return _unbound_hint()
        try:
            return {"ok": True, "data": my_daily(db, pc, month=month)}
        except BadMonth as exc:
            return _envelope_error("BAD_MONTH", str(exc),
                                   "月份必须是 YYYY-MM，例如 2026-09")
        except Exception as exc:  # noqa: BLE001
            return _envelope_error("INTERNAL", repr(exc),
                                   "系统内部错误，已记录；可重试")

    return _guarded(ctx, "visit_my_daily", params, run)


def visit_my_settlement(ctx: Context) -> dict[str, Any]:
    """我的找平状态与发放（只读；无参数）。"""
    params = {}

    def run(db, actor):
        pc = _person_code_of(actor)
        if not pc:
            return _unbound_hint()
        try:
            return {"ok": True, "data": my_settlement(db, pc)}
        except Exception as exc:  # noqa: BLE001
            return _envelope_error("INTERNAL", repr(exc),
                                   "系统内部错误，已记录；可重试")

    return _guarded(ctx, "visit_my_settlement", params, run)


# ---------------------------------------------------------------------------
# 注册（父会话在 tools.py 里接线调用）
# ---------------------------------------------------------------------------

def whoami(db, actor, username: str = None) -> dict[str, Any]:
    """**我是谁**：当前调用身份（账号/角色/绑定人员/权限范围/可用能力）。

    用途：用户问"我是谁""我能做什么""为什么不能查别人"时用我。
    """
    from app.models import Person, User
    u = db.get(User, actor.uid) if actor and actor.uid else None
    pc = getattr(actor, "person_code", None) or (u.person_code if u else None)
    person = db.query(Person).filter_by(code=pc).first() if pc else None
    role = (getattr(actor, "role", "") or (u.role if u else "")) or "unknown"
    is_admin = role == "admin"
    scopes = list(getattr(actor, "scopes", []) or [])
    return {
        "username": (u.username if u else None) or username,
        "display_name": (u.display_name if u else "") or "",
        "role": role,
        "role_label": "管理员" if is_admin else "员工",
        "person_code": pc,
        "person_name": (person.display_name if person else None),
        "scopes": scopes,
        "can_write": "write" in scopes,
        "capabilities": (
            ["查看公司级数据（月度汇总/薪资明细/看板/对账/找平）",
             "上传巡店与对账文件、入正式表、月度重算",
             "修改单价与系统配置、店铺主档合并/拆分",
             "导出发薪表、找平表、对账报表"]
            if is_admin else
            ["查看**本人**绩效与日明细",
             "查看**本人**的找平与已发工资",
             "不能查看他人或公司级数据，不能修改任何数据"]
        ),
        "currency": "JPY",
        "hint": ("管理员身份：可用全部工具。"
                 if is_admin else
                 "员工身份：仅可使用『我的』系列工具（我的绩效/我的日明细/我的找平与发放）；"
                 "查询公司级数据请用管理员账号。"),
    }


def register(mcp: MCPServer) -> None:
    mcp.tool(
        name="visit_my_perf",
        title="我的月绩效",
        annotations=read_ann("我的月绩效"),
        description=(
            "查询**我自己**（当前 Token 绑定的员工账号对应的人员）的月度绩效："
            "月汇总（有效店数/1点/2点/总点数/工资円/单价/对账偏差）与当月日统计行。"
            "month 可选（YYYY-MM）；不传=最新有数据的月。"
            "**只返回本人数据，无 person 参数**；金额为日元円（currency=JPY）。"
            "管理员调用时按管理员自己绑定的人员编号过滤；未绑定人员返回 BAD_PARAM"
            "（请用公司级工具查询）。只读。"
        ),
    )(visit_my_perf)

    mcp.tool(
        name="visit_my_daily",
        title="我的日明细",
        annotations=read_ann("我的日明细"),
        description=(
            "查询**我自己**在某结算月（YYYY-MM，必填）的每日明细："
            "person_daily_stats 逐日（日期/有效店数/1点/2点/点数）+ 该月汇总"
            "（店数/点数/工资円）。"
            "**只返回本人数据，无 person 参数**；金额为日元円（currency=JPY）。"
            "管理员调用时按管理员自己绑定的人员编号过滤；未绑定人员返回 BAD_PARAM"
            "（请用公司级工具查询）。只读。"
        ),
    )(visit_my_daily)

    mcp.tool(
        name="visit_my_settlement",
        title="我的找平与发放",
        annotations=read_ann("我的找平与发放"),
        description=(
            "查询**我自己**（当前 Token 绑定的人员）的薪资找平状态与发放："
            "① 找平表（payroll_adjusts：未结清/已结清，含原始金额/已找平/剩余/结清时间）；"
            "② 发放台账（payroll_payments：各期实发金额/点数/奖金/抵扣 adjust_applied）；"
            "③ 双向回收明细（payroll_settlement_links：每笔找平被哪几期发放冲抵、"
            "每笔发放冲了哪几笔找平）。"
            "含 remaining（剩余）合计与 summary。"
            "**只返回本人数据，无 person 参数**；金额为日元円（currency=JPY）。"
            "管理员调用时按管理员自己绑定的人员编号过滤；未绑定人员返回 BAD_PARAM"
            "（请用公司级工具查询）。只读。"
        ),
    )(visit_my_settlement)


def visit_whoami(ctx: Context) -> dict[str, Any]:
    """**我是谁**：返回当前调用身份与权限范围（授权 + 审计走统一入口）。"""
    from mcp_service import authz
    from mcp_service.tools import _client_info, actor_from_ctx
    from app.db import SessionLocal as _SL
    actor = actor_from_ctx(ctx)
    db = _SL()
    try:
        def _run(d):
            return {"ok": True, "data": whoami(d, actor)}
        return authz.dispatch(db, tool="visit_whoami", actor=actor, params={},
                              client_info=_client_info(ctx), fn=_run,
                              retryable=True, fn_args=1)
    finally:
        db.close()


def register_whoami(mcp: MCPServer) -> None:
    from mcp_service.annotations import read as read_ann
    mcp.tool(
        name="visit_whoami",
        title="我是谁（身份与权限）",
        annotations=read_ann("我是谁（身份与权限）"),
        description=(
            "**返回当前调用的身份与权限范围**：账号、显示名、角色（管理员/员工）、"
            "绑定的人员编号与姓名、scope、能否写、以及**可用能力清单**。"
            "什么时候用：用户问「我是谁」「我能做什么」「为什么查不到别人的数据」"
            "「我有没有权限导出」时用我；也可用于在操作前确认当前身份。只读、无参数。"
        ),
    )(visit_whoami)
