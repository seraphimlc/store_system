# -*- coding: utf-8 -*-
"""一次性提交令牌（防重复提交的服务端机制）：发放 / 只能用一次 / 归属 / 过期 / 豁免。

服务端机制在 app/services/form_tokens.py + app/forms.py；本文件验证**行为**而非实现。
"""
import re
from datetime import datetime, timedelta

import app.db as appdb
from app.auth import SESSION_COOKIE, hash_password, read_session_token
from app.models import FormToken, User


def _seed_admin(client):
    db = appdb.SessionLocal()
    if db.query(User).filter(User.username == "admin").first() is None:
        db.add(User(username="admin", password_hash=hash_password("pw123456"),
                    display_name="管理员", role="admin", is_active=True))
        db.commit()
    db.close()


def _login(client, username="admin", password="pw123456"):
    client.post("/login", data={"username": username, "password": password},
                follow_redirects=False)
    return read_session_token(client.cookies.get(SESSION_COOKIE))["csrf"]


def _seed_staff(client, username="emp1", code="P1", name="甲"):
    from app.models import Person
    db = appdb.SessionLocal()
    if db.get(Person, code) is None:
        db.add(Person(code=code, display_name=name))
    if db.query(User).filter(User.username == username).first() is None:
        db.add(User(username=username, password_hash=hash_password("pw123456"),
                    display_name=name, role="staff", person_code=code,
                    is_active=True, status="active", must_change_password=False))
    db.commit()
    db.close()


def _token_from(client, path="/my/password"):
    m = re.search(r'name="_ft" value="([^"]+)"', client.get(path).text)
    return m.group(1) if m else ""


def test_form_post_without_token_rejected(client):
    """已登录的表单 POST 没有令牌 → 400（页面渲染时必然带令牌）。"""
    _seed_staff(client)
    _login(client, "emp1")
    r = client.post("/my/report", data={"csrf_token": "x", "area": "x",
                                        "p1_cnt": "1", "p2_cnt": "0"},
                    follow_redirects=False)
    assert r.status_code == 400
    db = appdb.SessionLocal()
    from app.models import StaffDailyReport
    assert db.query(StaffDailyReport).count() == 0      # 没有落库
    db.close()


def test_same_token_can_only_be_used_once(client):
    """同一个令牌只能用一次：第二次提交被拒（双击/返回再提交都挡得住）。"""
    from app.models import StaffDailyReport
    _seed_staff(client)
    csrf = _login(client, "emp1")
    ft = _token_from(client)
    assert ft
    data = {"_ft": ft, "csrf_token": csrf, "area": "渋谷", "p1_cnt": "3",
            "p2_cnt": "2"}
    first = client.post("/my/report", data=data, follow_redirects=False)
    assert first.status_code == 303 and first.headers["location"] == "/my/report"
    db = appdb.SessionLocal()
    assert db.query(StaffDailyReport).count() == 1      # 第一次成功落库
    db.close()
    second = client.post("/my/report", data=data, follow_redirects=False)
    assert second.status_code == 400                    # 令牌已作废
    db = appdb.SessionLocal()
    assert db.query(StaffDailyReport).count() == 1      # 没有产生第二条
    db.close()


def test_token_bound_to_user(client):
    """别人的令牌用不了（跨用户拒绝）。"""
    _seed_staff(client)
    _login(client, "emp1")
    ft = _token_from(client)
    client.get("/logout")
    _seed_staff(client, username="emp2", code="P2", name="乙")
    _login(client, "emp2")
    r = client.post("/my/report", data={"_ft": ft, "csrf_token": "x",
                                        "area": "x", "p1_cnt": "1", "p2_cnt": "0"},
                    follow_redirects=False)
    assert r.status_code == 400


def test_expired_token_rejected(client):
    """超过 TTL 的令牌失效。"""
    _seed_staff(client)
    _login(client, "emp1")
    db = appdb.SessionLocal()
    uid = db.query(User).filter(User.username == "emp1").one().id
    db.add(FormToken(token="stale-token-1", user_id=uid,
                     created_at=datetime.utcnow() - timedelta(hours=3)))
    db.commit()
    db.close()
    r = client.post("/my/report", data={"_ft": "stale-token-1", "csrf_token": "x",
                                        "area": "x", "p1_cnt": "1", "p2_cnt": "0"},
                    follow_redirects=False)
    assert r.status_code == 400


def test_unauthenticated_post_still_redirects_to_login(client):
    """未登录 POST 不在这里拦截 → 保持原来的「跳登录」行为（不是 400）。"""
    r = client.post("/my/report", data={"area": "x", "p1_cnt": "1", "p2_cnt": "0"},
                    follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith("/login")


def test_exempt_paths_need_no_token(client):
    """机器端点/登录页豁免：/login 不带令牌也能登（OAuth 端点同理，由路由自己管）。"""
    _seed_admin(client)
    r = client.post("/login", data={"username": "admin", "password": "pw123456"},
                    follow_redirects=False)
    assert r.status_code == 302
    assert not r.headers["location"].startswith("/login")


def test_issue_and_purge(client):
    """发放与清理：issue 写库；purge 删掉过期记录（含已用过的）。"""
    from app.services import form_tokens
    db = appdb.SessionLocal()
    tk = form_tokens.issue(db, None)
    assert db.get(FormToken, tk) is not None
    assert form_tokens.stats(db)["unused"] >= 1
    db.add(FormToken(token="old-1", user_id=None,
                     created_at=datetime.utcnow() - timedelta(hours=48)))
    db.commit()
    n = form_tokens.purge(db)
    assert n >= 1
    assert db.get(FormToken, "old-1") is None
    db.close()
