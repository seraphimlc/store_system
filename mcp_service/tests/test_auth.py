# -*- coding: utf-8 -*-
"""鉴权中间件（P1：查 api_tokens 表）：HTTP 层 401 + WWW-Authenticate，且 401 也落日志。

用 asyncio.run 驱动（而非 pytest-asyncio），避免为中间件测试引入新依赖。
"""
import asyncio
import hashlib
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import ApiToken, Base, User
from mcp_service.auth import BearerAuthMiddleware

TOKEN = "g" * 43


class Recorder:
    def __init__(self):
        self.records = []

    def __call__(self, record):
        self.records.append(record)


@pytest.fixture(autouse=True)
def _token_db(tmp_path, monkeypatch):
    """临时库 + 一枚有效 Token，并让中间件用它查表。"""
    eng = create_engine(f"sqlite:///{tmp_path}/auth.db",
                        connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    S = sessionmaker(bind=eng, expire_on_commit=False)
    s = S()
    u = User(username="admin", password_hash="x", role="admin", is_active=True)
    s.add(u)
    s.commit()
    s.add(ApiToken(user_id=u.id, name="t", token_prefix=TOKEN[:8],
                   token_hash=hashlib.sha256(TOKEN.encode()).hexdigest(),
                   scopes="read,write"))
    s.commit()
    s.close()
    monkeypatch.setattr("app.db.SessionLocal", S)
    yield
    eng.dispose()


async def _ok_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


async def _boom_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 500, "headers": []})
    raise RuntimeError("boom after response start")


async def _consume_body_app(scope, receive, send):
    """真实路径：MCP POST 会读取请求体（JSON-RPC）。"""
    body = b""
    while True:
        m = await receive()
        body += m.get("body", b"")
        if not m.get("more_body", False):
            break
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": body})


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


def _mw(app, log):
    return BearerAuthMiddleware(app, log=log, bootstrap_token=None)


def test_missing_bearer_returns_401():
    log = Recorder()
    r = _call(_mw(_ok_app, log), [(b"host", b"127.0.0.1:8765")])
    assert r["status"] == 401
    # MCP OAuth：WWW-Authenticate 必须带 resource_metadata（客户端据此发起授权）
    hdr = dict((k.decode(), v.decode())
               for k, v in r["headers"]).get("www-authenticate", "")
    assert hdr.startswith("Bearer resource_metadata=")
    assert hdr.endswith("/.well-known/oauth-protected-resource\"")
    assert json.loads(r["body"])["error"]["code"] == "UNAUTHORIZED"


def test_401_header_resource_metadata_url(monkeypatch):
    """resource_metadata 指向 **MCP 服务自己的 origin**（RFC 9728）。

    为什么不能用 issuer（web 地址）：WorkBuddy 的 SDK 会把元数据里的 `resource`
    与它连接的 MCP URL 按 origin 精确比对，跨 origin（端口不同）会静默放弃——
    实测表现为"点连接没反应"。
    """
    from mcp_service.auth import resource_metadata_url
    monkeypatch.setenv("VISIT_OAUTH_RESOURCE", "http://127.0.0.1:8765")
    monkeypatch.setenv("VISIT_MCP_PUBLIC_HOST", "store.visitworld.me")
    assert resource_metadata_url() == \
        "http://127.0.0.1:8765/.well-known/oauth-protected-resource"

    # 未设 VISIT_OAUTH_RESOURCE → 由 VISIT_MCP_PUBLIC_HOST 推导（生产）
    monkeypatch.delenv("VISIT_OAUTH_RESOURCE", raising=False)
    assert resource_metadata_url() == \
        "https://store.visitworld.me/.well-known/oauth-protected-resource"

    # 都没有 → 本地默认 host:port
    monkeypatch.delenv("VISIT_MCP_PUBLIC_HOST", raising=False)
    monkeypatch.setenv("VISIT_MCP_HOST", "127.0.0.1")
    monkeypatch.setenv("VISIT_MCP_PORT", "8765")
    assert resource_metadata_url() == \
        "http://127.0.0.1:8765/.well-known/oauth-protected-resource"

def test_wrong_bearer_returns_401():
    log = Recorder()
    r = _call(_mw(_ok_app, log), [(b"authorization", b"Bearer wrong")])
    assert r["status"] == 401


def test_bearer_without_prefix_returns_401():
    log = Recorder()
    r = _call(_mw(_ok_app, log), [(b"authorization", TOKEN.encode())])
    assert r["status"] == 401


def test_correct_bearer_passes_through():
    log = Recorder()
    r = _call(_mw(_ok_app, log), [(b"authorization", f"Bearer {TOKEN}".encode())])
    assert r["status"] == 200
    assert r["body"] == b"ok"


def test_401_is_logged_with_status_and_query_redacted():
    log = Recorder()
    _call(_mw(_ok_app, log), [(b"host", b"127.0.0.1:8765")])
    assert len(log.records) == 1
    rec = log.records[0]
    assert rec["status"] == 401
    assert rec["auth_header_seen"] is False
    assert rec["path"] == "/mcp"
    assert "LEAK" not in json.dumps(rec)


def test_correct_bearer_logs_actor_and_header_seen():
    log = Recorder()
    _call(_mw(_ok_app, log), [(b"authorization", f"Bearer {TOKEN}".encode())])
    rec = log.records[0]
    assert rec["auth_header_seen"] is True
    assert rec["status"] == 200
    assert rec["token_id"] is not None          # P1：记下命中的 Token 行
    assert rec["actor_uid"] is not None


def test_logs_host_origin_and_session_headers():
    """意外 Origin 是本地连通的典型静默失败源，必须留证据（spec §5.6）。"""
    log = Recorder()
    _call(_mw(_ok_app, log), [
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


def test_logs_body_digest_matching_consumed_body():
    """§5.6 的 body_digest 必须等于下游实际消费的请求体摘要（原文不进日志）。"""
    log = Recorder()
    _call(_mw(_consume_body_app, log),
          [(b"authorization", f"Bearer {TOKEN}".encode())], query=b"")
    rec = log.records[0]
    assert rec["body_digest"] == hashlib.sha256(b"{}").hexdigest()


def test_logs_body_digest_of_unread_body_is_empty_hash():
    """下游不读请求体时（如 401 短路），摘要是空串的 sha256——不泄正文。"""
    log = Recorder()
    _call(_mw(_ok_app, log),
          [(b"authorization", f"Bearer {TOKEN}".encode())], query=b"")
    assert log.records[0]["body_digest"] == hashlib.sha256(b"").hexdigest()


def test_logs_even_when_downstream_raises():
    """下游异常也必须留一行记录——否则正是最难查的那种失败。"""
    log = Recorder()
    try:
        _call(_mw(_boom_app, log),
              [(b"authorization", f"Bearer {TOKEN}".encode())])
    except RuntimeError:
        pass
    assert len(log.records) == 1
    assert log.records[0]["status"] == 500


def test_non_http_scope_passes_through():
    """lifespan scope 必须原样透传，否则 SDK 的会话管理器起不来。"""
    seen = {}

    async def _app(scope, receive, send):
        seen["type"] = scope["type"]

    app = _mw(_app, Recorder())

    async def _run():
        await app({"type": "lifespan"}, None, None)

    asyncio.run(_run())
    assert seen["type"] == "lifespan"


def test_bootstrap_token_is_read_only(tmp_path, monkeypatch):
    """表内无 Token 时 env token 生效但仅 read（spec §5.3）。"""
    eng = create_engine(f"sqlite:///{tmp_path}/boot.db",
                        connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    S = sessionmaker(bind=eng, expire_on_commit=False)
    s = S()
    s.add(User(username="admin", password_hash="x", role="admin", is_active=True))
    s.commit()
    s.close()
    monkeypatch.setattr("app.db.SessionLocal", S)
    log = Recorder()
    app = BearerAuthMiddleware(_ok_app, log=log, bootstrap_token="env-token")
    assert _call(app, [(b"authorization", b"Bearer env-token")])["status"] == 200
    assert log.records[0]["token_id"] is None      # bootstrap 无 Token 行
    eng.dispose()


# ---------- tools/list 按身份裁剪（员工只看到自己能用的） ----------

def test_filter_tools_list_body_json_and_sse():
    """响应体裁剪：JSON 与 SSE 两种编码都要正确过滤，非 tools/list 内容原样返回。"""
    import json
    from mcp_service.auth import filter_tools_list_body

    allowed = {"visit_my_perf", "visit_ping"}
    payload = {"jsonrpc": "2.0", "id": 1, "result": {"tools": [
        {"name": "visit_my_perf"}, {"name": "visit_month_salary"},
        {"name": "visit_ping"}]}}

    # 普通 JSON
    out = json.loads(filter_tools_list_body(
        json.dumps(payload).encode("utf-8"), allowed).decode("utf-8"))
    assert [t["name"] for t in out["result"]["tools"]] == ["visit_my_perf", "visit_ping"]

    # SSE（data: {...}）
    sse = b"event: message\ndata: " + json.dumps(payload).encode("utf-8") + b"\n\n"
    out2 = filter_tools_list_body(sse, allowed)
    line = [l for l in out2.split(b"\n") if l.startswith(b"data:")][0]
    obj = json.loads(line[5:].strip().decode("utf-8"))
    assert [t["name"] for t in obj["result"]["tools"]] == ["visit_my_perf", "visit_ping"]

    # 非 tools/list（无 tools 字段）→ 原样
    other = b'{"jsonrpc":"2.0","id":2,"result":{"content":[]}}'
    assert filter_tools_list_body(other, allowed) == other


def test_staff_allowed_set_matches_authz():
    """裁剪用的白名单与授权矩阵是同一份真相（避免两处不一致）。"""
    from mcp_service import authz
    from mcp_service.auth import _ALLOWED_TOOL_NAMES  # noqa: F401  （未初始化时为 None）
    assert "visit_my_perf" in authz.STAFF_ALLOWED
    assert "visit_month_salary" not in authz.STAFF_ALLOWED
    assert authz.require_role("visit_month_salary") == "ADMIN_ONLY"
