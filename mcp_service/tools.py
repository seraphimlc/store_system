# -*- coding: utf-8 -*-
"""MCP 工具注册（适配层）。

职责边界：工具函数只做「取 Context → 调能力层 → 包错误信封」。
业务聚合在 capability.py，业务逻辑复用 app.services.*（不重复实现）。
P1 起：payload 构建函数从本文件拆出，本文件只保留注册（见计划评审建议）。
"""
from typing import Annotated, Any

from mcp.server.mcpserver import Context, MCPServer
from pydantic import Field

from mcp_service.capability import MONTH_PATTERN


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


def _envelope_error(code: str, message: str, hint: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, "hint": hint}}


def register(mcp: MCPServer) -> None:
    @mcp.tool(
        name="visit_ping",
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
        name="visit_month_summary",
        description=(
            "查询某结算月（格式 YYYY-MM）正式表的行数、总点数、1点/2点条数与人数。"
            "数据来自已结算的正式表，只读。"
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
