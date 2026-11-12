# -*- coding: utf-8 -*-
"""Bearer 鉴权中间件（纯 ASGI）。

为什么必须在 ASGI 层："工具内的检查"只能产生 MCP 工具错误，拿不到
HTTP 401 + `WWW-Authenticate`；而验收项 A3 要求客户端可见拒绝 **且**
服务端有 401 —— 两者都要成立（spec §5.2）。

只接受 `Authorization: Bearer <token>` 单通道。不做查询参数通道：
凭据会进 URL、进日志、进客户端历史（spec §5.2 明确排除）。
"""
import hashlib
import hmac
import json
import time
from typing import Any, Callable

from mcp_service.reqlog import redact_headers, redact_path

_UNAUTHORIZED = {
    "ok": False,
    "error": {
        "code": "UNAUTHORIZED",
        "message": "缺少或无效的 Access Token",
        "hint": "请在 WorkBuddy 连接器设置中重新填写 Access Token",
    },
}

_BEARER = "Bearer "

# 员工可用工具名（延迟从 authz 取，避免循环 import）
_ALLOWED_TOOL_NAMES = None


def resource_metadata_url() -> str:
    """OAuth protected-resource 元数据地址（MCP OAuth 规范 §2.1）。

    issuer 取 `VISIT_OAUTH_ISSUER`，缺省由 `VISIT_MCP_PUBLIC_HOST` 推导；
    客户端收到 401 后据此自动发起授权（WorkBuddy 支持）。
    """
    import os
    # **必须是 MCP 服务自己的地址**（RFC 9728：资源自带元数据；客户端会按 origin 校验
    # resource 与它连接的 URL 是否同源——实测跨 origin 会导致"点连接没反应"）。
    base = (os.environ.get("VISIT_OAUTH_RESOURCE") or "").strip().rstrip("/")
    if not base:
        host = (os.environ.get("VISIT_MCP_PUBLIC_HOST") or "").strip()
        if host:
            base = f"https://{host}"
        else:
            h = (os.environ.get("VISIT_MCP_HOST") or "127.0.0.1").strip()
            pt = (os.environ.get("VISIT_MCP_PORT") or "8765").strip()
            base = f"http://{h}:{pt}"
    return f"{base}/.well-known/oauth-protected-resource"


def filter_tools_list_body(raw: bytes, allowed: set) -> bytes:
    """把 tools/list 响应里的工具数组按白名单裁剪（员工只看到自己能用的工具）。

    为什么要改响应体：MCP 的 tools/list 在**独立的会话任务**里处理，中间件设的
    contextvar 传不进去（实测无效），因此在 HTTP 层裁剪响应最稳妥。
    兼容两种编码：SSE（`data: {...}`）与普通 JSON。
    """
    import json as _json

    def _filter_obj(obj):
        """返回 (是否改过, obj)。非 tools/list 响应不改（保持原字节，避免无谓改写）。"""
        try:
            result = obj.get("result") or {}
            tools = result.get("tools")
            if isinstance(tools, list):
                result["tools"] = [t for t in tools
                                   if isinstance(t, dict) and t.get("name") in allowed]
                obj["result"] = result
                return True, obj
        except Exception:  # noqa: BLE001
            pass
        return False, obj

    try:
        if b"data:" in raw:                       # SSE
            out = []
            for line in raw.split(b"\n"):
                if line.startswith(b"data:"):
                    payload = line[5:].strip()
                    try:
                        obj = _json.loads(payload.decode("utf-8"))
                        changed, obj = _filter_obj(obj)
                        if changed:
                            line = b"data: " + _json.dumps(
                                obj, ensure_ascii=False).encode("utf-8")
                    except Exception:  # noqa: BLE001
                        pass
                out.append(line)
            return b"\n".join(out)
        obj = _json.loads(raw.decode("utf-8"))     # 普通 JSON
        changed, obj = _filter_obj(obj)
        if not changed:
            return raw                             # 未改动 → 原字节返回
        return _json.dumps(obj, ensure_ascii=False).encode("utf-8")
    except Exception:  # noqa: BLE001
        return raw


def _www_authenticate_header(resource_metadata: str | None = None) -> bytes:
    """`WWW-Authenticate: Bearer resource_metadata="<url>"`（RFC 6750 + MCP OAuth）。"""
    url = resource_metadata or resource_metadata_url()
    return f'Bearer resource_metadata="{url}"'.encode("utf-8")


class _BodyCapture:
    """缓存请求体用于 body_digest；只记摘要，原文不进日志（spec §5.6）。"""

    def __init__(self, receive):
        self._receive = receive
        self.parts: list[bytes] = []

    async def __call__(self):
        message = await self._receive()
        if message["type"] == "http.request":
            self.parts.append(message.get("body", b""))
        return message


class BearerAuthMiddleware:
    """Bearer 鉴权（P1：查 api_tokens 表；env token 降级为只读 bootstrap）。"""

    def __init__(self, app, log: Callable[[dict[str, Any]], None],
                 bootstrap_token: str | None = None) -> None:
        self.app = app
        self._log = log
        self._bootstrap = bootstrap_token

    def _authorize(self, header: str):
        """返回 Actor 或 None（spec §5.3）。"""
        # RFC 7235：auth scheme 大小写不敏感
        if header[: len(_BEARER)].lower() != _BEARER.lower():
            return None
        raw = header[len(_BEARER):].strip()
        from app.db import SessionLocal
        from mcp_service import tokens
        db = SessionLocal()
        try:
            return tokens.resolve(db, raw, bootstrap_token=self._bootstrap)
        finally:
            db.close()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # **发现端点必须公开**：OAuth 客户端先取元数据才知道怎么认证；
        # 若这里也要求 Token，客户端会拿到 401 而无法开始授权流程（实测表现：点连接没反应）。
        path = scope.get("path", "")
        if path.startswith("/.well-known/"):
            await self.app(scope, receive, send)
            return

        raw = scope.get("headers") or []
        headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                   for k, v in raw}
        auth_header = headers.get("authorization", "")
        started = time.monotonic()
        body = _BodyCapture(receive)

        record: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "method": scope.get("method"),
            "path": redact_path(scope.get("path", "")),
            "header_names": redact_headers(raw),
            "auth_header_seen": bool(auth_header),
            "host": headers.get("host"),
            "origin": headers.get("origin"),
            "protocol_version": headers.get("mcp-protocol-version"),
            "session_id_seen": bool(headers.get("mcp-session-id")),
        }

        actor = self._authorize(auth_header)
        if actor is None:
            record["status"] = 401
            record["duration_ms"] = int((time.monotonic() - started) * 1000)
            record["body_digest"] = hashlib.sha256(
                b"".join(body.parts)).hexdigest()
            self._log(record)
            await self._send_401(send)
            return

        record["actor_uid"] = actor.uid
        record["token_id"] = actor.token_id

        captured = {"status": 200}
        # 员工：裁剪 tools/list 的响应（只保留其可用工具）。做法=缓冲响应体，
        # 请求体解析出 method==tools/list 时过滤后再发出（contextvar 在 SDK 的
        # 会话任务里传不过去，只能在 HTTP 层做）。管理员/无身份：直通。
        from mcp_service import authz as _authz
        global _ALLOWED_TOOL_NAMES
        if _ALLOWED_TOOL_NAMES is None:
            _ALLOWED_TOOL_NAMES = {
                "staff": set(_authz.STAFF_ALLOWED),
                "leader": set(_authz.LEADER_ALLOWED),
            }
        _role = getattr(actor, "role", "") if actor is not None else ""
        # ⚠️ 管理员**直通**（不裁剪）；员工/队长按各自档裁剪；未知角色 → 员工档（保守）
        #    2026-10-06 踩到：写成 `_keep = get(role) or staff` 会把管理员的列表也裁成员工档
        if _role == "admin":
            _keep = None
        elif _role:
            _keep = _ALLOWED_TOOL_NAMES.get(_role) or _ALLOWED_TOOL_NAMES["staff"]
        else:
            _keep = None                     # actor=None（stdio 本地）不裁剪
        need_filter = bool(_keep)
        buffered = {"chunks": [], "head": None}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                captured["status"] = message["status"]
                if need_filter:
                    buffered["head"] = message
                    return
            elif message["type"] == "http.response.body" and need_filter:
                buffered["chunks"].append(message.get("body", b""))
                if message.get("more_body"):
                    return
                raw = b"".join(buffered["chunks"])
                try:
                    req = json.loads(b"".join(body.parts).decode("utf-8"))
                    method = req.get("method") if isinstance(req, dict) else None
                except Exception:  # noqa: BLE001
                    method = None
                if method == "tools/list":
                    allowed = set(_keep or ())
                    raw = filter_tools_list_body(raw, allowed)
                await send(buffered["head"])
                await send({"type": "http.response.body", "body": raw})
                return
            await send(message)

        try:
            await self.app(scope, body, send_wrapper)
        finally:
            record["status"] = captured["status"]
            record["duration_ms"] = int((time.monotonic() - started) * 1000)
            record["body_digest"] = hashlib.sha256(
                b"".join(body.parts)).hexdigest()
            self._log(record)

    @staticmethod
    async def _send_401(send) -> None:
        body = json.dumps(_UNAUTHORIZED, ensure_ascii=False).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode()),
                (b"www-authenticate", _www_authenticate_header()),
            ],
        })
        await send({"type": "http.response.body", "body": body})
