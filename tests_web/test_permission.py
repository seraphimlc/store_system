# -*- coding: utf-8 -*-
"""权限隔离与改密测试。"""
import app.db as appdb
from app.auth import hash_password, verify_password
from app.models import Person, User


def _add_staff():
    db = appdb.SessionLocal()
    db.add(User(username="emp1", password_hash=hash_password("pass123"),
                display_name="甲", role="staff", person_code="111"))
    db.commit()
    db.close()


def test_staff_blocked_from_other_data(client):
    _add_staff()
    client.post("/login", data={"username": "emp1", "password": "pass123"},
                follow_redirects=False)
    r = client.get("/files", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/my/perf"
    r = client.get("/perf", follow_redirects=False)
    assert r.status_code == 302


def test_staff_own_password_change(client):
    _add_staff()
    client.post("/login", data={"username": "emp1", "password": "pass123"},
                follow_redirects=False)
    import re
    page = client.get("/my/password").text
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    r = client.post("/my/password", data={"csrf_token": csrf,
                                          "old_password": "pass123",
                                          "new_password": "newpass1"},
                    follow_redirects=False)
    assert r.status_code == 303
    db = appdb.SessionLocal()
    u = db.query(User).filter(User.username == "emp1").one()
    assert verify_password("newpass1", u.password_hash)
    db.close()
