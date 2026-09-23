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
    def __init__(self, app, token: str,
                 log: Callable[[dict[str, Any]], None]) -> None:
        self.app = app
        self._token = token
        self._log = log

    def _authorized(self, header: str) -> bool:
        # RFC 7235：auth scheme 大小写不敏感
        if header[: len(_BEARER)].lower() != _BEARER.lower():
            return False
        return hmac.compare_digest(header[len(_BEARER):].strip(), self._token)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
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

        if not self._authorized(auth_header):
            record["status"] = 401
            record["duration_ms"] = int((time.monotonic() - started) * 1000)
            record["body_digest"] = hashlib.sha256(
                b"".join(body.parts)).hexdigest()
            self._log(record)
            await self._send_401(send)
            return

        captured = {"status": 200}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                captured["status"] = message["status"]
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
                (b"www-authenticate", b"Bearer"),
            ],
        })
        await send({"type": "http.response.body", "body": body})
