# -*- coding: utf-8 -*-
"""对账只读能力层（P1 二期）。

能力函数（供 scenario_ops 的 visit_recon / visit_payroll / visit_recon_export 调用）：
  _recon_status        对账任务列表 + 差异统计（差异人数 / 差异金额円）
  _recon_diff          对账差异明细（人月差异 / 员工×日问题行 / 反向名单 / 对账侧汇总）
  _recon_adjust_state  某月找平状态（已确认找平记录 + 偏差金额，只读展示）
  _recon_settlement    找平结清状态
  _settlement_trace    找平↔薪资双向轨迹

硬性只读约束：所有路径只查询，不得 db.commit()/db.flush()/db.delete()；
不得调用任何写函数（confirm_adjust / cancel_adjust / run_task / start_task /
launch_task / submit_task / set_period_* / sync_*）。
口径对齐 app/routers/settle_r.py 的 recon_page / recon_export 与
app/services/recon.build_report；金额一律日元（円，字段 currency=JPY）。
"""
import re
from typing import Any

from mcp.server.mcpserver import Context

from app.db import SessionLocal
from mcp_service.capability import MONTH_PATTERN

# 本文件**不注册工具**（注册统一在 scenario_ops.py）；只暴露能力函数
#（_recon_status / _recon_diff / _recon_adjust_state / _recon_settlement /
#  _settlement_trace / _current_task_for_month）供 visit_recon / visit_payroll 调用。


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


def _authz_read(ctx: Context, fn, tool: str = None) -> dict[str, Any]:
    """**读工具统一包装**：授权拦截 → 两阶段审计 → 能力层 → 信封。

    为什么必须有：只读工具同样受授权矩阵约束（员工不得读公司级数据），
    且每次调用都要留痕（含被拒绝的调用）。工具名取自调用者函数名。
    """
    import inspect as _inspect
    from mcp_service import authz
    from mcp_service.tools import _client_info, actor_from_ctx

    tool = tool or next((f.function for f in _inspect.stack()
                         if f.function.startswith("visit_")), "unknown")
    actor = actor_from_ctx(ctx)
    db = SessionLocal()          # 模块级（测试会 monkeypatch 它，故不能局部 import）
    try:
        return authz.dispatch(db, tool=tool, actor=actor, params={},
                              client_info=_client_info(ctx),
                              fn=lambda d: fn(d), retryable=True, fn_args=1)
    finally:
        db.close()


def _run_read(ctx: Context, fn) -> dict[str, Any]:
    """对账读工具统一包装：授权 + 审计 外层；内层保留 ReconError → 错误码映射。"""
    def _mapped(db):
        try:
            data = fn(db)
        except ReconError as exc:
            return _err(exc.code, exc.message, exc.hint)
        except Exception as exc:  # noqa: BLE001
            return _err("INTERNAL", repr(exc), "系统内部错误，已记录；可重试")
        return {"ok": True, "data": data}
    return _authz_read(ctx, _mapped)

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
                "请用 visit_recon(view='status') 查看任务 id；"
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

    # 当前系统侧点数（现算，用于与任务快照对比；快照可能因后续重算而过期）
    from sqlalchemy import func
    from app.models import FormalRecord
    cur_pts: dict[str, int] = {}
    if m:
        y, mo = int(m[:4]), int(m[5:7])
        nxt = f"{y + 1}-01-01" if mo == 12 else f"{y}-{mo + 1:02d}-01"
        for pc, pts in db.query(FormalRecord.person_code,
                                func.sum(FormalRecord.points)).filter(
                FormalRecord.japan_date >= f"{m}-01",
                FormalRecord.japan_date < nxt).group_by(
                FormalRecord.person_code).all():
            cur_pts[pc] = int(pts or 0)

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
            # 当前系统侧（现算）：与快照不同说明任务创建后又重算过正式表
            "system_value_current": cur_pts.get(r.submitter_code),
            "snapshot_stale": (cur_pts.get(r.submitter_code) is not None
                               and (r.system_value or 0)
                               != cur_pts.get(r.submitter_code)),
            "adjust_amount_current": _adjust_amount_jpy(
                db, cur_pts.get(r.submitter_code, r.system_value or 0),
                r.report_value or 0, m),
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
    # 快照 vs 当前 的合计对比（分叉一眼可见）
    stale = [d for d in person_diffs if d.get("snapshot_stale")]
    snap_total = sum(d["adjust_amount"] or 0 for d in person_diffs)
    cur_total = sum(d["adjust_amount_current"] or 0 for d in person_diffs)
    data["amount_scope"] = {
        "snapshot_total": snap_total,          # 任务创建时的系统侧口径
        "current_total": cur_total,            # 现算系统侧口径（与找平表/工资一致）
        "stale_persons": [{"person_code": d["person_code"], "name": d["name"],
                           "snapshot_points": d["system_value"],
                           "current_points": d["system_value_current"]}
                          for d in stale],
        "note": ("snapshot=任务创建时的系统侧快照（审计留痕）；"
                 "current=按当前正式表现算（与找平表/工资口径一致）。"
                 "两者不同说明任务创建后又重算过正式表。"),
    }
    if stale:
        data["hint"] = (f"注意：{len(stale)} 人的系统侧快照已过期（任务创建后正式表被重算）"
                        f"→ 找平金额按快照为 {snap_total:,}，按当前为 {cur_total:,}；"
                        f"**以 current_total 为准**（与找平表一致）。")
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
