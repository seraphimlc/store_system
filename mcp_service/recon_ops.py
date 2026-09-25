# -*- coding: utf-8 -*-
"""对账相关只读 MCP 工具（P1 二期；由父会话接线到 tools.py，本文件不修改 tools.py）。

工具：
  visit_recon_status        对账任务列表 + 差异统计（差异人数 / 差异金额円）
  visit_recon_diff          对账差异明细（人月差异 / 员工×日问题行 / 反向名单 / 对账侧汇总）
  visit_recon_adjust_state  某月找平状态（已确认找平记录 + 偏差金额，只读展示）

硬性只读约束：所有路径只查询，不得 db.commit()/db.flush()/db.delete()；
不得调用任何写函数（confirm_adjust / cancel_adjust / run_task / start_task /
launch_task / submit_task / set_period_* / sync_*）。
口径对齐 app/routers/settle_r.py 的 recon_page / recon_export 与
app/services/recon.build_report；金额一律日元（円，字段 currency=JPY）。
"""
import re
from typing import Any

from mcp.server.mcpserver import Context, MCPServer

from app.db import SessionLocal
from mcp_service.capability import MONTH_PATTERN
from mcp_service.annotations import read as read_ann

# 注册时经 tools.py 接线：tools.register 内调用 recon_ops.register(mcp)。
# 本文件只实现 register；工具函数定义在模块级（便于单测直接调用与断言信封）。


class ReconError(RuntimeError):
    """带错误码的只读错误：由工具层转成错误信封。"""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint


def _bad_month(month) -> ReconError:
    return ReconError("BAD_MONTH", f"月份格式非法：{month!r}，应为 YYYY-MM",
                      "月份必须是 YYYY-MM，例如 2026-09")


def _validate_optional_month(month: str | None) -> str | None:
    """status/diff 的 month：None 放行；非 None 必须匹配 MONTH_PATTERN。"""
    if month is not None and not re.fullmatch(MONTH_PATTERN, month or ""):
        raise _bad_month(month)
    return month


def _require_month(month: str | None) -> str:
    """adjust_state 的 month：必填且必须匹配 MONTH_PATTERN。"""
    if not re.fullmatch(MONTH_PATTERN, month or ""):
        raise _bad_month(month)
    return month


def _err(code: str, message: str, hint: str, **extra) -> dict[str, Any]:
    """统一失败信封（含 retryable；码表见 mcp_service/envelope.py）。"""
    from mcp_service import envelope
    return envelope.error(code, message, hint, **extra)


def _run_read(ctx: Context, fn) -> dict[str, Any]:
    """只读工具统一包装：经 ctx 取 actor → 业务 → 错误信封。

    - 读工具不因 actor 为 None 拒绝（stdio 模式无鉴权头；HTTP 模式由中间件拦 401）。
    - ReconError → 对应错误码；其余意外异常 → INTERNAL（spec §5.5）。
    - 每调用独立 SessionLocal()，finally 关闭。
    """
    from mcp_service.tools import actor_from_ctx  # 延迟 import，避免父会话接线形成环
    actor = actor_from_ctx(ctx)  # noqa: F841  规范要求经 ctx 取 actor（预留审计接线）
    db = None
    try:
        db = SessionLocal()
        data = fn(db)
    except ReconError as exc:
        return _err(exc.code, exc.message, exc.hint)
    except Exception as exc:  # noqa: BLE001  只读工具意外异常 -> INTERNAL（可重试）
        return _err("INTERNAL", repr(exc), "系统内部错误，已记录；可重试")
    finally:
        if db is not None:
            db.close()
    return {"ok": True, "data": data}


# ---------------- 工具函数（模块级，可直接单测） ----------------

def visit_recon_status(ctx: Context,
                       month: str | None = None) -> dict[str, Any]:
    """对账任务列表（只读）。month 可选：YYYY-MM，只列该月任务。"""
    return _run_read(ctx, lambda db: _recon_status(db, month))


def visit_recon_diff(ctx: Context, task_id: int | None = None,
                     month: str | None = None) -> dict[str, Any]:
    """对账差异明细（只读）。task_id 与 month 二选一。"""
    return _run_read(ctx, lambda db: _recon_diff(db, task_id=task_id,
                                                 month=month))


def visit_settlement_trace(ctx: Context, month: str = None,
                           person: str = None, adjust_id: int = None,
                           payment_id: int = None) -> dict[str, Any]:
    return _run_read(ctx, lambda db: _settlement_trace(
        db, month=month, person=person, adjust_id=adjust_id,
        payment_id=payment_id))


def visit_recon_settlement(ctx: Context, month: str) -> dict[str, Any]:
    return _run_read(ctx, lambda db: _recon_settlement(db, month))


def visit_recon_adjust_state(ctx: Context, month: str) -> dict[str, Any]:
    """某月找平状态（只读）。month 必填 YYYY-MM。"""
    return _run_read(ctx, lambda db: _recon_adjust_state(db, month))


# ---------------- 业务实现（f(db, ...) -> dict；口径以实际代码为准） ----------------

def _iso(dt) -> str | None:
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%S") if hasattr(dt, "strftime") \
        else str(dt)


def _person_names(db) -> dict:
    from app.models import Person
    return {p.code: p.display_name for p in db.query(Person).all()}


def _month_per_point(db, month: str) -> int:
    from app.services import perf
    return perf.month_per_point(db, month)


def _adjust_amount_jpy(db, sys_pts: int, rep_pts: int, month: str) -> int:
    """应找平金额(円，含奖金) = 对账金额 − 系统已发金额（负=扣款/正=补款）。

    口径 = settle_r.recon_page 的 _pay_diff / recon.build_report 的 _diff_amount：
    salary_for(对账点数) − salary_for(系统点数)，单价取该月 month_per_point。
    """
    from app.services import perf
    pp = _month_per_point(db, month)
    return perf.salary_for(rep_pts or 0, pp, month) \
        - perf.salary_for(sys_pts or 0, pp, month)


def _task_diff_stats(db, task) -> tuple[int, int]:
    """差异人数 = 有差异(≠0)的 ReconResult 人数 ∪ 员工×日问题行人数；
    差异金额(円) = 各人月差异行按工资规则折算的应找平金额合计（负=扣款/正=补款）。"""
    from app.models import ReconDayRow, ReconResult
    month = (task.params or {}).get("month", "")
    persons: set = set()
    amount = 0
    for r in db.query(ReconResult).filter(
            ReconResult.task_id == task.id).all():
        if r.diff:
            if r.submitter_code:
                persons.add(r.submitter_code)
            amount += _adjust_amount_jpy(db, r.system_value or 0,
                                         r.report_value or 0, month)
    for d in db.query(ReconDayRow).filter(
            ReconDayRow.task_id == task.id).all():
        if d.person_code:
            persons.add(d.person_code)
    return len(persons), amount


def _current_task_for_month(db, month: str):
    """该月「当前」对账任务：kind=monthly_v3、未标记 replaced_by、id 最大。

    口径 = app/services/period.py 的 _current_recon。
    """
    from app.models import ReconTask
    cur = None
    for t in db.query(ReconTask).filter(
            ReconTask.kind == "monthly_v3").all():
        p = t.params or {}
        if p.get("month") != month or p.get("replaced_by"):
            continue
        if cur is None or t.id > cur.id:
            cur = t
    return cur


def _recon_status(db, month: str | None = None) -> dict[str, Any]:
    _validate_optional_month(month)
    from app.models import ReconTask
    tasks = []
    for t in db.query(ReconTask).filter(
            ReconTask.kind == "monthly_v3").order_by(
            ReconTask.id.desc()).all():
        p = t.params or {}
        if month is not None and p.get("month") != month:
            continue
        s = t.summary or {}
        diff_persons, diff_amount = _task_diff_stats(db, t)
        tasks.append({
            "id": t.id,
            "month": p.get("month", ""),
            "kind": "recon" if t.kind == "monthly_v3" else s.get("kind", p.get("kind", "")),
            "filename": p.get("file", ""),
            "status": t.status,
            "created_at": _iso(t.created_at),
            "finished_at": _iso(t.finished_at),
            "is_previous": bool(p.get("replaced_by")),
            "replaced_by": p.get("replaced_by"),
            "version": p.get("version"),
            "diff_persons": diff_persons,
            "diff_amount": diff_amount,
            "currency": "JPY",
        })
    data: dict[str, Any] = {"tasks": tasks, "count": len(tasks),
                            "currency": "JPY"}
    if not tasks:
        data["hint"] = (f"{month} 无对账任务（合法结果，不是错误）"
                        if month is not None else
                        "系统尚无对账任务（合法结果，不是错误）")
    return data


def _recon_diff(db, task_id: int | None = None,
                month: str | None = None) -> dict[str, Any]:
    _validate_optional_month(month)
    from app.models import (AdjustRecord, ReconDataRow, ReconDayRow,
                            ReconResult, ReconTask)
    task = None
    if task_id is not None:
        task = db.get(ReconTask, task_id)
        if task is None:
            raise ReconError(
                "NOT_FOUND", f"对账任务不存在：id={task_id}",
                "请用 visit_recon_status 查看任务 id；"
                "或改用 month 参数查该月当前对账任务")
    elif month is not None:
        task = _current_task_for_month(db, month)
    else:
        raise ReconError(
            "BAD_PARAM", "必须提供 task_id 或 month 之一",
            "task_id=精确任务；month=该月当前版本任务，二选一")
    if task is None:
        return {"task": None, "month": month, "person_diffs": [],
                "day_diffs": [], "system_only": [], "report_totals": [],
                "currency": "JPY",
                "hint": f"{month} 无对账任务（合法结果，不是错误）"}

    p = task.params or {}
    s = task.summary or {}
    m = p.get("month", "")
    names = _person_names(db)
    adj = {a.person_code: a for a in db.query(AdjustRecord).filter(
        AdjustRecord.source_task_id == task.id).all()}

    person_diffs = []
    for r in db.query(ReconResult).filter(
            ReconResult.task_id == task.id).order_by(
            ReconResult.submitter_code).all():
        person_diffs.append({
            "person_code": r.submitter_code,
            "name": (r.note or "").replace(" 点数差异", "") or
                    names.get(r.submitter_code, r.submitter_code),
            "system_value": r.system_value or 0,
            "report_value": r.report_value or 0,
            "diff": r.diff or 0,
            "adjust_amount": _adjust_amount_jpy(
                db, r.system_value or 0, r.report_value or 0, m),
            "adjusted": adj.get(r.submitter_code) is not None,
            "note": r.note or "",
        })

    day_diffs = []
    for d in db.query(ReconDayRow).filter(
            ReconDayRow.task_id == task.id).order_by(
            ReconDayRow.ref_date, ReconDayRow.person_code).all():
        day_diffs.append({
            "date": str(d.ref_date),
            "person_code": d.person_code,
            "name": names.get(d.person_code, d.person_code),
            "sys_points": d.sys_points or 0,
            "rep_points": d.rep_points or 0,
            "sys_count": d.sys_count or 0,
            "rep_count": d.rep_count or 0,
            "diff": d.diff or 0,
            "side": d.side,
            "note": d.note or "",
        })

    system_only = [{"person_code": c, "name": names.get(c, c)}
                   for c in sorted(s.get("sys_only") or [])]

    totals: dict = {}
    for r in db.query(ReconDataRow).filter(
            ReconDataRow.task_id == task.id).all():
        a = totals.setdefault(r.person_code, {
            "person_code": r.person_code,
            "name": r.person_name or names.get(r.person_code,
                                               r.person_code),
            "points": 0, "cnt": 0})
        a["points"] += r.points or 0
        a["cnt"] += r.cnt or 0
    report_totals = [totals[c] for c in sorted(totals)]

    data: dict[str, Any] = {
        "task": {"id": task.id, "month": m,
                 "kind": "recon" if task.kind == "monthly_v3" else s.get("kind", p.get("kind", "")),
                 "filename": p.get("file", ""),
                 "status": task.status,
                 "version": p.get("version"),
                 "is_previous": bool(p.get("replaced_by")),
                 "replaced_by": p.get("replaced_by"),
                 "compared": s.get("compared", 0),
                 "diff_count": s.get("diff_count", 0),
                 "created_at": _iso(task.created_at)},
        "person_diffs": person_diffs,
        "day_diffs": day_diffs,
        "system_only": system_only,
        "report_totals": report_totals,
        "currency": "JPY",
    }
    if not person_diffs and not day_diffs and not system_only:
        data["hint"] = "该任务无差异行（两侧一致，合法结果，不是错误）"
    return data


def _settlement_trace(db, month=None, person=None, adjust_id=None,
                      payment_id=None) -> dict[str, Any]:
    """找平↔薪资 双向轨迹（月/人/找平笔/发放笔 四维过滤）。"""
    if month is not None and str(month).strip() != "":
        _validate_optional_month(month)
    from app.services import period
    return period.settlement_trace(db, month=month, person=person,
                                   adjust_id=adjust_id, payment_id=payment_id)


def _recon_settlement(db, month: str) -> dict[str, Any]:
    """找平结清状态（只读）：该月对账差异扣/补到哪一步、是否已结清。"""
    _require_month(month)
    from app.services import period
    return period.settlement_status(db, month)


def _recon_adjust_state(db, month: str) -> dict[str, Any]:
    _require_month(month)
    from app.models import AdjustRecord, MonthPerfRecord, PayrollPeriodRow
    names = _person_names(db)
    adj_rows = db.query(AdjustRecord).filter(
        AdjustRecord.month == month).order_by(
        AdjustRecord.person_code).all()
    perf_map = {r.person_code: r for r in db.query(MonthPerfRecord).filter(
        MonthPerfRecord.month == month).all()}
    pay_map = {r.person_code: r for r in db.query(PayrollPeriodRow).filter(
        PayrollPeriodRow.month == month).all()}
    codes = sorted(set(a.person_code for a in adj_rows)
                   | set(perf_map) | set(pay_map))
    adj_by_code = {a.person_code: a for a in adj_rows}
    rows = []
    for code in codes:
        a = adj_by_code.get(code)
        mp = perf_map.get(code)
        pr = pay_map.get(code)
        rows.append({
            "person_code": code,
            "name": names.get(code, code),
            "confirmed": a is not None,
            # AdjustRecord.amount 实际为点数差（正=补/负=扣）；円金额 = amount×per_point
            "adjust_points": a.amount if a else None,
            "adjust_per_point": a.per_point if a else None,
            "adjust_amount": (a.amount * a.per_point) if a else None,
            "amount_adj": a.amount_adj if a else None,   # 已写入薪资找平的金额增量(円)
            "applied_to_month": a.applied_to_month if a else None,
            "reason": a.reason if a else None,
            "source_task_id": a.source_task_id if a else None,
            "source_points_diff": a.source_points_diff if a else None,
            "created_at": _iso(a.created_at) if a else None,
            "perf_diff_amount": (mp.diff_amount or 0) if mp else None,
            "payroll_diff_amount": (pr.diff_amount or 0) if pr else None,
            "payroll_adjust_amount": (pr.adjust_amount or 0) if pr else None,
        })
    data: dict[str, Any] = {
        "month": month,
        "rows": rows,
        "currency": "JPY",
        "summary": {
            "persons": len(codes),
            "confirmed": len(adj_rows),
            "total_adjust_amount": sum((a.amount or 0)
                                       * (a.per_point or 250)
                                       for a in adj_rows),
            "total_amount_adj": sum(a.amount_adj or 0 for a in adj_rows),
            "total_perf_diff_amount": sum((mp.diff_amount or 0)
                                          for mp in perf_map.values()),
        },
    }
    if not rows:
        data["hint"] = f"{month} 无找平/偏差数据（合法结果，不是错误）"
    return data


# ---------------- 注册 ----------------

_DESC_STATUS = (
    "查询巡店对账任务列表（只读）。返回每个任务的 id、对账月份、状态、创建时间、"
    "是否「上一版」（同月重传后旧任务被标记 replaced_by）、以及差异统计：差异人数"
    "（有差异的人员数）与差异金额（各人月差异按工资规则折算的应找平金额，円，"
    "负=扣款/正=补款）。可选参数 month（YYYY-MM）只列该月任务；不传则列出全部。"
    "数据来源 recon_tasks 及其差异行，仅查询不写入。"
    "金额单位为日元（円，字段 currency=JPY）。"
)

_DESC_DIFF = (
    "查询对账差异明细（只读）。task_id 与 month 二选一：task_id=精确对账任务；"
    "month=该月当前版本任务（同月重传后取最新、排除上一版）。返回："
    "① 按人的月差异（系统值 vs 对账值、差异点数、应找平金额円、是否已确认找平）；"
    "② 按 员工×日 的问题行（系统点数 vs 对账点数、行数、差异、归属侧）；"
    "③ 「系统有、对账文件未列出」的反向名单；"
    "④ 对账文件侧全量点数汇总（来源 recon_data_rows）。"
    "任务不存在返回 NOT_FOUND；该月无对账任务返回空结果（ok=True 带 hint）。"
    "仅查询不写入、不触发对账重跑。金额单位为日元（円，字段 currency=JPY）。"
)

_DESC_ADJUST = (
    "查询某月（YYYY-MM，必填）的薪资找平状态（只读）。"
    "返回该月各人已确认的找平记录（来源 adjust_records：找平点数=adjust_points、"
    "锁存单价=adjust_per_point、金额=adjust_amount 円、金额增量=amount_adj 円、"
    "生效月=applied_to_month、原因）与偏差金额（来源 month_perf_records 的 "
    "diff_amount 与 payroll_period_rows 的 diff_amount / adjust_amount，円；"
    "负=扣款/正=补款）。仅查询展示，不写入、不执行找平。"
    "金额均为日元（円，字段 currency=JPY）。"
)


def register(mcp: MCPServer) -> None:
    """注册 3 个对账只读工具（父会话在 tools.py 里接线调用）。"""
    mcp.tool(name="visit_recon_status", title="对账任务列表",
             annotations=read_ann("对账任务列表"),
             description=_DESC_STATUS)(visit_recon_status)
    mcp.tool(name="visit_recon_diff", title="对账差异明细",
             annotations=read_ann("对账差异明细"),
             description=_DESC_DIFF)(visit_recon_diff)
    mcp.tool(name="visit_recon_adjust_state", title="薪资找平状态",
             annotations=read_ann("薪资找平状态"),
             description=_DESC_ADJUST)(visit_recon_adjust_state)
    mcp.tool(name="visit_recon_settlement", title="找平结清状态",
             annotations=read_ann("找平结清状态"),
             description=(
                 "查询某结算月**找平的结清状态**：这笔对账差异扣/补到哪一步、是否已找平完毕。"
                 "返回每人 diff_amount（原始差异）/ chain（源月及后续各月结转债务）/ "
                 "recovered（已回收）/ remaining（还没扣补完的余额）/ "
                 "status（settled=已结清 / in_progress=找平中）。"
                 "**核对『某笔对账是否已找平完毕』用我**；逐笔证据（哪期扣多少、来源任务）"
                 "见 visit_payroll_rows 的台账字段。参数：month（YYYY-MM）。只读，日元。")
             )(visit_recon_settlement)
    mcp.tool(name="visit_settlement_trace", title="找平薪资双向轨迹",
             annotations=read_ann("找平薪资双向轨迹"),
             description=(
                 "**找平 ↔ 薪资 双向轨迹查询**（核对『谁欠谁、还了多少、从哪期扣的』用我）。"
                 "四个过滤维度（可单用/组合，全不传=全部找平汇总）："
                 "month=源月（如 2026-08，同时列出该月发放的冲抵）；person=工号或姓名；"
                 "adjust_id=单笔找平（给完整轨迹：原始/已找平/剩余/是否结清 + 被哪几期回收）；"
                 "payment_id=单笔发放（列出它冲了哪几笔找平，可能跨月多条）。"
                 "返回 adjusts[]（找平及 recovered_by 反向明细）与 payments[]（发放及 adjusts 正向明细）"
                 "+ summary（笔数/已结清数/原始合计/已找平合计/剩余合计）。"
                 "只读；金额为日元（JPY）。")
             )(visit_settlement_trace)
