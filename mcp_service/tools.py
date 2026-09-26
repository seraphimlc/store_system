# -*- coding: utf-8 -*-
"""MCP 工具注册（适配层）。

职责边界：工具函数只做「取 Context → 调能力层 → 包错误信封」。
业务聚合在 capability.py，业务逻辑复用 app.services.*（不重复实现）。
P1 起：payload 构建函数从本文件拆出，本文件只保留注册（见计划评审建议）。
"""
import os
from typing import Annotated, Any

from mcp.server.mcpserver import Context, MCPServer
from pydantic import Field

from mcp_service.capability import MONTH_PATTERN
from mcp_service.annotations import read as read_ann
from mcp_service.annotations import write as write_ann


def _client_info(ctx: Context) -> str | None:
    """客户端上报的名称/版本（spec §5.4 要求回显的是它，不是 request id）。"""
    try:
        impl = ctx.session.client_params.client_info
        return f"{impl.name}/{impl.version}"
    except Exception:  # noqa: BLE001  stdio 或老客户端可能没有
        return None


def ping_payload(headers, protocol_version, client_info) -> dict[str, Any]:
    lowered = {k.lower(): v for k, v in (headers or {}).items()}
    return {
        "server": "visit-settle-mcp",
        "sdk_version": _sdk_version(),
        "protocol_version": protocol_version,
        "client_info": client_info,
        "auth_header_seen": "authorization" in lowered,
        "session_id_seen": "mcp-session-id" in lowered,
        "now": _now_iso(),
    }


def _sdk_version() -> str:
    from importlib.metadata import version
    try:
        return version("mcp")
    except Exception:  # noqa: BLE001
        return "unknown"


def _now_iso() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _envelope_error(code: str, message: str, hint: str,
                    **extra: Any) -> dict[str, Any]:
    """统一失败信封（含 retryable；码表见 mcp_service/envelope.py）。"""
    from mcp_service import envelope
    return envelope.error(code, message, hint, **extra)


def _check_upload_sources(path: str | None,
                          content_base64: str | None) -> dict[str, Any] | None:
    """P0-1 参数互斥：`path` 与 `content_base64` 必须且只能提供其一。

    都传 → BAD_REQUEST；都不传 → BAD_REQUEST（hint 说明）。
    返回 None 表示通过；否则返回错误信封（调用方直接 return）。
    """
    if content_base64 and path:
        return _envelope_error(
            "BAD_REQUEST", "path 与 content_base64 不可同时提供",
            "二选一：跨机器用 content_base64；本机路径模式用 path，不要同时传")
    if not content_base64 and not path:
        return _envelope_error(
            "BAD_REQUEST", "必须提供 path 或 content_base64 之一",
            "请把文件内容以 base64 传入（content_base64），"
            "或在本机路径模式下给绝对路径（path）；两者都不可缺失")
    return None


def _write_call(ctx: Context, tool: str, params: dict, fn, *, retryable: bool):
    """写工具统一包装：actor → 授权拦截 → 审计 → 闸门/业务 → 错误信封。

    retryable=True（finalize/set_per_point：原子或幂等）→ 意外异常用 INTERNAL（可重试）
    retryable=False（rebuild：流程中间提交）→ INTERNAL_WRITE（**禁止自动重试**）
    授权拒绝（FORBIDDEN_TOOL / UNAUTHORIZED）在进入业务前拦截 → 无副作用。
    """
    from mcp_service import authz, guards

    actor = actor_from_ctx(ctx)
    from app.db import SessionLocal
    db = SessionLocal()
    try:
        def _mapped(db, actor):
            try:
                return fn(db, actor)
            except guards.GuardError as exc:
                return _envelope_error(exc.code, exc.message, exc.hint)

        return authz.dispatch(db, tool=tool, actor=actor, params=params,
                              client_info=_client_info(ctx),
                              fn=_mapped, retryable=retryable, fn_args=2)
    finally:
        db.close()


def _guarded(ctx: Context, tool: str, params: dict, fn, *,
             retryable: bool = True) -> dict[str, Any]:
    """直连只读工具的统一入口：授权拦截 + 两阶段审计 + 执行 fn(db)。

    fn 返回完整信封（自身负责业务异常映射）；供 visit_ping / visit_month_salary /
    visit_month_summary 等直接注册的工具使用。
    """
    from mcp_service import authz

    actor = actor_from_ctx(ctx)
    from app.db import SessionLocal
    db = SessionLocal()
    try:
        return authz.dispatch(db, tool=tool, actor=actor, params=params,
                              client_info=_client_info(ctx),
                              fn=fn, retryable=retryable, fn_args=1)
    finally:
        db.close()


def actor_from_ctx(ctx: Context):
    """从请求头解析 Actor（P1 写工具与审计用）。

    中间件已在 HTTP 层拦 401；这里为拿到 actor（含 token_id）再解析一次——
    一次前缀索引查询，成本可忽略，换来不必在 ASGI 层透传���态。
    """
    headers = {k.lower(): v for k, v in (ctx.headers or {}).items()}
    raw = headers.get("authorization", "")
    if raw[:7].lower() != "bearer ":
        return None
    from app.db import SessionLocal
    from mcp_service import tokens
    db = SessionLocal()
    try:
        return tokens.resolve(db, raw[7:].strip(),
                              bootstrap_token=os.environ.get("VISIT_MCP_TOKEN"))
    finally:
        db.close()


def client_info_of(ctx: Context) -> str | None:
    return _client_info(ctx)


def register(mcp: MCPServer) -> None:
    # 各批次模块自带 register（避免同文件并发改动）；此处统一接线
    from mcp_service import (export_ops, misc_ops, my_ops, payroll_write_ops,
                             read_ops, recon_ops, recon_write_ops,
                             store_write_ops, write_tools)
    read_ops.register(mcp)
    read_ops.register_integrity(mcp)
    recon_ops.register(mcp)
    export_ops.register(mcp)
    recon_write_ops.register(mcp)
    write_tools.register(mcp)
    store_write_ops.register(mcp)
    payroll_write_ops.register(mcp)
    misc_ops.register(mcp)
    my_ops.register(mcp)

    @mcp.tool(
        name="visit_ping",
        title="服务诊断回显",
        annotations=read_ann("服务诊断回显"),
        description=(
            "诊断用：回显服务端身份、协商到的协议版本、以及本次调用是否携带了 "
            "Authorization 凭据头。用于排查 WorkBuddy 连接与鉴权问题。"
        ),
    )
    def visit_ping(ctx: Context) -> dict[str, Any]:
        return {"ok": True, "data": ping_payload(
            headers=ctx.headers,
            protocol_version=ctx.protocol_version,
            client_info=_client_info(ctx),
        )}

    @mcp.tool(
        name="visit_upload_file",
        title="上传巡店/对账文件",
        annotations=write_ann("上传巡店/对账文件", idempotent=False),
        description=(
            "必须提供 path 或 content_base64 之一，不可同时提供，也不可都不提供。"
            "**统一上传入口：自动识别文件类型并走对应通道**，用户只需把文件丢进来。"
            "识别规则：① 巡店记录（MarsNavi STORE VISIT RECORD，sheet 名 "
            "STORE_TASK_EXCEL_SHEET）→ 解析 → 判定 → 入正式表 → 工资/找平/看板/员工分析全自动；"
            "② 对账明细（如 Alipay 结算数据，含 Statement Date/Agent Name 等列）→ 对账任务"
            "（月份自动从文件日期推断，也可用 month 指定）；"
            "③ 手工结算对照件（巡回最终结算）→ 明确提示不入库；④ 无法识别 → 给出指引。"
            "参数：filename；content_base64（文件内容 base64，跨机器上传用）；"
            "path（本机绝对路径，仅服务端开启本地路径模式时可用）；month（可选，对账文件用）；"
            "kind（可选，强制指定 daily_records/recon，兼容旧值 visit）；"
            "dry_run（可选，默认 false；true=只识别+推断月份+查将替换的对账任务，"
            "**不写入**，返回影响预估）。"
            "**detected 字段说明**：返回里的 detected = {kind, reason, month_inferred}；"
            "kind 为实际识别结果（daily_records=巡店 / recon=对账），reason 为识别依据，"
            "month_inferred=true 表示对账月份是从文件推断的。"
            "**若返回 NEED_FILE_KIND（识别不出），必须询问用户该文件属于哪一类，"
            "拿到答复后带 kind 参数重新上传，不要自行猜测。**"
            "**幂等与覆盖语义：**"
            "- 文件级去重：按内容 sha256。字节相同的文件重复上传返回 DUPLICATE_FILE，不重复入库。"
            "- 同月覆盖：同一结算月的同类型数据重复上传会「覆盖」上一版本"
            "（旧任务 replaced、version 递增），不是追加。"
            "- 不同月份互不影响。"
            "**错误码表**（失败返回 {ok:false, error:{code,message,retryable,hint}}）："
            "PARSE_FAILED 文件无法解析（不可重试，需修正文件）；DUPLICATE_FILE 内容指纹已存在"
            "（不可重试）；INTERNAL_WRITE 服务端写入异常、可能已部分生效"
            "（**禁止自动重试**）；TEMPLATE_SHEET_MISSING 未找到巡店模板 sheet（不可重试）；"
            "MONTH_SEALED / MONTH_CLOSED 目标月份已封账（不可重试）；AUTH_FAILED / "
            "UNAUTHORIZED / FORBIDDEN_TOOL 鉴权或权限不足（不可重试）；BAD_REQUEST / "
            "BAD_PARAM / BAD_MONTH 参数不合法（不可重试）。"
            "需要写权限 Token。封账月份的巡店文件会被拒绝。"
        ),
    )
    def visit_upload_file(
        ctx: Context,
        filename: str | None = None,
        content_base64: str | None = None,
        path: str | None = None,
        month: str | None = None,
        kind: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        bad = _check_upload_sources(path, content_base64)
        if bad is not None:
            return bad
        from mcp_service import write_ops

        content = write_ops.decode_base64(content_base64) if content_base64 else None

        def run(db, actor):
            return write_ops.upload_file(db, actor, filename=filename,
                                         content=content, path=path,
                                         month=month, kind=kind,
                                         dry_run=dry_run)

        return _write_call(ctx, "visit_upload_file",
                           {"filename": filename, "path": path,
                            "has_content": bool(content_base64),
                            "dry_run": dry_run},
                           run, retryable=False)

    @mcp.tool(
        name="visit_month_salary",
        title="月度薪资查询",
        annotations=read_ann("月度薪资查询"),
        description=(
            "**什么时候用我**：要看每人明细/薪资时用我。"
            "查询某结算月（格式 YYYY-MM）的员工薪资：人数、总点数、总工资，"
            "以及每人点数与工资明细。可选 person 参数按工号或姓名筛选；"
            "可选 sort_by 参数（points=按总点数降序【默认】/ amount=按工资金额降序）"
            "与 limit 参数（默认 0=返回全部；>0 只返回前 N 名，配合 sort_by 可当排行用）。"
            "字段口径：points（该人总点数）/ p1·p2（1点/2点条数）/ per_point"
            "（单价，円/点）/ salary（应付工资円）/ settle_amount（实际结算额円，"
            "未对账为 0）/ diff_amount（=settle_amount−salary，未对账时为 0）。"
            "person 匹配规则：工号精确匹配；姓名包含匹配；同名返回全部。"
            "数据来自已物化的月绩效记录（已结算口径），只读。"
            "注意：金额单位为日元（円，字段 currency=JPY），不要表述为人民币元。"
        ),
    )
    def visit_month_salary(
        month: Annotated[str, Field(pattern=MONTH_PATTERN)],
        ctx: Context,
        person: str | None = None,
        sort_by: str = "points",
        limit: int = 0,
    ) -> dict[str, Any]:
        from app.db import SessionLocal
        from mcp_service import capability

        db = SessionLocal()
        try:
            data = capability.month_salary(db, month, person=person,
                                            sort_by=sort_by, limit=limit)
        except capability.BadMonth as exc:
            return _envelope_error("BAD_MONTH", str(exc),
                                   "月份必须是 YYYY-MM，例如 2026-09")
        except capability.BadParam as exc:
            return _envelope_error("BAD_PARAM", str(exc), exc.hint or "")
        except Exception as exc:  # noqa: BLE001
            return _envelope_error("INTERNAL", repr(exc),
                                   "系统内部错误，已记录；可重试")
        finally:
            db.close()
        return {"ok": True, "data": data}

    @mcp.tool(
        name="visit_month_summary",
        title="月度正式表汇总",
        annotations=read_ann("月度正式表汇总"),
        description=(
            "**什么时候用我**：只要汇总数字（行数/点数/人数）时用我。"
            "查询某结算月（格式 YYYY-MM）正式表的行数、总点数、1点/2点条数与人数。"
            "数据来自已结算的正式表，只读。"
            "这是 visit_dashboard 的精简版：需要质量/排行/人员变动等更多指标时请用 "
            "visit_dashboard（同一月份，字段更全）。"
        ),
    )
    def visit_month_summary(
        month: Annotated[str, Field(pattern=MONTH_PATTERN)],
        ctx: Context,
    ) -> dict[str, Any]:
        from app.db import SessionLocal

        db = SessionLocal()
        try:
            data = _month_summary_capability(db, month)
        except Exception as exc:  # noqa: BLE001  spec §5.5 只读工具 -> INTERNAL
            return _envelope_error(
                "INTERNAL", repr(exc), "系统内部错误，已记录；可重试")
        finally:
            db.close()
        if data["formal_rows"] == 0:
            data["hint"] = "该月正式表无数据（合法结果，不是错误）"
        return {"ok": True, "data": data}


def _month_summary_capability(db, month: str) -> dict[str, Any]:
    """薄封装：把能力层的 BadMonth 转成 BAD_MONTH 信封。"""
    from mcp_service import capability

    try:
        return capability.month_summary(db, month)
    except capability.BadMonth as exc:
        raise _EnvelopeError("BAD_MONTH", str(exc),
                             "月份必须是 YYYY-MM，例如 2026-09") from exc


class _EnvelopeError(RuntimeError):
    """内部标记：调用方把它转成错误信封。"""

    def __init__(self, code: str, message: str, hint: str) -> None:
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint
