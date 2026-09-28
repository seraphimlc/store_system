# -*- coding: utf-8 -*-
"""MCP Token 管理员代发/吊销、员工状态联动提示、/mcp-audit（管理员审计）。

注：员工端自助签发页 /my/token 已于 2026-09-28 删除（员工走 OAuth，不需要管凭证）。


约定：
- 明文 token 只显示一次（签发后那个响应页），库内只存前缀 + sha256；
- 仅 `status == 'active'` 且 `is_active` 时 token 有效（spec §三）；
- 写操作一律 CSRF + 登录校验。
"""
import hashlib
import re
from datetime import datetime, timedelta
from urllib.parse import parse_qs, unquote, urlparse

import app.db as appdb
from app.auth import hash_password
from app.models import ApiToken, McpAuditLog, User


def _db():
    return appdb.SessionLocal()


def _seed():
    db = _db()
    db.add(User(username="admin", password_hash=hash_password("pw123456"),
                display_name="管理员", role="admin", is_active=True,
                status="active", must_change_password=False))
    for code, uname, disp in (("P1", "emp1", "员工甲"), ("P2", "emp2", "员工乙")):
        db.add(User(username=uname, password_hash=hash_password("pass123"),
                    display_name=disp, role="staff", person_code=code,
                    is_active=True, status="active", must_change_password=False))
    db.commit()
    db.close()


def _login(client, username, password="pass123"):
    if username == "admin":
        password = "pw123456"
    return client.post("/login", data={"username": username,
                                       "password": password},
                       follow_redirects=False)


def _csrf_of(client, path):
    page = client.get(path).text
    m = re.search(r'name="csrf_token" value="([^"]+)"', page)
    return m.group(1) if m else ""


def _uid(username):
    db = _db()
    u = db.query(User).filter(User.username == username).one()
    db.close()
    return u.id


def _token_rows(username):
    db = _db()
    u = db.query(User).filter(User.username == username).one()
    rows = db.query(ApiToken).filter(ApiToken.user_id == u.id).all()
    db.close()
    return rows


def _digest(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------- 未登录 ----------
def test_unauthenticated_redirects_to_login(client):
    r = client.get("/mcp-audit", follow_redirects=False)
    assert r.status_code == 302, f"/mcp-audit 未登录应 302，实际 {r.status_code}"
    assert r.headers["location"].startswith("/login")


def test_employee_self_service_token_page_removed(client):
    """员工端自助签发页已删除：导航无入口、员工被中间件拦住、路由本身也不存在。"""
    _seed()
    _login(client, "emp1")
    # 导航里没有入口
    assert "我的 Token" not in client.get("/my/perf").text
    # 员工访问 → 中间件按白名单拦到 /my/perf（不再放行到自助页）
    r = client.get("/my/token", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("/my/perf")
    # 路由本身已删除：管理员绕过员工白名单 → 404 / 405
    client.get("/logout")
    _login(client, "admin")
    assert client.get("/my/token", follow_redirects=False).status_code == 404
    assert client.post("/my/token/issue", data={"name": "x", "scope": "read"},
                       follow_redirects=False).status_code in (404, 405)





# ---------- CSRF ----------
def test_write_requires_csrf(client):
    """管理员代发/吊销同样要 CSRF。"""
    _seed()
    _login(client, "admin")
    uid = _uid("emp1")
    r = client.post(f"/staff-admin/{uid}/tokens/issue",
                    data={"name": "x", "scope": "read", "days": "90",
                          "csrf_token": "bad"}, follow_redirects=False)
    assert r.status_code == 400
    r = client.post(f"/staff-admin/{uid}/tokens/999/revoke",
                    data={"csrf_token": "bad"}, follow_redirects=False)
    assert r.status_code == 400


# ---------- 员工访问 /mcp-audit 被拒 ----------
def test_staff_blocked_from_audit(client):
    _seed()
    _login(client, "emp1")
    r = client.get("/mcp-audit", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith("/my/perf")


# ---------- 管理员：任意员工 token 列表 + 代发 + 吊销 ----------
def test_admin_issue_revoke_for_staff(client):
    _seed()
    _login(client, "admin")
    uid = _uid("emp1")
    csrf = _csrf_of(client, "/staff-admin")
    r = client.post(f"/staff-admin/{uid}/tokens/issue",
                    data={"name": "代发 token", "scope": "read,write",
                          "days": "permanent", "csrf_token": csrf},
                    follow_redirects=False)
    assert r.status_code == 303
    loc = urlparse(r.headers["location"])
    assert loc.path == "/staff-admin"
    raw = parse_qs(loc.query).get("new_token", [""])[0]
    assert len(raw) >= 30
    # 明文只显示一次（管理员页）
    page = client.get(r.headers["location"]).text
    assert raw in page
    # 员工 token 列表可见（前缀/scope/状态）
    page2 = client.get("/staff-admin").text
    assert raw[:8] in page2
    assert "read,write" in page2
    assert "有效" in page2
    assert raw not in page2
    # 管理员永久签发 → expires_at 为空
    rows = _token_rows("emp1")
    assert len(rows) == 1
    assert rows[0].scopes == "read,write"
    assert rows[0].expires_at is None
    # 管理员吊销
    csrf = _csrf_of(client, "/staff-admin")
    r = client.post(f"/staff-admin/{uid}/tokens/{rows[0].id}/revoke",
                    data={"csrf_token": csrf}, follow_redirects=False)
    assert r.status_code == 303
    rows = _token_rows("emp1")
    assert rows[0].revoked_at is not None
    # 吊销后状态显示
    page3 = client.get("/staff-admin").text
    assert "已吊销" in page3


# ---------- 员工状态变更 → 页面提示 token 失效/恢复 ----------
def test_staff_status_change_token_invalidation_hint(client):
    _seed()
    _login(client, "admin")
    uid = _uid("emp1")
    csrf = _csrf_of(client, "/staff-admin")
    client.post(f"/staff-admin/{uid}/tokens/issue",
                data={"name": "t1", "scope": "read", "days": "90",
                      "csrf_token": csrf}, follow_redirects=False)
    # 改状态为请假 → 提示 token 失效
    csrf = _csrf_of(client, "/staff-admin")
    r = client.post(f"/staff-admin/{uid}/status",
                    data={"new_status": "leave", "csrf_token": csrf},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "token 已随之失效" in unquote(r.headers["location"])
    page = client.get("/staff-admin").text
    assert "该员工 token 已随之失效" in page
    assert "随员工状态失效" in page
    # 改回在岗 → 提示恢复
    csrf = _csrf_of(client, "/staff-admin")
    r = client.post(f"/staff-admin/{uid}/status",
                    data={"new_status": "active", "csrf_token": csrf},
                    follow_redirects=False)
    assert "token 已恢复有效" in unquote(r.headers["location"])
    page = client.get("/staff-admin").text
    assert "有效" in page


# ---------- /mcp-audit：列/过滤/分页 ----------
def test_audit_page_columns_and_filters(client):
    _seed()
    _login(client, "admin")
    uid1 = _uid("emp1")
    db = _db()
    now = datetime.utcnow()
    for i in range(3):
        db.add(McpAuditLog(user_id=uid1, token_id=1,
                           tool="visit_overview",
                           params_json='{"month":"2026-08"}',
                           ok=True, error_code=None, duration_ms=12,
                           created_at=now - timedelta(days=1)))
    db.add(McpAuditLog(user_id=None, token_id=None, tool="visit_whoami",
                       params_json="{}", ok=False,
                       error_code="UNAUTHORIZED", duration_ms=1,
                       created_at=now - timedelta(days=2)))
    db.commit()
    db.close()
    page = client.get("/mcp-audit").text
    assert "MCP 调用审计" in page
    assert "visit_overview" in page
    assert "UNAUTHORIZED" in page
    assert "参数摘要" in page
    # 按人过滤
    page = client.get(f"/mcp-audit?user_id={uid1}").text
    assert "visit_overview" in page
    assert "visit_whoami" not in page
    # 按工具过滤
    page = client.get("/mcp-audit?tool=whoami").text
    assert "visit_whoami" in page
    assert "visit_overview" not in page


def test_audit_pagination(client):
    _seed()
    _login(client, "admin")
    uid1 = _uid("emp1")
    db = _db()
    for i in range(60):
        db.add(McpAuditLog(user_id=uid1, tool="tool_%02d" % i, ok=True,
                           created_at=datetime.utcnow()))
    db.commit()
    db.close()
    p1 = client.get("/mcp-audit").text
    assert "tool_59" in p1 and "tool_00" not in p1
    assert "下一页" in p1
    p2 = client.get("/mcp-audit?page=2").text
    assert "tool_00" in p2 and "tool_59" not in p2
    assert "上一页" in p2
