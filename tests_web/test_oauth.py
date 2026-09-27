# -*- coding: utf-8 -*-
"""MCP OAuth（SSO）验收测试（规格 docs/specs-mcp-oauth.md §六）。

覆盖 9 项验收：
1  完整授权码 + PKCE 流程 → 换出的 token 能调 MCP 工具成功
   （E2E：真实 MCP 服务子进程 + 原始 JSON-RPC，见 test_e2e_oauth_token_calls_mcp_tool）
2  未登录 authorize → 跳登录页；登录后继续并拿到 code
3  PKCE 校验失败（错误 verifier）→ invalid_grant
4  code 重放 → 第二次 invalid_grant，且第一次换出的 token 被吊销
5  redirect_uri 不匹配 → invalid_request（不发 code）
6  员工申请 write → 实际只拿到 read（调写工具被 FORBIDDEN_TOOL，E2E 验证）
7  refresh 轮换：旧 refresh 再用 → invalid_grant；新 access 可用
8  员工状态改 leave → 换出的 token 立即失效（复用现有联动，E2E 走真实 resolve）
9  审计：OAuth 换出的 token 调用工具时 mcp_audit_log 有 user_id（E2E 验证）

另补：发现端点 / DCR 校验 / 拒绝授权 / CSRF / VISIT_OAUTH_ENABLED=0 / code 5 分钟。

说明：mcp_service 为 Python ≥3.10 独立 venv（PEP 604），主 venv（3.9）无法导入，
故「换出的 token 调 MCP 工具」在 E2E 测试里以真实服务子进程验证；
web 侧断言 api_tokens 行的摘要/scope/有效期（即 mcp_service.tokens.resolve 消费的字段）。
用临时库 fixture（tests_web/conftest.py 的 client），不碰真库。
"""
import base64
import hashlib
import html
import json
import os
import re
import socket
import subprocess
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import pytest

import app.db as appdb
from app.auth import hash_password
from app.config import get_settings
from app.models import ApiToken, McpAuditLog, OAuthCode, OAuthRefreshToken, User

REPO_ROOT = Path(__file__).resolve().parent.parent
CB = "http://127.0.0.1:9999/callback"


@pytest.fixture(autouse=True)
def _oauth_env(monkeypatch):
    """OAuth 环境：issuer 固定 + 缓存清理（get_settings 带 lru_cache）。"""
    monkeypatch.setenv("VISIT_OAUTH_ISSUER", "https://oauth.test")
    monkeypatch.setenv("VISIT_MCP_PUBLIC_HOST", "mcp.test")
    monkeypatch.setenv("VISIT_OAUTH_ENABLED", "1")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _db():
    return appdb.SessionLocal()


def _seed():
    db = _db()
    db.add(User(username="admin", password_hash=hash_password("pw123456"),
                display_name="管理员", role="admin", is_active=True,
                status="active", must_change_password=False))
    db.add(User(username="emp1", password_hash=hash_password("pass123"),
                display_name="员工甲", role="staff", person_code="P1",
                is_active=True, status="active", must_change_password=False))
    db.commit()
    db.close()


def _uid(username):
    db = _db()
    u = db.query(User).filter(User.username == username).one()
    db.close()
    return u.id


def _user(username):
    db = _db()
    u = db.query(User).filter(User.username == username).one()
    db.close()
    return u


def _login(client, username="admin", password=None):
    password = password or ("pw123456" if username == "admin" else "pass123")
    return client.post("/login", data={"username": username,
                                       "password": password},
                       follow_redirects=False)


def _register(client, name="WorkBuddy 测试", uris=None):
    r = client.post("/oauth/register",
                    json={"client_name": name,
                          "redirect_uris": uris or [CB]})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["client_id"]
    assert data["redirect_uris"] == (uris or [CB])
    assert data["token_endpoint_auth_method"] == "none"
    return data["client_id"]


def _pkce():
    verifier = "v" * 43
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _authz_params(client_id, redirect_uri=CB, scope="read", state="st-123",
                  challenge=None, method="S256"):
    if challenge is None:
        _, challenge = _pkce()
    return {"response_type": "code", "client_id": client_id,
            "redirect_uri": redirect_uri, "scope": scope, "state": state,
            "code_challenge": challenge, "code_challenge_method": method}


def _consent_hidden(html_text):
    """提取确认页全部 hidden 字段（模拟浏览器表单提交）。"""
    out = {}
    for m in re.finditer(r'<input type="hidden" name="([^"]+)" value="([^"]*)"',
                         html_text):
        out[m.group(1)] = html.unescape(m.group(2))
    return out


def _approve(client, params):
    """GET authorize（已登录）→ 确认页 → 同意 → 返回 302 location。"""
    page = client.get("/oauth/authorize?" + urlencode(params))
    assert page.status_code == 200, page.text
    fields = _consent_hidden(page.text)
    assert fields.get("csrf_token"), "确认页必须带 CSRF"
    assert "WorkBuddy 测试" in page.text          # 展示客户端名（§五.6）
    assert "权限范围" in page.text                # 展示 scope
    fields["decision"] = "approve"
    r = client.post("/oauth/authorize", data=fields, follow_redirects=False)
    assert r.status_code == 302, r.text
    return r.headers["location"]


def _code_from_location(loc):
    q = parse_qs(urlparse(loc).query)
    return q.get("code", [""])[0], q.get("state", [""])[0]


def _exchange(client, code, client_id, verifier, redirect_uri=CB):
    return client.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": code,
        "client_id": client_id, "redirect_uri": redirect_uri,
        "code_verifier": verifier})


def _full_flow(client, username="admin", scope="read", verifier=None,
               state="st-123", client_id=None, redirect_uri=CB):
    """注册客户端 → 登录 → 授权确认 → 换 token。返回 (resp, 明细)。"""
    client_id = client_id or _register(client)
    verifier = verifier or "v" * 43
    _, challenge = _pkce()
    _login(client, username)
    loc = _approve(client, _authz_params(
        client_id, redirect_uri=redirect_uri, scope=scope, state=state,
        challenge=challenge))
    code, got_state = _code_from_location(loc)
    assert code and got_state == state          # state 原样回传（§五.5）
    resp = _exchange(client, code, client_id, verifier, redirect_uri)
    return resp, {"code": code, "client_id": client_id,
                  "verifier": verifier, "state": state}


# ---------- 验收 1：完整流程换出的 token 是一行一等公民 api_tokens ----------
# （真实 MCP 工具调用在下方 E2E 测试里验证）

def test_full_flow_issues_first_class_api_token(client):
    _seed()
    resp, _ = _full_flow(client, "admin", scope="read,write")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["token_type"] == "Bearer"
    assert data["expires_in"] == 24 * 3600
    assert data["scope"] == "read,write"          # 管理员按请求授予
    at, rt = data["access_token"], data["refresh_token"]
    assert len(at) >= 30 and len(rt) >= 30

    # 库内只存摘要：api_tokens 一行 = OAuth 换出的 access token（规格 §一）
    uid = _uid("admin")
    db = _db()
    rows = db.query(ApiToken).filter(ApiToken.user_id == uid).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.token_hash == hashlib.sha256(at.encode()).hexdigest()
    assert row.scopes == "read,write"
    assert row.name.startswith("OAuth")
    # access 24h / refresh 90d / code 5min（规格 §四、§五.4）
    remain = row.expires_at - row.created_at
    assert timedelta(hours=23) < remain < timedelta(hours=25)
    oc = db.query(OAuthCode).one()
    assert oc.expires_at - oc.created_at == timedelta(minutes=5)
    ort = db.query(OAuthRefreshToken).one()
    assert timedelta(days=89) < ort.expires_at - ort.created_at < timedelta(days=91)
    assert ort.revoked_at is None
    db.close()


# ---------- 验收 2：未登录 → 登录页；登录后继续 ----------

def test_unauthenticated_authorize_redirects_to_login_then_continues(client):
    _seed()
    cid = _register(client)
    params = _authz_params(cid)
    r = client.get("/oauth/authorize?" + urlencode(params),
                   follow_redirects=False)
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith("/login?next=")
    next_url = parse_qs(urlparse(loc).query)["next"][0]
    assert next_url.startswith("/oauth/authorize?")     # 站内相对回跳

    # 登录（带 next）→ 回到 authorize → 确认 → 换 token 成功
    r2 = client.post("/login", data={"username": "admin", "password": "pw123456",
                                     "next": next_url}, follow_redirects=False)
    assert r2.status_code == 302
    assert r2.headers["location"] == next_url
    page = client.get(next_url)
    assert page.status_code == 200
    fields = _consent_hidden(page.text)
    fields["decision"] = "approve"
    r3 = client.post("/oauth/authorize", data=fields, follow_redirects=False)
    assert r3.status_code == 302
    code, _ = _code_from_location(r3.headers["location"])
    verifier, _ = _pkce()
    resp = _exchange(client, code, cid, verifier)
    assert resp.status_code == 200, resp.text
    assert resp.json()["access_token"]


# ---------- 验收 3：PKCE 失败 ----------

def test_wrong_pkce_verifier_invalid_grant(client):
    _seed()
    resp, _ = _full_flow(client, verifier="WRONG" + "x" * 38)
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"


# ---------- 验收 4：code 重放 → invalid_grant + 吊销第一次 token ----------

def test_code_replay_revokes_issued_token(client):
    from app.services import mcp_tokens as web_tokens
    _seed()
    resp, detail = _full_flow(client, "admin")
    assert resp.status_code == 200
    at = resp.json()["access_token"]

    db = _db()
    row = db.query(ApiToken).filter(
        ApiToken.token_prefix == at[:8]).one()
    assert web_tokens.token_status(row, _user("admin")) == "active"
    db.close()

    # 重放同一个 code
    r2 = _exchange(client, detail["code"], detail["client_id"],
                   detail["verifier"])
    assert r2.status_code == 400
    assert r2.json()["error"] == "invalid_grant"

    # 第一次换出的 token 已被吊销（重放防务，规格 §五.2）
    db = _db()
    row2 = db.query(ApiToken).filter(
        ApiToken.token_prefix == at[:8]).one()
    assert row2.revoked_at is not None
    assert web_tokens.token_status(row2, _user("admin")) == "revoked"
    db.close()


# ---------- 验收 5：redirect_uri 不匹配 → invalid_request，不发 code ----------

def test_redirect_uri_mismatch_invalid_request_no_code(client):
    _seed()
    cid = _register(client)
    _login(client)
    params = _authz_params(cid, redirect_uri="http://evil.example/cb")
    r = client.get("/oauth/authorize?" + urlencode(params))
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_request"
    db = _db()
    assert db.query(OAuthCode).count() == 0             # 不发 code
    db.close()


# ---------- 验收 6：员工申请 write → 只拿到 read（E2E 验证 FORBIDDEN_TOOL） ----------

def test_staff_write_downgraded_to_read(client):
    _seed()
    resp, _ = _full_flow(client, "emp1", scope="read,write")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["scope"] == "read"                      # 降级，不报错（§四）

    db = _db()
    row = db.query(ApiToken).filter(ApiToken.user_id == _uid("emp1")).one()
    assert row.scopes == "read"
    db.close()


# ---------- 验收 7：refresh 轮换 ----------

def test_refresh_rotation_old_refresh_rejected(client):
    from app.services import mcp_tokens as web_tokens
    _seed()
    resp, _ = _full_flow(client, "admin")
    assert resp.status_code == 200
    a1, r1 = resp.json()["access_token"], resp.json()["refresh_token"]
    cid = _cid_of(client)

    # 第一次刷新 → 新 access + 新 refresh（轮换）
    r2 = client.post("/oauth/token", data={"grant_type": "refresh_token",
                                           "refresh_token": r1,
                                           "client_id": cid})
    assert r2.status_code == 200, r2.text
    d2 = r2.json()
    assert d2["access_token"] and d2["refresh_token"]
    assert d2["refresh_token"] != r1                    # 轮换出新 refresh
    assert d2["access_token"] != a1
    db = _db()
    new_row = db.query(ApiToken).filter(
        ApiToken.token_prefix == d2["access_token"][:8]).one()
    assert web_tokens.token_status(new_row, _user("admin")) == "active"
    db.close()

    # 旧 refresh 再用 → invalid_grant
    r3 = client.post("/oauth/token", data={"grant_type": "refresh_token",
                                           "refresh_token": r1,
                                           "client_id": cid})
    assert r3.status_code == 400
    assert r3.json()["error"] == "invalid_grant"

    # 新 refresh 仍可用 → 200
    r4 = client.post("/oauth/token", data={"grant_type": "refresh_token",
                                           "refresh_token": d2["refresh_token"],
                                           "client_id": cid})
    assert r4.status_code == 200
    db = _db()
    assert db.query(OAuthRefreshToken).filter(
        OAuthRefreshToken.revoked_at.is_not(None)).count() >= 1
    db.close()


def _cid_of(client):
    """取最近注册的客户端 id（测试内注册且未显式保存时用）。"""
    db = _db()
    from app.models import OAuthClient
    row = db.query(OAuthClient).order_by(OAuthClient.id.desc()).first()
    db.close()
    assert row is not None
    return row.client_id


# ---------- 验收 8：员工 leave → token 失效（web 侧联动；E2E 走真实 resolve） ----------

def test_staff_leave_invalidates_oauth_token_web_side(client):
    from app.services import mcp_tokens as web_tokens
    _seed()
    resp, _ = _full_flow(client, "emp1")
    assert resp.status_code == 200
    at = resp.json()["access_token"]

    db = _db()
    row = db.query(ApiToken).filter(
        ApiToken.token_prefix == at[:8]).one()
    assert web_tokens.token_status(row, _user("emp1")) == "active"
    u = db.query(User).filter(User.username == "emp1").one()
    u.status = "leave"                                  # 休假（is_active 仍 True）
    db.commit()
    # 复用现有联动：非 active → token 随之失效（规格 §四）
    assert web_tokens.token_status(row, u) == "user_inactive"
    db.close()


# ---------- 发现端点 ----------

def test_discovery_documents(client):
    pr = client.get("/.well-known/oauth-protected-resource")
    assert pr.status_code == 200
    body = pr.json()
    # resource 必须与客户端连接的 MCP URL 同源（WorkBuddy SDK 按 origin 精确比对）；
    # 本 fixture 未设 VISIT_OAUTH_RESOURCE → 回退 issuer
    assert body["resource"] == "https://oauth.test"
    assert body["authorization_servers"] == ["https://oauth.test"]
    assert body["scopes_supported"] == ["read", "write"]

    as_ = client.get("/.well-known/oauth-authorization-server")
    assert as_.status_code == 200
    d = as_.json()
    assert d["issuer"] == "https://oauth.test"
    assert d["authorization_endpoint"] == "https://oauth.test/oauth/authorize"
    assert d["token_endpoint"] == "https://oauth.test/oauth/token"
    assert d["registration_endpoint"] == "https://oauth.test/oauth/register"
    assert d["code_challenge_methods_supported"] == ["S256"]
    assert d["grant_types_supported"] == ["authorization_code", "refresh_token"]
    assert d["token_endpoint_auth_methods_supported"] == ["none"]


# ---------- DCR 校验 ----------

def test_dcr_rejects_invalid_metadata(client):
    r = client.post("/oauth/register", json={"client_name": "x"})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_client_metadata"
    r = client.post("/oauth/register",
                    json={"redirect_uris": ["not-a-uri"]})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_client_metadata"
    r = client.post("/oauth/register", data="not json",
                    headers={"content-type": "text/plain"})
    assert r.status_code == 400


# ---------- 拒绝授权 / CSRF ----------

def test_consent_deny_redirects_access_denied(client):
    _seed()
    cid = _register(client)
    _login(client)
    params = _authz_params(cid)
    page = client.get("/oauth/authorize?" + urlencode(params))
    fields = _consent_hidden(page.text)
    fields["decision"] = "deny"
    r = client.post("/oauth/authorize", data=fields, follow_redirects=False)
    assert r.status_code == 302
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["error"] == ["access_denied"]              # 规格 §五.6
    assert q["state"] == ["st-123"]                     # state 原样回传
    db = _db()
    assert db.query(OAuthCode).count() == 0
    db.close()


def test_consent_requires_csrf(client):
    _seed()
    cid = _register(client)
    _login(client)
    params = _authz_params(cid)
    page = client.get("/oauth/authorize?" + urlencode(params))
    fields = _consent_hidden(page.text)
    fields["csrf_token"] = "bad-token"
    fields["decision"] = "approve"
    r = client.post("/oauth/authorize", data=fields)
    assert r.status_code == 400


# ---------- VISIT_OAUTH_ENABLED=0 ----------

def test_disabled_oauth_returns_404_but_my_token_works(client, monkeypatch):
    _seed()
    monkeypatch.setenv("VISIT_OAUTH_ENABLED", "0")
    get_settings.cache_clear()
    # authorize / token / register 一律 404（规格 §七）
    r = client.post("/oauth/register",
                    json={"client_name": "x", "redirect_uris": [CB]})
    assert r.status_code == 404
    r = client.get("/oauth/authorize?" + urlencode(_authz_params("cid-x")))
    assert r.status_code == 404
    r = client.post("/oauth/token", data={"grant_type": "authorization_code"})
    assert r.status_code == 404
    # 发现端点保留
    assert client.get("/.well-known/oauth-protected-resource").status_code == 200
    # /my/token 自助签发不受影响（登录后 200）
    _login(client, "admin")
    assert client.get("/my/token").status_code == 200
    get_settings.cache_clear()


# ---------- code 过期（5 分钟） ----------

def test_code_expires_at_5min(client):
    _seed()
    resp, _ = _full_flow(client, "admin")
    assert resp.status_code == 200
    db = _db()
    oc = db.query(OAuthCode).one()
    assert oc.expires_at - oc.created_at == timedelta(minutes=5)
    db.close()


# ======================================================================
# E2E：真实 MCP 服务子进程 —— 换出的 token 调 MCP 工具（验收 1/6/8/9）
# ======================================================================

@pytest.fixture()
def e2e_env(tmp_path):
    """文件库 + 独立 app，供真实 MCP 服务子进程共享同一份数据。"""
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import Base
    from app.main import create_app

    db_path = str(tmp_path / "oauth_e2e.db")
    eng = create_engine(f"sqlite:///{db_path}",
                        connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    S = sessionmaker(bind=eng, future=True, expire_on_commit=False)
    old_eng, old_sl = appdb._ENGINE, appdb.SessionLocal
    appdb._ENGINE = eng
    appdb.SessionLocal = S
    app = create_app()
    try:
        with TestClient(app) as c:
            yield c, db_path, eng
    finally:
        appdb._ENGINE, appdb.SessionLocal = old_eng, old_sl
        eng.dispose()


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_port(port: int, timeout: float = 60) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), 0.3).close()
            return
        except OSError:
            time.sleep(0.2)
    pytest.fail(f"MCP 服务未在 {timeout}s 内监听 {port}")


def _sse_json(r: httpx.Response):
    """解析 streamable HTTP 响应（SSE 或纯 JSON）。"""
    text = r.text or ""
    data_lines = [ln[6:] for ln in text.splitlines() if ln.startswith("data:")]
    if data_lines:
        return json.loads("".join(data_lines))
    try:
        return r.json()
    except Exception:  # noqa: BLE001
        return {}


def _mcp_call(port: int, token: str, tool: str, args: dict) -> tuple:
    """原始 JSON-RPC 握手 + tools/call（模拟 MCP 客户端）。

    返回 (初始化响应, tools/call 解析体)。调用方按需断言状态码
    （如员工 leave 后 token 失效 → 初始化即 401，属于预期）。
    """
    base = f"http://127.0.0.1:{port}/mcp"
    headers = {"Authorization": f"Bearer {token}",
               "Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    r = httpx.post(base, json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                               "params": {"protocolVersion": "2025-11-25",
                                          "capabilities": {},
                                          "clientInfo": {"name": "e2e",
                                                         "version": "1.0"}}},
                   headers=headers, timeout=20)
    if r.status_code != 200:
        return r, None
    sid = r.headers.get("mcp-session-id")
    assert sid, "初始化必须返回 Mcp-Session-Id"
    httpx.post(base, json={"jsonrpc": "2.0", "method": "notifications/initialized"},
               headers={**headers, "Mcp-Session-Id": sid}, timeout=20)
    r2 = httpx.post(base,
                    json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                          "params": {"name": tool, "arguments": args}},
                    headers={**headers, "Mcp-Session-Id": sid}, timeout=20)
    return r2, _sse_json(r2)


def test_e2e_oauth_token_calls_mcp_tool(e2e_env, tmp_path):
    """验收 1/6/8/9：真实 MCP 服务 + OAuth 换出的 token。"""
    client, db_path, eng = e2e_env
    _seed()

    # 管理员完整流程 → read,write
    resp, _ = _full_flow(client, "admin", scope="read,write")
    assert resp.status_code == 200
    at_admin = resp.json()["access_token"]
    # 员工申请 write → 实际只拿到 read（验收 6 的前置）
    resp2, _ = _full_flow(client, "emp1", scope="read,write")
    assert resp2.status_code == 200
    at_staff = resp2.json()["access_token"]
    assert resp2.json()["scope"] == "read"

    port = _free_port()
    env = dict(os.environ)
    env.update({
        "VISIT_MCP_TOKEN": "bootstrap-dummy",
        "VISIT_MCP_STATIC_TOKENS": "0",
        "DATABASE_URL": f"sqlite:///{db_path}",
        "VISIT_MCP_LOG": str(tmp_path / "mcp_req.jsonl"),
        "VISIT_MCP_PORT": str(port),
        "VISIT_MCP_HOST": "127.0.0.1",
        "VISIT_OAUTH_ISSUER": "https://oauth.test",
        "VISIT_MCP_PUBLIC_HOST": "mcp.test",
    })
    proc = subprocess.Popen(
        ["mcp_service/.venv/bin/python", "mcp_service/server.py"],
        env=env, cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        _wait_port(port)

        # 验收 1：管理员 token 调 MCP 读工具成功
        r, body = _mcp_call(port, at_admin, "visit_whoami", {})
        assert r.status_code == 200, r.text[:300]
        assert body["result"]["structuredContent"]["ok"] is True
        assert body["result"]["structuredContent"]["data"]["role"] == "admin"
        # 走两阶段审计的工具（visit_overview 经 authz.dispatch）
        r2, body2 = _mcp_call(port, at_admin, "visit_overview",
                              {"month": "2026-09"})
        assert r2.status_code == 200, r2.text[:300]
        assert body2["result"]["structuredContent"]["ok"] is True

        # 验收 9：审计有 user_id（与 /my/token 签发无差别）
        from sqlalchemy.orm import sessionmaker
        S = sessionmaker(bind=eng, expire_on_commit=False)
        s = S()
        alog = s.query(McpAuditLog).filter(
            McpAuditLog.tool == "visit_overview").one()
        assert alog.user_id == _uid("admin")
        assert alog.token_id is not None
        assert alog.ok is True
        s.close()

        # 验收 6：员工（只读）调写工具 → FORBIDDEN_TOOL（授权矩阵拦截）
        rw, body_w = _mcp_call(port, at_staff, "visit_upload", {})
        err = body_w["result"]["structuredContent"]["error"]
        assert err["code"] == "FORBIDDEN_TOOL"

        # 验收 8：员工 leave → token 立即失效（MCP 401，复用现有联动）
        s = S()
        u = s.query(User).filter(User.username == "emp1").one()
        u.status = "leave"
        s.commit()
        s.close()
        r_leave, _ = _mcp_call(port, at_staff, "visit_whoami", {})
        assert r_leave.status_code == 401
        hdr = r_leave.headers.get("www-authenticate", "")
        assert hdr.startswith("Bearer resource_metadata=")
        assert hdr.endswith("/.well-known/oauth-protected-resource\"")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        if proc.stdout is not None:
            out = proc.stdout.read().decode("utf-8", "replace")
            if "TRACE" in os.environ.get("OAUTH_E2E_DEBUG", ""):
                print("=== MCP SERVER OUTPUT ===\n" + out[:4000])

def test_authorize_blocked_when_user_not_working(client):
    """账号非在岗（请假/停用/离职）不得授权。

    为什么必须拦：MCP 每次调用都校验 `status == "active"`（请假即失效），
    若授权环节放行，用户会以为授权成功、实际所有工具都不可用。
    """
    from app.models import User
    _seed()
    cid = _register(client)
    _login(client, "emp1")
    db = _db()
    u = db.query(User).filter(User.username == "emp1").one()
    u.status = "leave"
    db.commit()
    db.close()
    try:
        _, challenge = _pkce()
        r = client.get("/oauth/authorize", params=_authz_params(
            cid, scope="read", challenge=challenge), follow_redirects=False)
        assert r.status_code == 403
        assert "请假" in r.text
    finally:
        db = _db()
        u = db.query(User).filter(User.username == "emp1").one()
        u.status = "active"
        db.commit()
        db.close()
