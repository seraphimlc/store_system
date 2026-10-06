# -*- coding: utf-8 -*-
"""MCP 工具场景化层（49 → 16，规格：docs/specs-mcp-tools-scenario.md）。

设计铁律：
- **一个工具 = 一个用户会问的问题 / 会派的一件事**；工具名用用户语言（visit_upload）。
- **薄封装**：本文件只做「取 Context → 校验 view/action 枚举 → 调既有能力函数
  （read_ops / my_ops / recon_ops / export_ops / misc_ops / write_ops /
  recon_write_ops / payroll_write_ops / store_write_ops / write_tools）
  → 包统一错误信封」。**绝不重写业务逻辑**，业务全部复用各 ops 模块的能力函数。
- **读写分离**：写工具（visit_upload / visit_payroll_export / visit_rebuild(run) /
  visit_staff(set_status) / visit_config(set) / visit_store(merge|split|apply|skip)）
  → readOnlyHint=false + 写闸门（确认语/预演）；其余只读。
- **非法 view/action → BAD_PARAM + 列出可选值**（在授权之后校验，未授权调用者不得探测参数）。
- 金额一律日元（円，currency=JPY）；月份一律 YYYY-MM（BAD_MONTH 拒非法）。
"""
from typing import Any

from mcp.server.mcpserver import Context, MCPServer

from app.db import SessionLocal          # 模块级：测试 monkeypatch 用
from mcp_service import guards
from mcp_service.annotations import read as read_ann
from mcp_service.annotations import write as write_ann
from mcp_service.tools import _client_info, _write_call, actor_from_ctx
from mcp_service.capability import MONTH_PATTERN
import re


# ---------------------------------------------------------------------------
# 统一信封 / 错误映射
# ---------------------------------------------------------------------------

def _envelope_error(code: str, message: str, hint: str = "",
                    **extra: Any) -> dict[str, Any]:
    """统一失败信封（含 retryable；码表见 mcp_service/envelope.py）。"""
    from mcp_service import envelope
    return envelope.error(code, message, hint, **extra)


def _bad_view(view: str, options: list[str]) -> dict[str, Any]:
    """非法 view → BAD_PARAM + 可选值提示（规格：非法枚举值必须给可选值）。"""
    return _envelope_error(
        "BAD_PARAM", f"未知 view：{view!r}",
        "可选 view：" + "、".join(options) + f"（收到 {view!r}）")


def _bad_action(action: str, options: list[str]) -> dict[str, Any]:
    """非法 action → BAD_PARAM + 可选值提示。"""
    return _envelope_error(
        "BAD_PARAM", f"未知 action：{action!r}",
        "可选 action：" + "、".join(options) + f"（收到 {action!r}）")


def _map_error(exc: Exception) -> dict[str, Any]:
    """把各能力层的业务异常统一映射到错误信封（只读路径）。"""
    if isinstance(exc, guards.GuardError):
        return _envelope_error(exc.code, exc.message, exc.hint or "")
    from mcp_service import capability, export_ops, misc_ops, read_ops, recon_ops
    if isinstance(exc, (read_ops.BadMonth, capability.BadMonth)):
        return _envelope_error("BAD_MONTH", str(exc),
                               "月份必须是 YYYY-MM，例如 2026-09")
    if isinstance(exc, (read_ops.BadParam, capability.BadParam)):
        return _envelope_error("BAD_PARAM", str(exc),
                               getattr(exc, "hint", "") or "")
    if isinstance(exc, read_ops.NotFound):
        return _envelope_error("NOT_FOUND", str(exc), exc.hint or "")
    if isinstance(exc, recon_ops.ReconError):
        return _envelope_error(exc.code, exc.message, exc.hint or "")
    if isinstance(exc, misc_ops.MiscError):
        return _envelope_error(exc.code, exc.message, exc.hint or "")
    if isinstance(exc, export_ops.ExportError):
        return _envelope_error(exc.code, exc.message, exc.hint or "")
    return _envelope_error("INTERNAL", repr(exc), "系统内部错误，已记录；可重试")


def _read(ctx: Context, tool: str, params: dict, fn) -> dict[str, Any]:
    """只读统一入口：授权拦截 → 两阶段审计 → 能力层 → 错误信封。

    fn 签名：fn(db) -> data dict 或完整信封（含 "ok" 键时原样透传，
    否则包 {"ok": True, "data": ...}）。意外异常兜底 INTERNAL；
    业务异常经 _map_error 映射。
    """
    from mcp_service import authz

    actor = actor_from_ctx(ctx)
    db = SessionLocal()
    try:
        def _mapped(d):
            try:
                res = fn(d)
            except Exception as exc:  # noqa: BLE001
                return _map_error(exc)
            if isinstance(res, dict) and "ok" in res:
                return res
            return {"ok": True, "data": res}
        return authz.dispatch(db, tool=tool, actor=actor, params=params,
                              client_info=_client_info(ctx), fn=_mapped,
                              retryable=True, fn_args=1)
    finally:
        db.close()


def _write(ctx: Context, tool: str, params: dict, fn, *, retryable: bool) -> dict[str, Any]:
    """写统一入口：复用 tools._write_call（授权 → 审计 → 闸门/业务 → 信封）。"""
    return _write_call(ctx, tool, params, fn, retryable=retryable)


def _validate_optional_month(month: str | None) -> str | None:
    """可选月份：None 放行；非 None 必须 YYYY-MM。"""
    if month is not None and not re.fullmatch(MONTH_PATTERN, str(month).strip() or ""):
        raise guards.GuardError("BAD_MONTH", f"月份格式非法：{month!r}，应为 YYYY-MM",
                                "月份必须是 YYYY-MM，例如 2026-09")
    return month.strip() if month is not None and str(month).strip() else (month or None)


def _iso(dt) -> str | None:
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%S") if hasattr(dt, "strftime") else str(dt)


# ===========================================================================
# 员工侧（3 个）
# ===========================================================================

def _person_code_of(actor) -> str | None:
    return actor.person_code if actor is not None else None


def _unbound_hint() -> dict[str, Any]:
    return _envelope_error(
        "BAD_PARAM", "账号未绑定人员",
        "当前账号未绑定人员编号，无法查本人数据；请用管理员账号或绑定人员", retryable=False)


def _my_month_latest(db, person_code: str) -> str | None:
    """该人最新有数据的月（与 my_ops 同源：月绩效优先，人×日兜底）。"""
    from mcp_service import my_ops
    return my_ops._latest_month(db, person_code)


# ------------------------------ visit_whoami ------------------------------

def _whoami(db, actor) -> dict[str, Any]:
    from mcp_service import my_ops
    return my_ops.whoami(db, actor)


def visit_whoami(ctx: Context) -> dict[str, Any]:
    """我是谁：账号/角色/绑定人员/权限/可用能力（员工与管理员均可用）。"""
    from mcp_service import authz

    actor = actor_from_ctx(ctx)
    db = SessionLocal()
    try:
        return authz.dispatch(db, tool="visit_whoami", actor=actor, params={},
                              client_info=_client_info(ctx),
                              fn=lambda d: {"ok": True, "data": _whoami(d, actor)},
                              retryable=True, fn_args=1)
    finally:
        db.close()


# ------------------------------ visit_my_perf ------------------------------

_MY_PERF_VIEWS = ("month", "daily")


def visit_my_perf(ctx: Context, month: str | None = None,
                  view: str = "month") -> dict[str, Any]:
    """我的绩效：view=month 月汇总（默认）/ view=daily 逐日明细。"""
    from mcp_service import my_ops

    params = {"month": month, "view": view}
    if view not in _MY_PERF_VIEWS:
        return _bad_view(view, list(_MY_PERF_VIEWS))
    actor = actor_from_ctx(ctx)

    def run(db):
        pc = _person_code_of(actor)
        if not pc:
            return _unbound_hint()
        try:
            m = _validate_optional_month(month)
        except guards.GuardError as exc:
            return _envelope_error(exc.code, exc.message, exc.hint or "")
        if view == "daily":
            m2 = m or _my_month_latest(db, pc)
            if not m2:
                return {"ok": True, "data": {
                    "month": None, "person_code": pc, "daily": [],
                    "summary": None, "currency": "JPY",
                    "hint": "该员工暂无绩效数据（合法结果，不是错误）"}}
            return {"ok": True, "data": my_ops.my_daily(db, pc, m2)}
        return {"ok": True, "data": my_ops.my_perf(db, pc, month=month)}

    return _read(ctx, "visit_my_perf", params, run)


# ------------------------------ visit_my_pay ------------------------------

def _filter_my_pay(data: dict, month: str) -> dict[str, Any]:
    """只保留目标月的找平/发放/回收轨迹（展示层过滤，不重算业务）。"""
    adj = data["adjusts"]; pays = data["payments"]; links = data["links"]
    adj_un = [a for a in adj["unsettled"] if a["source_month"] == month]
    adj_set = [a for a in adj["settled"] if a["source_month"] == month]
    pays_f = [p for p in pays if p["month"] == month]
    adj_f = adj_un + adj_set
    by_adjust = {aid: [lk for lk in v if lk.get("month") == month]
                 for aid, v in (links.get("by_adjust") or {}).items()
                 if any(a["adjust_id"] == aid and a["source_month"] == month
                        for a in adj_f)}
    by_payment = {pid: [lk for lk in v if lk.get("source_month") == month]
                  for pid, v in (links.get("by_payment") or {}).items()}
    out = {
        "month": month,
        "person_code": data.get("person_code"),
        "name": data.get("name"),
        "currency": "JPY",
        "adjusts": {
            "unsettled": adj_un, "settled": adj_set, "count": len(adj_f),
            "unsettled_count": len(adj_un), "settled_count": len(adj_set),
            "original_total": sum(a["adjust_amount"] or 0 for a in adj_f),
            "settled_total": sum(a["settled_amount"] or 0 for a in adj_f),
            "remaining_total": sum(a["remaining"] or 0 for a in adj_f),
        },
        "payments": pays_f,
        "links": {"by_adjust": by_adjust, "by_payment": by_payment},
        "summary": {
            "remaining_total": sum(a["remaining"] or 0 for a in adj_f),
            "unsettled_count": len(adj_un), "settled_count": len(adj_set),
            "paid_total": sum(p["amount"] or 0 for p in pays_f),
            "adjust_applied_total": sum(p["adjust_applied"] or 0 for p in pays_f),
        },
    }
    if not adj_f and not pays_f:
        out["hint"] = f"{month} 无该员工的找平/发放记录（合法结果，不是错误）"
    return out


def visit_my_pay(ctx: Context, month: str | None = None) -> dict[str, Any]:
    """我的找平与发放：找平进度 + 发放台账 + 回收轨迹（一次给全）。"""
    from mcp_service import my_ops

    params = {"month": month}
    if month is not None and not re.fullmatch(MONTH_PATTERN, str(month).strip() or ""):
        return _envelope_error("BAD_MONTH", f"月份格式非法：{month!r}，应为 YYYY-MM",
                               "月份必须是 YYYY-MM，例如 2026-09")
    actor = actor_from_ctx(ctx)

    def run(db):
        pc = _person_code_of(actor)
        if not pc:
            return _unbound_hint()
        try:
            data = my_ops.my_settlement(db, pc)
        except Exception as exc:  # noqa: BLE001
            return _map_error(exc)
        m = str(month).strip() if month is not None else None
        if m:
            data = _filter_my_pay(data, m)
        return {"ok": True, "data": data}

    return _read(ctx, "visit_my_pay", params, run)


# ===========================================================================
# 管理员侧（13 个）
# ===========================================================================

# ------------------------------ visit_upload ------------------------------

def visit_upload(ctx: Context, filename: str | None = None,
                 content_base64: str | None = None, path: str | None = None,
                 month: str | None = None, kind: str | None = None,
                 dry_run: bool = False) -> dict[str, Any]:
    """统一上传入口：自动识别巡店/对账文件；dry_run=true 只预估不写入。"""
    from mcp_service import write_ops

    def run(db, actor):
        # 授权在参数校验之前（dispatch 内先 enforce）
        if content_base64 and path:
            return _envelope_error(
                "BAD_REQUEST", "path 与 content_base64 不可同时提供",
                "二选一：跨机器用 content_base64；本机路径模式用 path，不要同时传")
        if not content_base64 and not path:
            return _envelope_error(
                "BAD_REQUEST", "必须提供 path 或 content_base64 之一",
                "请把文件内容以 base64 传入（content_base64），"
                "或在本机路径模式下给绝对路径（path）；两者都不可缺失")
        content = (write_ops.decode_base64(content_base64)
                   if content_base64 else None)
        return write_ops.upload_file(db, actor, filename=filename,
                                     content=content, path=path,
                                     month=month, kind=kind, dry_run=dry_run)

    return _write(ctx, "visit_upload",
                  {"filename": filename, "path": path,
                   "has_content": bool(content_base64), "dry_run": dry_run,
                   "month": month, "kind": kind},
                  run, retryable=False)


# ------------------------------ visit_overview ------------------------------

_OVERVIEW_VIEWS = ("summary", "months", "ranking")


def _overview_summary(db, month: str) -> dict[str, Any]:
    """全公司某月总览：多少店/多少点/多少钱/有没有对账。"""
    from app.services import period
    from mcp_service import capability, read_ops

    ms = capability.month_summary(db, month)
    sal = capability.month_salary(db, month)
    return {
        "month": month,
        "formal_rows": ms["formal_rows"],
        "points_total": ms["points_total"],
        "p1_count": ms["p1_count"],
        "p2_count": ms["p2_count"],
        "persons": ms["persons"],
        "total_salary": sal["total_salary"],
        "has_recon": period.month_has_recon(db, month),
        "currency": "JPY",
        "hint": "summary=某月正式表行数/总点数/人数/工资总额/是否已对账；"
                "更细的看板指标与排行用 view='ranking' 或 visit_person 逐人查看",
    }


def visit_overview(ctx: Context, month: str | None = None,
                   view: str = "summary", limit: int = 10) -> dict[str, Any]:
    """公司级总览：view=summary 某月汇总（默认）/ months 有哪些月 / ranking 排行。"""
    from mcp_service import capability, read_ops

    params = {"month": month, "view": view, "limit": limit}
    if view not in _OVERVIEW_VIEWS:
        return _bad_view(view, list(_OVERVIEW_VIEWS))

    def run(db):
        if view == "months":
            return read_ops.list_months(db)
        if month is None or not re.fullmatch(MONTH_PATTERN, str(month).strip() or ""):
            raise guards.GuardError("BAD_MONTH", f"月份格式非法：{month!r}，应为 YYYY-MM",
                                    "view='summary'/'ranking' 需要 month（YYYY-MM）")
        m = str(month).strip()
        if view == "ranking":
            return read_ops.perf_ranking(db, m, limit=limit)
        return _overview_summary(db, m)

    return _read(ctx, "visit_overview", params, run)


# ------------------------------ visit_person ------------------------------

def _latest_month_with_data(db) -> str | None:
    """系统最新有数据的月（月绩效优先，正式表兜底）。"""
    from sqlalchemy import func
    from app.models import FormalRecord, MonthPerfRecord
    m1 = db.query(func.max(MonthPerfRecord.month)).scalar()
    m2 = str(db.query(func.max(FormalRecord.japan_date)).scalar() or "")[:7] or None
    months = [x for x in (m1, m2) if x]
    return max(months) if months else None


def _person_view(db, person: str, month: str | None) -> dict[str, Any]:
    """某人某月：月汇总（含工资/对账偏差）+ 逐日明细。"""
    from app.models import MonthPerfRecord
    from mcp_service import read_ops

    person = (person or "").strip()
    if not person:
        raise guards.GuardError("BAD_PARAM", "person 不能为空",
                                "传工号（精确）或姓名（包含），如 P001 或 张三")
    m = str(month).strip() if month else None
    if not m:
        m = _latest_month_with_data(db)
    if not m:
        return {"month": None, "person_code": None, "name": None,
                "summary": None, "daily": [], "currency": "JPY",
                "hint": "系统尚无任何月份数据（合法结果，不是错误）"}
    if not re.fullmatch(MONTH_PATTERN, m):
        raise guards.GuardError("BAD_MONTH", f"月份格式非法：{m!r}，应为 YYYY-MM",
                                "月份必须是 YYYY-MM，例如 2026-09")

    detail = read_ops.person_detail(db, m, person)   # 找不到 → NotFound → NOT_FOUND
    mp = (db.query(MonthPerfRecord).filter(
        MonthPerfRecord.month == m,
        MonthPerfRecord.person_code == detail["person_code"]).first())
    summary = dict(detail["summary"])
    if mp is not None:
        summary["settle_points"] = mp.settle_points or 0
        summary["settle_amount"] = mp.settle_amount or 0
        summary["diff_points"] = mp.diff_points or 0
        summary["diff_amount"] = mp.diff_amount or 0
    return {"month": m, "person_code": detail["person_code"],
            "name": detail["name"], "summary": summary,
            "daily": detail["daily"], "currency": "JPY",
            "hint": "看全公司某月用 visit_overview(view='summary')；"
                    "看我自己的用 visit_my_perf（员工）"}


def visit_person(ctx: Context, person: str | None = None,
                 name: str | None = None,
                 month: str | None = None) -> dict[str, Any]:
    """某个人：月汇总（点数/工资/对账偏差）+ 逐日明细。person 或 name 二选一。"""
    params = {"person": person, "name": name, "month": month}

    def run(db):
        key = (person or "").strip() or (name or "").strip()
        return _person_view(db, key, month)

    return _read(ctx, "visit_person", params, run)


# ------------------------------ visit_payroll ------------------------------

_PAYROLL_VIEWS = ("rows", "adjusts", "trace")


def visit_payroll(ctx: Context, month: str,
                  view: str = "rows") -> dict[str, Any]:
    """薪资找平：view=rows 两期明细（默认）/ adjusts 找平状态 / trace 双向轨迹。"""
    from mcp_service import read_ops, recon_ops

    params = {"month": month, "view": view}
    if view not in _PAYROLL_VIEWS:
        return _bad_view(view, list(_PAYROLL_VIEWS))

    def run(db):
        guards.validate_month(month)
        if view == "rows":
            return read_ops.payroll_rows(db, month)
        if view == "adjusts":
            data = recon_ops._recon_adjust_state(db, month)
            data["settlement"] = recon_ops._recon_settlement(db, month)
            return data
        return recon_ops._settlement_trace(db, month=month)

    return _read(ctx, "visit_payroll", params, run)


# ------------------------------ visit_payroll_export ------------------------------

def _payroll_export(db, actor, *, month: str, seq: int,
                    dry_run: bool) -> dict[str, Any]:
    """导出发薪表（写）：导出即登记该期已发；dry_run=true 只算金额不登记。"""
    from app.services import period
    from mcp_service import export_ops

    month = guards.validate_month(month)
    if seq not in (1, 2):
        raise guards.GuardError("BAD_PARAM", f"seq 只能是 1 或 2，收到 {seq!r}",
                                "seq=1 上半月发薪表；seq=2 下半月发薪表")
    half = "half1" if seq == 1 else "half2"
    res = export_ops._run_export(db, "salary", month=month, period=half)
    if not res.get("ok"):
        return res
    d = res["data"]
    if dry_run:
        from app.models import PayrollPeriodRow
        col = "half1_amount" if seq == 1 else "half2_amount"
        rows = db.query(PayrollPeriodRow).filter(
            PayrollPeriodRow.month == month).all()
        d["amount_summary"] = {
            "month": month, "seq": seq,
            "total_amount": sum((getattr(r, col) or 0) for r in rows),
            "persons": len(rows), "currency": "JPY",
            "note": "按 payroll_period_rows 该期应发金额合计；dry_run=true 未登记发放"}
        d["ledger"] = {"seq": seq, "registered": 0,
                       "note": "dry_run=true：只算金额与生成文件，**未登记发放**；"
                               "确认后用 dry_run=false 正式导出（导出即登记该期已发）"}
    else:
        n = period.register_exported_half(db, month, seq, paid_by=actor.uid)
        d["ledger"] = {"seq": seq, "registered": n,
                       "note": "导出发薪表即视为该期已发薪（台账快照，同期再次导出不覆盖）"}
    return res


def visit_payroll_export(ctx: Context, month: str, seq: int,
                         dry_run: bool = False) -> dict[str, Any]:
    """导出发薪表 Excel（写）：seq=1 上半月 / 2 下半月；导出=登记已发。"""
    params = {"month": month, "seq": seq, "dry_run": dry_run}

    def run(db, actor):
        return _payroll_export(db, actor, month=month, seq=seq, dry_run=dry_run)

    return _write(ctx, "visit_payroll_export", params, run, retryable=True)


# ------------------------------ visit_recon ------------------------------

_RECON_VIEWS = ("status", "diff", "interpret")


def _recon_interpret_read(db, task_id: int) -> dict[str, Any]:
    """读某对账任务已生成的 AI 解读（只读展示，不触发 AI）。"""
    from app.models import ReconTask
    from mcp_service import recon_ops

    t = db.get(ReconTask, task_id)
    if t is None:
        raise recon_ops.ReconError("NOT_FOUND", f"对账任务不存在：{task_id}",
                                   "请先用 visit_recon(view='status') 确认 task_id")
    ai = (t.summary or {}).get("ai_interpret") or {}
    text = (ai.get("text") or "") if isinstance(ai, dict) else ""
    data = {"task_id": task_id, "month": (t.params or {}).get("month", ""),
            "has_interpretation": bool(text), "interpretation": text or None,
            "model": (ai.get("model") if isinstance(ai, dict) else None),
            "generated_at": _iso(ai.get("at")) if isinstance(ai, dict) else None}
    if not text:
        data["hint"] = "该任务尚无 AI 解读（未生成或解读失败）；可查看报告或人工核对差异"
    return data


def visit_recon(ctx: Context, month: str | None = None,
                task_id: int | None = None,
                view: str = "status") -> dict[str, Any]:
    """对账：view=status 任务列表 / diff 差异明细 / interpret 已有 AI 解读。"""
    from mcp_service import recon_ops

    params = {"month": month, "task_id": task_id, "view": view}
    if view not in _RECON_VIEWS:
        return _bad_view(view, list(_RECON_VIEWS))

    def run(db):
        if view == "status":
            return recon_ops._recon_status(db, month)
        if view == "diff":
            return recon_ops._recon_diff(db, task_id=task_id, month=month)
        # interpret：读已有 AI 解读（task_id 或 month 解析当前任务）
        tid = task_id
        if tid is None:
            if month is None:
                raise guards.GuardError(
                    "BAD_PARAM", "interpret 需要 task_id（或 month）",
                    "task_id=精确对账任务；month=该月当前版本任务，二选一")
            t = recon_ops._current_task_for_month(db, str(month).strip())
            if t is None:
                raise recon_ops.ReconError(
                    "NOT_FOUND", f"{month} 无当前对账任务",
                    "请先用 visit_recon(view='status') 查看任务")
            tid = t.id
        return _recon_interpret_read(db, tid)

    return _read(ctx, "visit_recon", params, run)


# ------------------------------ visit_recon_export ------------------------------

_RECON_EXPORT_KINDS = ("report", "detail", "diff")


def _resolve_task_id(db, task_id: int | None, month: str | None) -> int:
    from mcp_service import recon_ops
    if task_id is not None:
        return int(task_id)
    if month and str(month).strip():
        m = str(month).strip()
        if not re.fullmatch(MONTH_PATTERN, m):
            raise guards.GuardError("BAD_MONTH", f"月份格式非法：{m!r}，应为 YYYY-MM",
                                    "月份必须是 YYYY-MM，例如 2026-09")
        t = recon_ops._current_task_for_month(db, m)
        if t is None:
            raise recon_ops.ReconError("NOT_FOUND", f"{m} 无当前对账任务",
                                       "请先用 visit_recon(view='status') 查看任务")
        return t.id
    raise guards.GuardError("BAD_PARAM", "必须提供 task_id 或 month 之一",
                            "task_id=精确任务；month=该月当前版本任务，二选一")


def visit_recon_export(ctx: Context, month: str | None = None,
                       task_id: int | None = None,
                       kind: str = "report") -> dict[str, Any]:
    """导出对账文件：kind=report 月度报告 / detail 原始产物 / diff 差异清单。"""
    from mcp_service import export_ops, misc_ops

    params = {"month": month, "task_id": task_id, "kind": kind}
    if kind not in _RECON_EXPORT_KINDS:
        return _envelope_error(
            "BAD_PARAM", f"未知 kind：{kind!r}",
            "可选 kind：" + "、".join(_RECON_EXPORT_KINDS) + f"（收到 {kind!r}）")

    def run(db):
        tid = _resolve_task_id(db, task_id, month)
        if kind == "report":
            return export_ops._run_export(db, "recon_report", task_id=tid)
        if kind == "diff":
            return export_ops._run_export(db, "recon_diff", task_id=tid)
        return {"ok": True, "data": misc_ops.export_recon_result(db, tid)}

    return _read(ctx, "visit_recon_export", params, run)


# ------------------------------ visit_files ------------------------------

_FILES_VIEWS = ("list", "layout", "report", "tasks")


def visit_files(ctx: Context, view: str = "list",
                import_id: int | None = None, month: str | None = None,
                kind: str | None = None, status: str | None = None) -> dict[str, Any]:
    """文件与任务：view=list 文件列表 / layout 解析布局 / report 判定明细 / tasks 任务列表。"""
    from mcp_service import misc_ops, read_ops

    params = {"view": view, "import_id": import_id, "month": month,
              "kind": kind, "status": status}
    if view not in _FILES_VIEWS:
        return _bad_view(view, list(_FILES_VIEWS))

    def run(db):
        if view == "layout":
            if import_id is None:
                raise guards.GuardError("BAD_PARAM", "view='layout' 需要 import_id",
                                        "传文件 id（可用 view='list' 查询）")
            return misc_ops.file_layout(db, import_id)
        if view == "report":
            if import_id is None:
                raise guards.GuardError("BAD_PARAM", "view='report' 需要 import_id",
                                        "传文件 id（可用 view='list' 查询）")
            return read_ops.file_report(db, import_id, bucket=None)
        if view == "tasks":
            return read_ops.list_tasks(db, month=month, kind=kind, status=status)
        return read_ops.file_list(db, month=month)

    return _read(ctx, "visit_files", params, run)


# ------------------------------ visit_rebuild ------------------------------

_REBUILD_ACTIONS = ("preview", "run")


def visit_rebuild(ctx: Context, month: str, action: str = "preview",
                  preview_id: int | None = None,
                  confirm_text: str | None = None) -> dict[str, Any]:
    """月度重算：action=preview 只读预演（拿 preview_id）/ run 正式重算（需确认语）。"""
    from mcp_service import write_tools

    params = {"month": month, "action": action, "preview_id": preview_id}
    if action not in _REBUILD_ACTIONS:
        return _bad_action(action, list(_REBUILD_ACTIONS))

    def run(db, actor):
        if action == "preview":
            return write_tools.rebuild_preview(db, actor, month=month)
        return write_tools.rebuild_month(db, actor, month=month,
                                         preview_id=preview_id,
                                         confirm_text=confirm_text)

    return _write(ctx, "visit_rebuild", params, run,
                  retryable=(action == "preview"))


# ------------------------------ visit_staff ------------------------------

_STAFF_ACTIONS = ("set_status",)


def _resolve_user_id(db, username: str | None) -> int:
    from app.models import User
    u = db.query(User).filter(User.username == (username or "").strip()).first()
    if u is None:
        raise guards.GuardError(
            "NOT_FOUND", f"员工账号不存在：{username!r}",
            "请先用 visit_staff(view='list') 确认正确的用户名")
    return u.id


def visit_staff(ctx: Context, view: str | None = None,
                action: str | None = None, username: str | None = None,
                status: str | None = None,
                confirm_text: str | None = None) -> dict[str, Any]:
    """员工账号：view=list 列表（默认）/ action=set_status 改状态（写，需确认语）。"""
    from mcp_service import misc_ops, read_ops

    params = {"view": view, "action": action, "username": username,
              "status": status}
    if view is None and action is None:
        view = "list"
    if action is not None and action not in _STAFF_ACTIONS:
        return _bad_action(action, list(_STAFF_ACTIONS))

    if action == "set_status":
        def run(db, actor):
            return misc_ops.staff_set_status(
                db, actor, user_id=_resolve_user_id(db, username),
                new_status=status, confirm_text=confirm_text)
        return _write(ctx, "visit_staff", params, run, retryable=True)

    def run(db):
        return read_ops.staff_list(db, status=status)

    return _read(ctx, "visit_staff", params, run)


# ------------------------------ visit_config ------------------------------

_CONFIG_ACTIONS = ("set",)


def visit_config(ctx: Context, view: str | None = None,
                 action: str | None = None,
                 per_point: int | None = None,
                 bonus_group: int | None = None,
                 bonus_amount: int | None = None,
                 staff_visible_from: str | None = None,
                 confirm_text: str | None = None) -> dict[str, Any]:
    """系统配置：view=get 当前配置（默认）/ action=set 保存配置（写，需确认语）。"""
    from mcp_service import payroll_write_ops, read_ops

    params = {"view": view, "action": action, "per_point": per_point,
              "bonus_group": bonus_group, "bonus_amount": bonus_amount,
              "staff_visible_from": staff_visible_from}
    if view is None and action is None:
        view = "get"
    if action is not None and action not in _CONFIG_ACTIONS:
        return _bad_action(action, list(_CONFIG_ACTIONS))

    if action == "set":
        def run(db, actor):
            return payroll_write_ops.config_set(
                db, actor, per_point=per_point, bonus_group=bonus_group,
                bonus_amount=bonus_amount, staff_visible_from=staff_visible_from,
                confirm_text=confirm_text)
        return _write(ctx, "visit_config", params, run, retryable=True)

    def run(db):
        return read_ops.config_get(db)

    return _read(ctx, "visit_config", params, run)


# ------------------------------ visit_store ------------------------------

_STORE_VIEWS = ("search", "pairs")
_STORE_ACTIONS = ("merge", "split", "apply", "skip")


def _entity_summary(e) -> dict[str, Any]:
    """实体摘要（与 read_ops.store_search / store_write_ops 同口径字段）。"""
    return {
        "id": e.id,
        "store_id_raw": e.store_id_raw,
        "name_local": e.name_local or "",
        "name_norm": e.name_norm or "",
        "city": e.city or "",
        "address_local": e.address_local or "",
        "master_id": e.master_id,
        "is_master": e.master_id == e.id,
        "master_store_id": e.master_store_id,
    }


def _store_pairs(db, kind: str | None) -> dict[str, Any]:
    """候选对列表（复用 store_master._pending_pairs 的过滤口径，只做展示格式化）。"""
    from app.services import store_master

    kinds = [kind] if kind else ["exact", "fuzzy"]
    rows = []
    for k in kinds:
        if k not in ("exact", "fuzzy"):
            raise guards.GuardError("BAD_PARAM", f"kind 只能是 exact/fuzzy，收到 {kind!r}",
                                    "候选对类型：exact=归一化全等；fuzzy=高相似（人工/AI 判定）")
        for item in store_master._pending_pairs(db, k):
            rows.append({
                "pair_id": item["pair"].id,
                "kind": item["pair"].kind,
                "status": item["pair"].status,
                "entity_a": _entity_summary(item["a"]),
                "entity_b": _entity_summary(item["b"]),
            })
    data = {"pairs": rows, "total": len(rows)}
    if not rows:
        data["hint"] = "当前无待处理候选对（合法结果，不是错误）"
    return data


def visit_store(ctx: Context, view: str | None = None,
                action: str | None = None,
                q: str | None = None, limit: int = 20,
                pair_id: int | None = None, keep: int | None = None,
                entity_id: int | None = None, kind: str | None = None,
                note: str | None = None,
                confirm_text: str | None = None) -> dict[str, Any]:
    """店铺主档：view=search 检索 / pairs 候选对；action=merge|split|apply|skip（写）。"""
    from mcp_service import read_ops, store_write_ops

    params = {"view": view, "action": action, "q": q, "limit": limit,
              "pair_id": pair_id, "keep": keep, "entity_id": entity_id,
              "kind": kind, "note": note}
    if view is None and action is None:
        view = "search"
    if view is not None and view not in _STORE_VIEWS:
        return _bad_view(view, list(_STORE_VIEWS))
    if action is not None and action not in _STORE_ACTIONS:
        return _bad_action(action, list(_STORE_ACTIONS))

    if action == "merge":
        if pair_id is None or keep is None:
            return _envelope_error("BAD_PARAM", "merge 需要 pair_id 与 keep",
                                   "pair_id=候选对 id；keep=候选对中要保留的实体 id"
                                   "（可用 view='pairs' 查看）")
        def run_merge(db, actor):
            return store_write_ops.merge_pair(db, actor, pair_id=pair_id,
                                              keep=keep, kind=kind, note=note)
        return _write(ctx, "visit_store", params, run_merge, retryable=False)

    if action == "split":
        if entity_id is None:
            return _envelope_error("BAD_PARAM", "split 需要 entity_id",
                                   "entity_id=要拆回自己为主档的实体 id")
        def run_split(db, actor):
            return store_write_ops.split_entity(db, actor, entity_id=entity_id,
                                                confirm_text=confirm_text)
        return _write(ctx, "visit_store", params, run_split, retryable=False)

    if action == "apply":
        def run_apply(db, actor):
            return store_write_ops.apply_all(db, actor, confirm_text=confirm_text,
                                             kind=kind)
        return _write(ctx, "visit_store", params, run_apply, retryable=False)

    if action == "skip":
        if pair_id is None:
            return _envelope_error("BAD_PARAM", "skip 需要 pair_id",
                                   "pair_id=候选对 id（可用 view='pairs' 查看）")
        def run_skip(db, actor):
            return store_write_ops.skip_pair(db, actor, pair_id=pair_id, kind=kind)
        return _write(ctx, "visit_store", params, run_skip, retryable=False)

    def run(db):
        if view == "pairs":
            return _store_pairs(db, kind)
        return read_ops.store_search(db, q or "", limit=limit)

    return _read(ctx, "visit_store", params, run)


# ------------------------------ visit_verify ------------------------------

def visit_verify(ctx: Context) -> dict[str, Any]:
    """数据自洽检查（只读）：找平表/台账/关联/找平行 四表互相印证。"""
    from mcp_service import read_ops

    def run(db):
        # integrity.check_all 返回 {"ok", "checks", "summary"}；统一包成 data
        res = read_ops.verify_integrity(db)
        return {"ok": True, "data": res}

    return _read(ctx, "visit_verify", {}, run)


# ===========================================================================
# 注册（唯一注册入口；旧工具注册已全部移除）
# ===========================================================================

# 各工具描述（一句话做什么 + 什么时候用我 + view/action 各回答什么问题 + 关键约束）
_DESC = {
    "visit_whoami": (
        "**我是谁**：返回当前调用身份——账号、显示名、角色（管理员/员工）、绑定的人员编号与姓名、"
        "权限范围、以及可用能力清单。"
        "什么时候用我：用户问「我是谁」「我能做什么」「为什么查不到别人的数据」时用我；"
        "也可在操作前确认当前身份。只读、无参数。"
    ),
    "visit_my_perf": (
        "查询**我自己**（当前 Token 绑定的人员）的绩效："
        "view=month 月汇总（默认：有效店数/1点/2点/总点数/工资円/单价/对账偏差）；"
        "view=daily 逐日明细（日期/店数/点数）。month 可选（YYYY-MM），不传=最新有数据的月。"
        "什么时候用我：员工问「我这个月多少分/多少钱」「我某天巡了几家店」；"
        "管理员查公司数据用 visit_overview / visit_person。"
        "**只返回本人数据，无 person 参数**；金额为日元（currency=JPY）。只读。"
    ),
    "visit_my_pay": (
        "查询**我自己**的找平与发放（一次给全）：找平进度（payroll_adjusts：原始/已找平/剩余/状态）、"
        "发放台账（payroll_payments：各期实发金额/点数/奖金/抵扣）、回收轨迹"
        "（payroll_settlement_links：每笔找平被哪几期发放冲抵、每笔发放冲了哪几笔找平）。"
        "month 可选（YYYY-MM），不传=全部月份。"
        "什么时候用我：员工问「我上月的差额扣了吗」「我这个月发了多少钱」；"
        "管理员查某月公司级找平用 visit_payroll。"
        "**只返回本人数据，无 person 参数**；不给他人/公司级数据（用户问公司级数据时告知无权限）。"
        "金额为日元（currency=JPY）。只读。"
    ),
    "visit_upload": (
        "**统一上传入口**：把巡店/对账文件丢进来，自动识别类型并走对应通道，无需自己判断。"
        "必须提供 path 或 content_base64 之一（不可同时、不可都不）。"
        "识别：① 巡店记录（MarsNavi STORE VISIT RECORD）→ 解析→判定→入正式表→工资/找平/看板全自动；"
        "② 对账明细（含 Statement Date/Agent Name 等列）→ 对账任务（月份可从文件推断，也可用 month 指定）；"
        "③ 手工结算对照件 → 明确提示不入库；④ 识别不出 → 返回 NEED_FILE_KIND，"
        "**必须询问用户**后带 kind 重传（kind='daily_records'/'recon'），不要自行猜测。"
        "什么时候用我：任何文件（巡店记录/对账明细）要进系统时用我；"
        "只想看已上传的文件列表用 visit_files(view='list')。"
        "dry_run=true（推荐先试）：只识别+推断月份+查将替换的对账任务，**不写入**。"
        "同月重传对账会「覆盖」上一版本（旧任务 replaced）；字节相同的文件返回 DUPLICATE_FILE。"
        "封账月份被拒（MONTH_SEALED）。需要写权限 Token；金额/月份：JPY / YYYY-MM。"
    ),
    "visit_overview": (
        "公司级总览（管理员）。view=summary 某月汇总（默认，month 必填：多少店/多少点/多少人/工资总额円/有没有对账）；"
        "view=months 系统有哪几个月的结算数据（回答「有哪些月份」）；"
        "view=ranking 绩效排行（month 必填，limit 控制条数，回答「谁多谁少」）。"
        "什么时候用我：看**全公司某月** → visit_overview；看**某个人** → visit_person；"
        "看**我自己的** → visit_my_perf（员工）/ visit_person（管理员）。"
        "金额为日元（currency=JPY）；月份 YYYY-MM。只读。"
    ),
    "visit_person": (
        "查**某个人**（管理员）：月汇总（有效店/1点/2点/总点数/工资円/单价/对账偏差）+ 逐日明细。"
        "person 或 name 二选一（工号精确/姓名包含）；month 可选（YYYY-MM），不传=最新有数据的月。"
        "什么时候用我：用户点名要某人的绩效明细；看全公司用 visit_overview；看自己用 visit_my_perf。"
        "找不到人返回 NOT_FOUND。金额为日元（currency=JPY）。只读。"
    ),
    "visit_payroll": (
        "薪资找平（管理员）。view=rows 两期明细（默认：上半月/下半月点数、金额、奖金、找平与递延 carry，month 必填）；"
        "view=adjusts 找平状态（每人已确认找平记录+偏差金额+结清状态）；"
        "view=trace 找平↔发放双向轨迹（谁欠谁、还了多少、从哪期扣的，可按月筛）。"
        "什么时候用我：核对「某月发薪/找平/递延」用我；绩效维度用 visit_overview / visit_person；"
        "员工查自己的用 visit_my_pay。"
        "金额为日元（currency=JPY，负=扣款/正=补款）；月份 YYYY-MM。只读。"
    ),
    "visit_payroll_export": (
        "导出发薪表 Excel（写，发薪用）：seq=1 上半月 / seq=2 下半月。"
        "**导出发薪表即登记该期已发**（台账快照，同期再次导出不覆盖）——只想核对金额时**务必**先 "
        "dry_run=true（只算金额、生成文件、**未登记发放**），确认后再正式导出。"
        "什么时候用我：每月发薪前要导出给财务、或核对某期应发金额时用我；"
        "只看金额与两期明细用 visit_payroll(view='rows')（只读，不登记）。"
        "返回 content_base64（解码保存为 filename 即得 Excel）+ saved_path。"
        "该月无发薪数据返回 NOT_FOUND。需要写权限 Token；月份 YYYY-MM；金额日元（JPY）。"
    ),
    "visit_recon": (
        "对账（管理员）。view=status 对账任务列表（默认：任务 id/月份/状态/是否上一版/差异人数/差异金额）；"
        "view=diff 差异明细（task_id 或 month 二选一：人月差异/员工×日问题行/反向名单/对账侧汇总）；"
        "view=interpret 读某任务的**已有** AI 解读（task_id 或 month；只读展示，不触发 AI）。"
        "什么时候用我：核对「对账对到哪一步、差异是什么、AI 怎么说」用我；"
        "导出对账文件用 visit_recon_export；找平结清用 visit_payroll(view='adjusts')。"
        "金额为日元（currency=JPY）。只读。"
    ),
    "visit_recon_export": (
        "导出对账文件（管理员，只读导出）。kind=report 月度对账报告 / detail 对账原始产物 / diff 差异清单。"
        "task_id 或 month 二选一（month=该月当前版本任务）。"
        "什么时候用我：要把对账结果/报告发给别人时用我；只看不下载用 visit_recon。"
        "返回 content_base64（解码保存为 filename 即得 Excel）+ saved_path。"
        "任务不存在/未完成返回 NOT_FOUND。月份 YYYY-MM。"
    ),
    "visit_files": (
        "文件与任务（管理员）。view=list 巡店导入文件列表（默认，month 可选：每个文件的判定分类计数/是否入正式表）；"
        "view=layout 某文件解析布局（import_id 必填：表头行/列映射/点数规则/解析诊断，排查解析失败用）；"
        "view=report 某文件判定明细（import_id 必填：分桶计数+抽样，核对判定分布用）；"
        "view=tasks 统一任务列表（巡店导入+对账，可按 kind/month/status 过滤）。"
        "什么时候用我：查「这个月上传过哪些文件、解析得对不对、有哪些任务」用我。只读。"
    ),
    "visit_rebuild": (
        "月度重算（写）。action=preview 只读预演（默认：估算正式表现状/raw 判定分布/点数影响面，返回 preview_id，30 分钟有效）；"
        "action=run 正式重算（需 preview_id + 确认语 `确认重算 {month}`；重判+翻 from_sub+重建正式表+同步找平/看板）。"
        "什么时候用我：同月补传新文件后要「收敛口径」、或要按最新规则重算某月时用我。"
        "**执行 run 前必须**先 preview 并把影响面复述给用户。重算非幂等，意外失败返回 INTERNAL_WRITE、"
        "**不要自动重试**。需要写权限 Token；月份 YYYY-MM。"
    ),
    "visit_staff": (
        "员工账号（管理员）。view=list 账号列表（默认：用户名/角色/状态/是否绑定人员/最近登录，可按 status 过滤）；"
        "action=set_status 修改账号状态（写：active 在岗/leave 请假/disabled 停用/resigned 离职，"
        "需 username+status+确认语 `确认改状态 {username} {status}`，停用/离职即禁登录，数据永不删除）。"
        "什么时候用我：账号盘点/员工请假离职要停用账号时用我。只读 view 无需确认语。"
    ),
    "visit_config": (
        "系统配置（管理员）。view=get 当前生效配置（默认：每点单价円/奖金门槛/奖额/员工可见起始月）；"
        "action=set 保存配置（写，需确认语 `确认修改配置`；per_point/bonus_group/bonus_amount/"
        "staff_visible_from 参数留空=保留当前值；**全局单值，影响所有月份工资口径**）。"
        "什么时候用我：回答「现在每点多少钱/奖金怎么算」或要改配置时用我。金额为日元（JPY）。"
    ),
    "visit_store": (
        "店铺主档（管理员）。view=search 检索店铺（默认：按编号/名称/规范化名/城市模糊匹配）；"
        "view=pairs 待处理候选对列表（exact=归一化全等 / fuzzy=高相似，kind 可选过滤）；"
        "action=merge 合并一对（pair_id+keep=保留哪个实体 id）；action=split 拆分被误合并的实体"
        "（entity_id+确认语 `确认拆分 {entity_id}`）；action=apply 整批应用推荐合并"
        "（确认语 `确认批量应用合并`）；action=skip 标记候选对为不同店（pair_id）。"
        "什么时候用我：排查/修正「同店不同写法各算一家店」或店铺主档归并时用我。"
        "写 action 需确认语（影响历史统计口径，执行前先向用户说明）；意外失败 INTERNAL_WRITE 勿自动重试。"
    ),
    "visit_verify": (
        "数据自洽检查（只读，无参数）：找平表/台账/关联表/找平行四类数字互相印证"
        "（8 项：找平金额=该月差异 / 已找平+剩余=原始 / 状态一致 / 关联=台账抵扣 / 关联=已找平 / "
        "台账快照vs应发（提示非错误）/ 抵扣来源可反查 / 递延链符号一致）。"
        "什么时候用我：怀疑数据不一致、上线/重算后核对系统算得对不对时用我。"
        "返回 checks[]（pass/fail/info + 反例明细）与 summary（通过数/失败数/规模）。"
    ),
}

# 写工具：readOnlyHint=false（规格 §4.2）
_WRITE_TOOLS = {"visit_upload", "visit_payroll_export", "visit_rebuild",
                "visit_staff", "visit_config", "visit_store",
                # 作业域写工具（2026-10-06）
                "visit_self_report", "visit_task_report", "visit_task_assign",
                "visit_task_confirm", "visit_task_return", "visit_task_transfer"}

_WRITE_TITLES = {
    # 作业域（团队 / 车站 / 任务）
    "visit_self_report": "提交今日自报（点数+进度）",
    "visit_task_report": "上报任务进展",
    "visit_task_assign": "派工 / 改派 / 回收",
    "visit_task_confirm": "确认 / 一键全确认 / 驳回",
    "visit_task_return": "撤回任务到车站池",
    "visit_task_transfer": "转给别的队（队长间换活）",
    "visit_upload": "上传巡店/对账文件",
    "visit_payroll_export": "导出发薪表",
    "visit_rebuild": "月度重算",
    "visit_staff": "员工账号管理",
    "visit_config": "系统配置",
    "visit_store": "店铺主档管理",
}

_READ_TITLES = {
    # 作业域（团队 / 车站 / 任务）
    "visit_my_tasks": "我的任务（含自动延续）",
    "visit_team_tasks": "本队任务 / 待确认",
    "visit_task_board": "任务总表（管理员）",
    "visit_whoami": "我是谁（身份与权限）",
    "visit_my_perf": "我的绩效",
    "visit_my_pay": "我的找平与发放",
    "visit_overview": "公司级总览",
    "visit_person": "个人绩效明细",
    "visit_payroll": "薪资找平",
    "visit_recon": "对账查询",
    "visit_recon_export": "导出对账文件",
    "visit_files": "文件与任务",
    "visit_verify": "数据自洽检查",
}


def _annotations(name: str):
    """按工具读/写属性生成注解（写工具 readOnlyHint=false）。"""
    if name in _WRITE_TOOLS:
        return write_ann(_WRITE_TITLES[name], idempotent=False)
    return read_ann(_READ_TITLES[name])


# ---------------- 作业域（团队 / 车站 / 任务）----------------
# 口径与 Web 端**同一套服务层**（app/services/bd_tasks.py 等），不另写一套。
# 详见 docs/任务域-全流程.md；MCP 侧能力层在 mcp_service/task_ops.py。

def _task_write(ctx: Context, tool: str, params: dict, fn):
    """作业域写工具统一入口（提交/回滚在 fn 里做）。"""
    def _mapped(db, actor):
        from mcp_service import task_ops
        try:
            data = fn(db, actor, task_ops)
            db.commit()
            return {"ok": True, "data": data}
        except Exception as exc:                       # noqa: BLE001
            db.rollback()
            from app.services.bd_tasks import TaskError
            if isinstance(exc, (TaskError, ValueError)):
                return _envelope_error("BAD_PARAM", str(exc),
                                       "请检查参数或权限后重试", retryable=False)
            raise
    return _write(ctx, tool, params, _mapped, retryable=False)


def visit_my_tasks(ctx: Context) -> dict[str, Any]:
    """**我的任务**：今天派给我的 ∪ 之前派给我但还没做完的（自动延续）+ 今天已填点数。"""
    from mcp_service import task_ops
    actor = actor_from_ctx(ctx)
    return _read(ctx, "visit_my_tasks", {}, lambda db: _my_tasks_run(db, actor, task_ops))


def _my_tasks_run(db, actor, task_ops) -> dict[str, Any]:
    pc = _person_code_of(actor)
    if not pc:
        return _unbound_hint()
    return {"ok": True, "data": task_ops.my_tasks(db, pc)}


def visit_self_report(ctx: Context, area: str = "", p1_cnt: int = 0,
                      p2_cnt: int = 0,
                      items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """**提交今日自报**：点数（1点/2点店数）+ 当天若干任务的进度，**一次提交同一事务**。

    items = [{"task_id": 123, "pct": 40, "note": "可空"}, ...]；
    进度没变又没写备注的会被跳过（不会产生多余待确认）。
    """
    params = {"area": area, "p1_cnt": p1_cnt, "p2_cnt": p2_cnt,
              "items": items or []}
    return _task_write(ctx, "visit_self_report", params,
                       lambda db, actor, ops: ops.self_report(
                           db, actor, area=area, p1_cnt=p1_cnt, p2_cnt=p2_cnt,
                           items=items or []))


def visit_task_report(ctx: Context, task_id: int, pct: int,
                      note: str = "") -> dict[str, Any]:
    """**上报单条任务进展**（0–100）。队员报自己的、队长调整本队的都走它。

    - 队长**随时**可以调本队任何任务的进度（不受"确认后锁定"限制）
    - **未分配的任务**：队长直接 `pct=100` 就是"标识完成"（不用先派人）
    - 队员那边：队长确认过的**当天**那条会被锁住
    """
    params = {"task_id": task_id, "pct": pct, "note": note}
    return _task_write(ctx, "visit_task_report", params,
                       lambda db, actor, ops: ops.report_one(
                           db, actor, task_id=task_id, pct=pct, note=note))


def visit_team_tasks(ctx: Context, tab: str = "", kw: str = "",
                     line_id: int | None = None) -> dict[str, Any]:
    """**本队任务**（队长视角）：tab 计数 + 任务行 + 待确认队列。

    tab：空=全部 / `unassigned`=待派（没分人）/ `doing`=已分人 / `done`=已完成。
    """
    from mcp_service import task_ops
    actor = actor_from_ctx(ctx)
    params = {"tab": tab, "kw": kw, "line_id": line_id}

    def run(db):
        ids = _actor_team_ids(actor, db)
        if not ids:
            return _envelope_error("FORBIDDEN_TOOL", "只有队长能看本队任务",
                                   "该账号不是队长，或还没有带队", retryable=False)
        return {"ok": True, "data": task_ops.team_tasks(db, ids, tab=tab, kw=kw,
                                                        line_id=line_id)}
    return _read(ctx, "visit_team_tasks", params, run)


def _actor_team_ids(actor, db) -> list:
    """当前身份带的队（队长=他带的队；管理员=全部队）。"""
    from app.services import bd_teams
    if actor is None:
        return []
    if getattr(actor, "role", "") == "admin":
        # 管理员：全部队。⚠️ list_teams 的行是 {"team": <BdTeam>, ...}（不是 {"id":…}）
        #    —— 2026-10-06 实测踩到 KeyError('id')
        out = []
        for row in bd_teams.list_teams(db):
            t = row.get("team") if isinstance(row, dict) else None
            tid = getattr(t, "id", None)
            if tid is None and isinstance(row, dict):
                tid = row.get("id")
            if tid is not None:
                out.append(int(tid))
        return out
    return [t.id for t in bd_teams.leader_teams(db, getattr(actor, "person_code", None))]


def visit_task_assign(ctx: Context, task_id: int,
                      person_codes: list[str] | None = None) -> dict[str, Any]:
    """**派工 / 改派 / 回收（空置）**：`person_codes` 传 `[]` 就是回收（进度保留）。"""
    params = {"task_id": task_id, "person_codes": person_codes or []}
    return _task_write(ctx, "visit_task_assign", params,
                       lambda db, actor, ops: ops.assign(
                           db, actor, task_id=task_id,
                           person_codes=person_codes or []))


def visit_task_transfer(ctx: Context, task_ids: list[int],
                        to_team_id: int) -> dict[str, Any]:
    """**转给别的队**（队长之间私下换活）：未分配/进行中都能转，已完成不转。

    只能转**自己当队长**的那个队的任务；转出会移出原担当（**已上报进度保留**），
    并给对方队长与被移出的担当各发一条站内消息。想"互换"就各自转一条。
    """
    params = {"task_ids": task_ids or [], "to_team_id": to_team_id}
    return _task_write(ctx, "visit_task_transfer", params,
                       lambda db, actor, ops: ops.transfer(
                           db, actor, task_ids=task_ids or [],
                           to_team_id=to_team_id))


def visit_task_confirm(ctx: Context, task_id: int | None = None,
                       all_today: bool = False, reject: bool = False,
                       pct: int | None = None, note: str = "") -> dict[str, Any]:
    """**确认 / 一键全确认 / 驳回**（队长）：

    - `task_id` + 默认 → 确认这一条（认可队员上报的原值）
    - `all_today=True` → **一键确认当天全部**待确认
    - `reject=True` + `pct` → 驳回（把 100% 退回成 pct；队员原值保留）
    """
    params = {"task_id": task_id, "all_today": all_today, "reject": reject,
              "pct": pct, "note": note}

    def run(db, actor, ops):
        if reject:
            if not task_id or pct is None:
                raise ValueError("驳回要同时给 task_id 和 pct")
            return ops.reject(db, actor, task_id=task_id, pct=pct, note=note)
        if all_today:
            return ops.confirm_day(db, actor)
        if not task_id:
            raise ValueError("要么给 task_id（确认这一条），要么 all_today=True（全部）")
        return ops.confirm_day(db, actor, task_id=task_id)
    return _task_write(ctx, "visit_task_confirm", params, run)


def visit_task_return(ctx: Context, task_ids: list[int]) -> dict[str, Any]:
    """**撤回任务到车站池**（管理员）：只撤"已派队但没分到人"的（有进展也行）。"""
    params = {"task_ids": task_ids}
    return _task_write(ctx, "visit_task_return", params,
                       lambda db, actor, ops: ops.return_pool(
                           db, actor, task_ids=task_ids))


def visit_task_board(ctx: Context, tab: str = "", line_id: int | None = None,
                     kw: str = "", stale_only: bool = False, page: int = 1,
                     per: int = 20) -> dict[str, Any]:
    """**任务总表**（管理员）：tab 计数 + 任务行 + 按队汇总 + 停滞口径。

    tab：空=全部 / `unassigned`=车站池 / `assigned`=已派队未完成 / `done`=已完成。
    `stale_only=True` → 只看"已分到人但 ≥N 天没提交"的。
    """
    from mcp_service import task_ops
    params = {"tab": tab, "line_id": line_id, "kw": kw,
              "stale_only": stale_only, "page": page, "per": per}
    return _read(ctx, "visit_task_board", params,
                 lambda db: {"ok": True, "data": task_ops.board(
                     db, tab=tab, line_id=line_id, kw=kw,
                     stale_only=stale_only, page=page, per=per)})


def register(mcp: MCPServer) -> None:
    """注册全部场景化工具（唯一注册入口；server.py 经 tools.register 接线）。

    2026-10-06：16 → **24**（新增作业域 8 个：员工 3 + 队长 4 + 管理员 1）。
    """
    from typing import Annotated
    from pydantic import Field

    # 员工侧 3 个
    @mcp.tool(name="visit_whoami", title=_READ_TITLES["visit_whoami"],
              annotations=_annotations("visit_whoami"), description=_DESC["visit_whoami"])
    def _t_whoami(ctx: Context) -> dict[str, Any]:
        return visit_whoami(ctx)

    @mcp.tool(name="visit_my_perf", title=_READ_TITLES["visit_my_perf"],
              annotations=_annotations("visit_my_perf"), description=_DESC["visit_my_perf"])
    def _t_my_perf(ctx: Context,
                   month: Annotated[str | None, Field(pattern=MONTH_PATTERN)] = None,
                   view: str = "month") -> dict[str, Any]:
        return visit_my_perf(ctx, month=month, view=view)

    @mcp.tool(name="visit_my_pay", title=_READ_TITLES["visit_my_pay"],
              annotations=_annotations("visit_my_pay"), description=_DESC["visit_my_pay"])
    def _t_my_pay(ctx: Context,
                  month: Annotated[str | None, Field(pattern=MONTH_PATTERN)] = None
                  ) -> dict[str, Any]:
        return visit_my_pay(ctx, month=month)

    # 作业域 8 个（员工 3 + 队长 4 + 管理员 1；授权矩阵见 mcp_service/authz.py）
    @mcp.tool(name="visit_my_tasks", title=_READ_TITLES["visit_my_tasks"],
              annotations=_annotations("visit_my_tasks"),
              description="我的任务（今天派的 ∪ 没做完自动延续的）+ 今天已填点数")
    def _t_my_tasks(ctx: Context) -> dict[str, Any]:
        return visit_my_tasks(ctx)

    @mcp.tool(name="visit_self_report", title=_WRITE_TITLES["visit_self_report"],
              annotations=_annotations("visit_self_report"),
              description="提交今日自报：点数（1点/2点店数）+ 当天若干任务的进度，"
                          "一次提交（同一事务）；进度没变又没备注的会跳过")
    def _t_self_report(ctx: Context, area: str = "", p1_cnt: int = 0,
                       p2_cnt: int = 0,
                       items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        return visit_self_report(ctx, area=area, p1_cnt=p1_cnt, p2_cnt=p2_cnt,
                                 items=items)

    @mcp.tool(name="visit_task_report", title=_WRITE_TITLES["visit_task_report"],
              annotations=_annotations("visit_task_report"),
              description="上报单条任务进展（0–100）；队长随时可调本队进度，"
                          "未分配的任务 pct=100 即标识完成；队员当天被确认后锁住")
    def _t_task_report(ctx: Context, task_id: int, pct: int,
                       note: str = "") -> dict[str, Any]:
        return visit_task_report(ctx, task_id=task_id, pct=pct, note=note)

    @mcp.tool(name="visit_team_tasks", title=_READ_TITLES["visit_team_tasks"],
              annotations=_annotations("visit_team_tasks"),
              description="本队任务（tab: 空/unassigned 待派/doing 已分人/done）+ 待确认队列")
    def _t_team_tasks(ctx: Context, tab: str = "", kw: str = "",
                      line_id: int | None = None) -> dict[str, Any]:
        return visit_team_tasks(ctx, tab=tab, kw=kw, line_id=line_id)

    @mcp.tool(name="visit_task_transfer",
              title=_WRITE_TITLES["visit_task_transfer"],
              annotations=_annotations("visit_task_transfer"),
              description="转给别的队（队长之间换活）：不确认即生效；移出原担当但保留进度")
    def _t_task_transfer(ctx: Context, task_ids: list[int],
                         to_team_id: int) -> dict[str, Any]:
        return visit_task_transfer(ctx, task_ids=task_ids, to_team_id=to_team_id)

    @mcp.tool(name="visit_task_assign", title=_WRITE_TITLES["visit_task_assign"],
              annotations=_annotations("visit_task_assign"),
              description="派工 / 改派 / 回收：person_codes=[] 即回收（回到本队待派，进展保留）")
    def _t_task_assign(ctx: Context, task_id: int,
                       person_codes: list[str] | None = None) -> dict[str, Any]:
        return visit_task_assign(ctx, task_id=task_id, person_codes=person_codes)

    @mcp.tool(name="visit_task_confirm", title=_WRITE_TITLES["visit_task_confirm"],
              annotations=_annotations("visit_task_confirm"),
              description="确认（task_id）/ 一键全确认（all_today=True）/ 驳回（reject=True+pct）")
    def _t_task_confirm(ctx: Context, task_id: int | None = None,
                        all_today: bool = False, reject: bool = False,
                        pct: int | None = None, note: str = "") -> dict[str, Any]:
        return visit_task_confirm(ctx, task_id=task_id, all_today=all_today,
                                  reject=reject, pct=pct, note=note)

    @mcp.tool(name="visit_task_return", title=_WRITE_TITLES["visit_task_return"],
              annotations=_annotations("visit_task_return"),
              description="撤回任务到车站池（管理员）：只撤已派队但没分到人的（有进展也行）")
    def _t_task_return(ctx: Context, task_ids: list[int]) -> dict[str, Any]:
        return visit_task_return(ctx, task_ids=task_ids)

    @mcp.tool(name="visit_task_board", title=_READ_TITLES["visit_task_board"],
              annotations=_annotations("visit_task_board"),
              description="任务总表（管理员）：tab 计数 + 任务行 + 按队汇总 + 停滞口径")
    def _t_task_board(ctx: Context, tab: str = "", line_id: int | None = None,
                      kw: str = "", stale_only: bool = False, page: int = 1,
                      per: int = 20) -> dict[str, Any]:
        return visit_task_board(ctx, tab=tab, line_id=line_id, kw=kw,
                                stale_only=stale_only, page=page, per=per)

    # 管理员侧 13 个
    @mcp.tool(name="visit_upload", title=_WRITE_TITLES["visit_upload"],
              annotations=_annotations("visit_upload"), description=_DESC["visit_upload"])
    def _t_upload(ctx: Context, filename: str | None = None,
                  content_base64: str | None = None, path: str | None = None,
                  month: Annotated[str | None, Field(pattern=MONTH_PATTERN)] = None,
                  kind: str | None = None,
                  dry_run: bool = False) -> dict[str, Any]:
        return visit_upload(ctx, filename=filename, content_base64=content_base64,
                            path=path, month=month, kind=kind, dry_run=dry_run)

    @mcp.tool(name="visit_overview", title=_READ_TITLES["visit_overview"],
              annotations=_annotations("visit_overview"), description=_DESC["visit_overview"])
    def _t_overview(ctx: Context,
                    month: Annotated[str | None, Field(pattern=MONTH_PATTERN)] = None,
                    view: str = "summary", limit: int = 10) -> dict[str, Any]:
        return visit_overview(ctx, month=month, view=view, limit=limit)

    @mcp.tool(name="visit_person", title=_READ_TITLES["visit_person"],
              annotations=_annotations("visit_person"), description=_DESC["visit_person"])
    def _t_person(ctx: Context, person: str | None = None,
                  name: str | None = None,
                  month: Annotated[str | None, Field(pattern=MONTH_PATTERN)] = None
                  ) -> dict[str, Any]:
        return visit_person(ctx, person=person, name=name, month=month)

    @mcp.tool(name="visit_payroll", title=_READ_TITLES["visit_payroll"],
              annotations=_annotations("visit_payroll"), description=_DESC["visit_payroll"])
    def _t_payroll(ctx: Context,
                   month: Annotated[str, Field(pattern=MONTH_PATTERN)],
                   view: str = "rows") -> dict[str, Any]:
        return visit_payroll(ctx, month=month, view=view)

    @mcp.tool(name="visit_payroll_export", title=_WRITE_TITLES["visit_payroll_export"],
              annotations=_annotations("visit_payroll_export"),
              description=_DESC["visit_payroll_export"])
    def _t_payroll_export(ctx: Context,
                          month: Annotated[str, Field(pattern=MONTH_PATTERN)],
                          seq: int,
                          dry_run: bool = False) -> dict[str, Any]:
        return visit_payroll_export(ctx, month=month, seq=seq, dry_run=dry_run)

    @mcp.tool(name="visit_recon", title=_READ_TITLES["visit_recon"],
              annotations=_annotations("visit_recon"), description=_DESC["visit_recon"])
    def _t_recon(ctx: Context,
                 month: Annotated[str | None, Field(pattern=MONTH_PATTERN)] = None,
                 task_id: int | None = None,
                 view: str = "status") -> dict[str, Any]:
        return visit_recon(ctx, month=month, task_id=task_id, view=view)

    @mcp.tool(name="visit_recon_export", title=_READ_TITLES["visit_recon_export"],
              annotations=_annotations("visit_recon_export"),
              description=_DESC["visit_recon_export"])
    def _t_recon_export(ctx: Context,
                        month: Annotated[str | None, Field(pattern=MONTH_PATTERN)] = None,
                        task_id: int | None = None,
                        kind: str = "report") -> dict[str, Any]:
        return visit_recon_export(ctx, month=month, task_id=task_id, kind=kind)

    @mcp.tool(name="visit_files", title=_READ_TITLES["visit_files"],
              annotations=_annotations("visit_files"), description=_DESC["visit_files"])
    def _t_files(ctx: Context, view: str = "list",
                 import_id: int | None = None,
                 month: Annotated[str | None, Field(pattern=MONTH_PATTERN)] = None,
                 kind: str | None = None, status: str | None = None) -> dict[str, Any]:
        return visit_files(ctx, view=view, import_id=import_id, month=month,
                           kind=kind, status=status)

    @mcp.tool(name="visit_rebuild", title=_WRITE_TITLES["visit_rebuild"],
              annotations=_annotations("visit_rebuild"), description=_DESC["visit_rebuild"])
    def _t_rebuild(ctx: Context,
                   month: Annotated[str, Field(pattern=MONTH_PATTERN)],
                   action: str = "preview",
                   preview_id: int | None = None,
                   confirm_text: str | None = None) -> dict[str, Any]:
        return visit_rebuild(ctx, month=month, action=action,
                             preview_id=preview_id, confirm_text=confirm_text)

    @mcp.tool(name="visit_staff", title=_WRITE_TITLES["visit_staff"],
              annotations=_annotations("visit_staff"), description=_DESC["visit_staff"])
    def _t_staff(ctx: Context, view: str | None = None,
                 action: str | None = None, username: str | None = None,
                 status: str | None = None,
                 confirm_text: str | None = None) -> dict[str, Any]:
        return visit_staff(ctx, view=view, action=action, username=username,
                           status=status, confirm_text=confirm_text)

    @mcp.tool(name="visit_config", title=_WRITE_TITLES["visit_config"],
              annotations=_annotations("visit_config"), description=_DESC["visit_config"])
    def _t_config(ctx: Context, view: str | None = None,
                  action: str | None = None,
                  per_point: int | None = None,
                  bonus_group: int | None = None,
                  bonus_amount: int | None = None,
                  staff_visible_from: str | None = None,
                  confirm_text: str | None = None) -> dict[str, Any]:
        return visit_config(ctx, view=view, action=action, per_point=per_point,
                            bonus_group=bonus_group, bonus_amount=bonus_amount,
                            staff_visible_from=staff_visible_from,
                            confirm_text=confirm_text)

    @mcp.tool(name="visit_store", title=_WRITE_TITLES["visit_store"],
              annotations=_annotations("visit_store"), description=_DESC["visit_store"])
    def _t_store(ctx: Context, view: str | None = None,
                 action: str | None = None,
                 q: str | None = None, limit: int = 20,
                 pair_id: int | None = None, keep: int | None = None,
                 entity_id: int | None = None, kind: str | None = None,
                 note: str | None = None,
                 confirm_text: str | None = None) -> dict[str, Any]:
        return visit_store(ctx, view=view, action=action, q=q, limit=limit,
                           pair_id=pair_id, keep=keep, entity_id=entity_id,
                           kind=kind, note=note, confirm_text=confirm_text)

    @mcp.tool(name="visit_verify", title=_READ_TITLES["visit_verify"],
              annotations=_annotations("visit_verify"), description=_DESC["visit_verify"])
    def _t_verify(ctx: Context) -> dict[str, Any]:
        return visit_verify(ctx)
