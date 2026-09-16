# -*- coding: utf-8 -*-
"""导入自动建员工账号测试（不再需要手动创建员工）。"""
import re

import app.db as appdb
from app.auth import hash_password, read_session_token, SESSION_COOKIE, verify_password
from app.models import Person, User
from tests.helpers import wide_xlsx_bytes


def _seed_admin(client):
    db = appdb.SessionLocal()
    db.add(User(username="admin", password_hash=hash_password("pw123456"),
                display_name="管理员", role="admin", is_active=True))
    db.commit()
    db.close()


def _login(client):
    client.post("/login", data={"username": "admin", "password": "pw123456"},
                follow_redirects=False)
    data = read_session_token(client.cookies.get(SESSION_COOKIE))
    return data["csrf"]


def _upload(client, filename, content):
    return client.post("/files/upload",
                       data={"csrf_token": _login(client)},
                       files=[("files", (filename, content,
                                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
                       follow_redirects=False)


def test_upload_auto_creates_new_staff(client, tmp_path):
    _seed_admin(client)
    # 文件中出现两名新提交者（此前无 Person、无账号）
    content = wide_xlsx_bytes(
        tmp_path, ["吴海峰(2188240620009615)", "陈嘉溢(2188240606650879)"])
    r = _upload(client, "aug2026.xlsx", content)
    assert r.status_code == 303
    db = appdb.SessionLocal()
    for code, name, uname in [("2188240620009615", "吴海峰", "wuhaifeng"),
                              ("2188240606650879", "陈嘉溢", "chenjiayi")]:
        p = db.get(Person, code)
        assert p is not None and p.display_name == name
        u = db.query(User).filter(User.person_code == code,
                                  User.role == "staff").one()
        assert u.username == uname          # 姓名自动转拼音登录名
        assert u.display_name == name
        assert u.status == "active" and u.must_change_password is True
        assert verify_password("demo123", u.password_hash)  # 系统默认初始口令
    db.close()


def test_upload_reuses_existing_account(client, tmp_path):
    """已有账号（含早期手工账号）不重复创建、不改登录名。"""
    _seed_admin(client)
    db = appdb.SessionLocal()
    db.add(User(username="xiaochuan", password_hash=hash_password("keepme"),
                display_name="小川逸", role="staff", is_active=True,
                person_code="2188240606634082", status="active"))
    db.commit()
    db.close()
    content = wide_xlsx_bytes(
        tmp_path, ["小川逸(2188240606634082)", "高乔(2188240606670370)"])
    r = _upload(client, "sep.xlsx", content)
    assert r.status_code == 303
    db = appdb.SessionLocal()
    old = db.query(User).filter(User.username == "xiaochuan").one()
    assert old.person_code == "2188240606634082"
    assert verify_password("keepme", old.password_hash)  # 口令未被重置
    n = db.query(User).filter(User.role == "staff").count()
    assert n == 2  # xiaochuan + 新自动建的高乔
    new = db.query(User).filter(User.person_code == "2188240606670370").one()
    assert new.username == "gaoqiao"
    db.close()


def test_same_name_generates_unique_usernames(client, tmp_path):
    """同名不同编号 → 登录名不冲突。"""
    _seed_admin(client)
    content = wide_xlsx_bytes(
        tmp_path, ["张伟(1001)", "张伟(2002)"])
    r = _upload(client, "dup.xlsx", content)
    assert r.status_code == 303
    db = appdb.SessionLocal()
    us = [u.username for u in db.query(User).filter(User.role == "staff")
          .order_by(User.person_code).all()]
    assert len(us) == len(set(us))
    assert len(us) == 2
    assert all(u.startswith("zhangwei") for u in us)
    db.close()


def test_staff_admin_page_has_no_create(client):
    """员工管理页不再提供手动创建入口。"""
    _seed_admin(client)
    _login(client)
    page = client.get("/staff-admin").text
    assert "/staff-admin/create" not in page
    assert "新建员工账号" not in page
    assert "自动开户" in page or "自动" in page
    # create 路由已移除 → 404/405
    r = client.post("/staff-admin/create", data={
        "username": "hack", "person_code": "1", "password": "x12345",
        "csrf_token": ""}, follow_redirects=False)
    assert r.status_code in (404, 405)


def test_pinyin_username_unit():
    from app.services.login_names import suggest_username
    assert suggest_username("甘子杰", "2188240606734603") == "ganzijie"
    assert suggest_username("小川逸", "2188240606634082").startswith("xiaochuan")
    assert suggest_username("YAN GUANGHE", "1") == "yanguanghe"
