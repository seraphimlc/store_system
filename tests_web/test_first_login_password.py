# -*- coding: utf-8 -*-
"""首次登录/口令重置后必须改密测试。"""
import re

import app.db as appdb
from app.auth import hash_password
from app.models import Person, User


def _db():
    return appdb.SessionLocal()


def _seed():
    db = _db()
    db.add(User(username="admin", password_hash=hash_password("pw123456"),
                display_name="管理员", role="admin", is_active=True))
    db.add(Person(code="111", display_name="员工111"))
    # 未设置 must_change_password → 默认 False（普通老员工）
    db.add(User(username="emp1", password_hash=hash_password("pass123"),
                display_name="员工111", role="staff", person_code="111",
                is_active=True, status="active"))
    db.commit()
    db.close()


def _csrf(client, path="/my/password"):
    page = client.get(path).text
    m = re.search(r'name="csrf_token" value="([^"]+)"', page)
    return m.group(1) if m else ""


def test_new_staff_must_change_first_login(client):
    _seed()
    # 模拟导入自动建号：新员工默认 must_change_password=True、默认口令 demo123
    from app.services.importer import ensure_staff_user
    db = _db()
    u = ensure_staff_user(db, "222", "新员工")
    db.commit()
    db.close()
    assert u.must_change_password is True
    # 用系统默认口令登录成功
    r = client.post("/login", data={"username": u.username, "password": "demo123"},
                    follow_redirects=False)
    assert r.status_code == 302
    # 但访问任何功能页都被拦到改密页
    r = client.get("/my/perf", follow_redirects=False)
    assert r.status_code == 302
    assert "/my/password?must=1" in r.headers["location"]
    r = client.get("/my/perf", follow_redirects=False)
    # 改密页可见且有横幅
    page = client.get("/my/password?must=1").text
    assert "必须先修改密码" in page
    # 提交新密码 → 清除标志 → 放行
    r = client.post("/my/password", data={
        "csrf_token": _csrf(client), "old_password": "demo123",
        "new_password": "mynewpass1"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/my/perf")
    db = _db()
    u = db.query(User).filter(User.person_code == "222").one()
    assert u.must_change_password is False
    db.close()
    r = client.get("/my/perf", follow_redirects=False)
    assert r.status_code == 200


def test_admin_reset_requires_change_again(client):
    _seed()
    client.post("/login", data={"username": "admin", "password": "pw123456"},
                follow_redirects=False)
    db = _db()
    u = db.query(User).filter(User.username == "emp1").one()
    uid = u.id
    db.close()
    # 管理员重置口令 → 强制再次改密
    r = client.post(f"/staff-admin/{uid}/reset", data={
        "password": "reset123", "csrf_token": _csrf(client, "/staff-admin")},
        follow_redirects=False)
    assert r.status_code == 303
    db = _db()
    u = db.get(User, uid)
    assert u.must_change_password is True
    db.close()
    # 新口令登录被拦到改密页
    client.post("/login", data={"username": "emp1", "password": "reset123"},
                follow_redirects=False)
    r = client.get("/employees", follow_redirects=False)
    assert r.status_code == 302
    assert "/my/password?must=1" in r.headers["location"]


def test_staff_already_changed_not_forced(client):
    _seed()
    # emp1 无 must_change（默认 False）→ 登录后直进
    r = client.post("/login", data={"username": "emp1", "password": "pass123"},
                    follow_redirects=False)
    assert r.status_code == 302
    r = client.get("/my/perf", follow_redirects=False)
    assert r.status_code == 200


def test_admin_reset_all_requires_change(client):
    """批量重置全部员工口令 → 每人须首登改密才能进入功能页。"""
    _seed()
    db = _db()
    db.add(User(username="emp2", password_hash=hash_password("pass123"),
                display_name="员工222", role="staff", person_code="222",
                is_active=True, status="active"))
    db.commit()
    db.close()
    client.post("/login", data={"username": "admin", "password": "pw123456"},
                follow_redirects=False)
    csrf = _csrf(client, "/staff-admin")
    r = client.post("/staff-admin/reset-all",
                    data={"csrf_token": csrf, "password": "demo123"},
                    follow_redirects=False)
    assert r.status_code == 303
    from urllib.parse import unquote
    assert "已重置 2 名员工" in unquote(r.headers.get("location", ""))
    db = _db()
    staff = db.query(User).filter(User.role == "staff").all()
    assert all(u.must_change_password for u in staff)
    db.close()
    # demo123 登录后进任何功能页都被拦到改密页
    client.post("/login", data={"username": "emp1", "password": "demo123"},
                follow_redirects=False)
    r = client.get("/my/perf", follow_redirects=False)
    assert r.status_code == 302
    assert "/my/password?must=1" in r.headers.get("location", "")
