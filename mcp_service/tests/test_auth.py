# -*- coding: utf-8 -*-
"""鉴权中间件：HTTP 层 401 + WWW-Authenticate，且 401 也要落日志。

用 asyncio.run 驱动（而非 pytest-asyncio），避免为中间件测试引入新依赖。
"""
import asyncio
import json

from mcp_service.auth import BearerAuthMiddleware

TOKEN = "g" * 32


class Recorder:
    def __init__(self):
        self.records = []

    def __call__(self, record):
        self.records.append(record)


async def _ok_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


async def _boom_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 500, "headers": []})
    raise RuntimeError("boom after response start")


async def _asgi_call(app, headers, path="/mcp", query=b"token=LEAK"):
    scope = {
        "type": "http", "method": "POST", "path": path,
        "query_string": query, "headers": headers,
        "http_version": "1.1", "scheme": "http",
        "server": ("127.0.0.1", 8765),
    }
    out = {"status": None, "headers": [], "body": b""}

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            out["status"] = message["status"]
            out["headers"] = message["headers"]
        elif message["type"] == "http.response.body":
            out["body"] += message.get("body", b"")

    await app(scope, receive, send)
    return out


def _call(app, headers, path="/mcp", query=b"token=LEAK"):
    return asyncio.run(_asgi_call(app, headers, path, query))


def test_missing_bearer_returns_401():
    log = Recorder()
    app = BearerAuthMiddleware(_ok_app, token=TOKEN, log=log)
    r = _call(app, [(b"host", b"127.0.0.1:8765")])
    assert r["status"] == 401
    assert (b"www-authenticate", b"Bearer") in r["headers"]
    assert json.loads(r["body"])["error"]["code"] == "UNAUTHORIZED"


def test_wrong_bearer_returns_401():
    log = Recorder()
    app = BearerAuthMiddleware(_ok_app, token=TOKEN, log=log)
    r = _call(app, [(b"authorization", b"Bearer wrong")])
    assert r["status"] == 401


def test_bearer_without_prefix_returns_401():
    log = Recorder()
    app = BearerAuthMiddleware(_ok_app, token=TOKEN, log=log)
    r = _call(app, [(b"authorization", TOKEN.encode())])
    assert r["status"] == 401


def test_correct_bearer_passes_through():
    log = Recorder()
    app = BearerAuthMiddleware(_ok_app, token=TOKEN, log=log)
    r = _call(app, [(b"authorization", f"Bearer {TOKEN}".encode())])
    assert r["status"] == 200
    assert r["body"] == b"ok"


def test_401_is_logged_with_status_and_query_redacted():
    log = Recorder()
    app = BearerAuthMiddleware(_ok_app, token=TOKEN, log=log)
    _call(app, [(b"host", b"127.0.0.1:8765")])
    assert len(log.records) == 1
    rec = log.records[0]
    assert rec["status"] == 401
    assert rec["auth_header_seen"] is False
    assert rec["path"] == "/mcp"
    assert "LEAK" not in json.dumps(rec)


def test_correct_bearer_logs_auth_header_seen_true():
    log = Recorder()
    app = BearerAuthMiddleware(_ok_app, token=TOKEN, log=log)
    _call(app, [(b"authorization", f"Bearer {TOKEN}".encode())])
    assert log.records[0]["auth_header_seen"] is True
    assert log.records[0]["status"] == 200


def test_logs_host_origin_and_session_headers():
    """意外 Origin 是本地连通的典型静默失败源，必须留证据（spec §5.6）。"""
    log = Recorder()
    app = BearerAuthMiddleware(_ok_app, token=TOKEN, log=log)
    _call(app, [
        (b"authorization", f"Bearer {TOKEN}".encode()),
        (b"host", b"127.0.0.1:8765"),
        (b"origin", b"app://workbuddy"),
        (b"mcp-session-id", b"sess-1"),
        (b"mcp-protocol-version", b"2025-11-25"),
    ])
    rec = log.records[0]
    assert rec["host"] == "127.0.0.1:8765"
    assert rec["origin"] == "app://workbuddy"
    assert rec["session_id_seen"] is True
    assert rec["protocol_version"] == "2025-11-25"


def test_logs_even_when_downstream_raises():
    """下游异常也必须留一行记录——否则正是最难查的那种失败。"""
    log = Recorder()
    app = BearerAuthMiddleware(_boom_app, token=TOKEN, log=log)
    try:
        _call(app, [(b"authorization", f"Bearer {TOKEN}".encode())])
    except RuntimeError:
        pass
    assert len(log.records) == 1
    assert log.records[0]["status"] == 500


def test_non_http_scope_passes_through():
    """lifespan scope 必须原样透传，否则 SDK 的会话管理器起不来。"""
    seen = {}

    async def _app(scope, receive, send):
        seen["type"] = scope["type"]

    app = BearerAuthMiddleware(_app, token=TOKEN, log=Recorder())

    async def _run():
        await app({"type": "lifespan"}, None, None)

    asyncio.run(_run())
    assert seen["type"] == "lifespan"
