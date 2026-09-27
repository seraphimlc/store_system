# -*- coding: utf-8 -*-
"""MCP 工具注册（适配层）。

职责边界：工具函数只做「取 Context → 调能力层 → 包错误信封」。
P1 起：payload 构建函数从本文件拆出，本文件只保留注册接线。
场景化重构（49 → 16）：全部工具注册迁移到 scenario_ops.py（本文件只接线），
旧工具名已按用户要求**直接删除、不做兼容期**（规格：docs/specs-mcp-tools-scenario.md）。
"""
import os
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

    retryable=True（幂等写）→ 意外异常用 INTERNAL（可重试）
    retryable=False（流程中间提交）→ INTERNAL_WRITE（**禁止自动重试**）
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

    fn 返回完整信封（自身负责业务异常映射）。
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
    一次前缀索引查询，成本可忽略，换来不必在 ASGI 层透传状态。
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
    """唯一注册入口：只注册 16 个场景化工具（scenario_ops）。"""
    from mcp_service import scenario_ops
    scenario_ops.register(mcp)
