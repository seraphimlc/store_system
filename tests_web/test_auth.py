# -*- coding: utf-8 -*-
"""登录/会话测试。"""
import pytest

import app.db as appdb
from app.auth import hash_password, new_session_token, read_session_token, SESSION_COOKIE
from app.models import User


def _db():
    # 动态取 app.db.SessionLocal：conftest 在 fixture 里替换了该模块属性
    return appdb.SessionLocal()


@pytest.fixture
def admin_user(client):
    """依赖 client 确保表已建；client 为独立内存库，无需清理用户。"""
    db = _db()
    u = User(username="admin", password_hash=hash_password("pw123456"),
             display_name="管理员", role="admin", is_active=True)
    db.add(u)
    db.commit()
    db.close()
    return u


def login(client, username="admin", password="pw123456"):
    return client.post("/login", data={"username": username, "password": password},
                       follow_redirects=False)


def test_anonymous_redirected(client):
    # / → /perf（需登录）→ /login；任意环节均要求登录
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc in ("/perf", "/login")
    final = client.get("/perf", follow_redirects=True)
    assert str(final.url).endswith("/login")


def test_wrong_password(client, admin_user):
    r = login(client, password="wrong")
    assert r.status_code == 401
    assert "用户名或密码错误" in r.text


def test_login_flow(client, admin_user):
    r = login(client)
    assert r.status_code == 302
    assert SESSION_COOKIE in r.headers["set-cookie"]
    # /perf 即现行绩效工资页（登录后管理员落地页）
    r2 = client.get("/perf")
    assert r2.status_code == 200
    assert "绩效工资" in r2.text
    assert "管理员" in r2.text
    # 旧版 /v3/perf 兼容别名仍可用
    r3 = client.get("/v3/perf")
    assert r3.status_code == 200
    assert "绩效工资" in r3.text


def test_logout_requires_csrf(client, admin_user):
    login(client)
    r = client.post("/logout")
    assert r.status_code in (400, 422)  # 缺 csrf 字段：FastAPI 校验 422 或业务 400
    token = client.cookies.get(SESSION_COOKIE)
    data = read_session_token(token)
    r = client.post("/logout", data={"csrf_token": data["csrf"]},
                    follow_redirects=False)
    assert r.status_code == 302
    assert client.cookies.get(SESSION_COOKIE) is None
    assert client.get("/", follow_redirects=False).status_code == 302


def test_session_token_roundtrip():
    t = new_session_token(7)
    d = read_session_token(t)
    assert d["uid"] == 7 and d["csrf"]
    assert read_session_token("garbage") is None
    assert read_session_token(None) is None
