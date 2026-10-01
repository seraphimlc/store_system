# -*- coding: utf-8 -*-
"""员工管理（状态：在岗/请假/停用/离职；禁删）测试。"""
import re

import app.db as appdb
from tests.helpers import form_token
from app.auth import hash_password
from app.models import Person, User


def _db():
    return appdb.SessionLocal()


def _seed(person_codes=("111", "222")):
    db = _db()
    db.add(User(username="admin", password_hash=hash_password("pw123456"),
                display_name="管理员", role="admin", is_active=True))
    for c in person_codes:
        db.add(Person(code=c, display_name=f"员工{c}"))
        db.add(User(username=f"emp{c}", password_hash=hash_password("pass123"),
                    display_name=f"员工{c}", role="staff",
                    person_code=c, is_active=True, status="active"))
    db.commit()
    db.close()


def _login(client, username, password):
    return client.post("/login", data={"username": username,
                                       "password": password},
                       follow_redirects=False)


def _csrf(client, path="/staff-admin"):
    page = client.get(path).text
    m = re.search(r'name="csrf_token" value="([^"]+)"', page)
    return m.group(1) if m else ""


def _uid(uname):
    db = _db()
    u = db.query(User).filter(User.username == uname).one()
    db.close()
    return u.id


def test_admin_page_lists_status(client):
    _seed()
    _login(client, "admin", "pw123456")
    page = client.get("/staff-admin").text
    assert "员工管理" in page
    assert "在岗" in page and "离职" in page
    assert "不提供删除员工" in page
    # 每行只留一个「编辑」按钮；状态/语言/口令都挪进了弹窗（2026-10-01 用户要求）
    assert 'data-testid="staff-edit-dialog"' in page
    assert 'data-testid="edit-' in page and "openStaffEdit" in page
    assert 'name="new_status"' not in page          # 行内不再有改状态下拉
    # 语言/状态下拉各只剩弹窗里那一个（行内表单已全部撤掉）
    assert page.count('name="lang"') == 1
    assert page.count('name="status"') == 1


def test_set_leave_still_logs_in_and_confirms(client):
    _seed()
    _login(client, "admin", "pw123456")
    uid = _uid("emp111")
    r = client.post(f"/staff-admin/{uid}/status",
                    data={"_ft": form_token(client), "new_status": "leave", "csrf_token": _csrf(client)},
                    follow_redirects=False)
    assert r.status_code == 303
    db = _db()
    u = db.get(User, uid)
    assert u.status == "leave" and u.is_active is True and u.can_login
    db.close()
    # 请假员工仍可登录（登录接口放行）
    r = _login(client, "emp111", "pass123")
    assert r.status_code == 302
    r = client.get("/my/perf", follow_redirects=False)
    assert r.status_code == 200


def test_set_disabled_cannot_login(client):
    _seed()
    _login(client, "admin", "pw123456")
    uid = _uid("emp111")
    r = client.post(f"/staff-admin/{uid}/status",
                    data={"_ft": form_token(client), "new_status": "disabled", "csrf_token": _csrf(client)},
                    follow_redirects=False)
    assert r.status_code == 303
    db = _db()
    u = db.get(User, uid)
    assert u.status == "disabled" and u.is_active is False and not u.can_login
    db.close()
    # 登录被拒：401 并提示不可登录
    r = client.post("/login", data={"username": "emp111", "password": "pass123"},
                    follow_redirects=False)
    assert r.status_code == 401
    assert "不可登录" in r.text


def test_set_resigned_keeps_record_and_blocks(client):
    _seed()
    _login(client, "admin", "pw123456")
    uid = _uid("emp222")
    r = client.post(f"/staff-admin/{uid}/status",
                    data={"_ft": form_token(client), "new_status": "resigned", "csrf_token": _csrf(client)},
                    follow_redirects=False)
    assert r.status_code == 303
    # 记录仍在（不删除）
    db = _db()
    u = db.get(User, uid)
    assert u is not None and u.status == "resigned" and not u.can_login
    db.close()
    # 历史数据（Person）仍在
    db = _db()
    assert db.query(Person).filter(Person.code == "222").count() == 1
    db.close()
    # 离职员工无法访问已登录页面（会话被 require_login 拒绝）
    r = _login(client, "emp222", "pass123")
    assert r.status_code == 401


def test_status_filter_and_reactive(client):
    _seed()
    _login(client, "admin", "pw123456")
    uid = _uid("emp111")
    client.post(f"/staff-admin/{uid}/status",
                data={"_ft": form_token(client), "new_status": "resigned", "csrf_token": _csrf(client)},
                follow_redirects=False)
    page = client.get("/staff-admin?status=resigned").text
    assert "emp111" in page and "emp222" not in page
    # 改回在岗 → 能登录
    client.post(f"/staff-admin/{uid}/status",
                data={"_ft": form_token(client), "new_status": "active", "csrf_token": _csrf(client)},
                follow_redirects=False)
    r = _login(client, "emp111", "pass123")
    assert r.status_code == 302


def test_manual_create_requires_admin_and_csrf(client):
    """手工建号（编号即身份键）需要 admin + CSRF；员工身份不可用。

    需求变更（2026-09-27）：客户确认可提供员工编号 → 支持手工建号。
    """
    from app.models import Person, User
    import app.db as appdb
    _seed()
    _login(client, "admin", "pw123456")
    page = client.get("/staff-admin").text
    assert "/staff-admin/create" in page
    # 无 CSRF → 400，且不建号
    r = client.post("/staff-admin/create", data={"_ft": form_token(client), 
        "username": "hack", "person_code": "111", "password": "x12345",
        "csrf_token": ""}, follow_redirects=False)
    assert r.status_code == 400
    # 带 CSRF 正常建号
    r2 = client.post("/staff-admin/create", data={"_ft": form_token(client), 
        "code": "2188240601234567", "name": "新人乙", "username": "",
        "password": "", "csrf_token": _csrf(client)}, follow_redirects=False)
    assert r2.status_code == 303
    db = appdb.SessionLocal()
    assert db.get(Person, "2188240601234567") is not None
    assert db.query(User).filter(User.person_code == "2188240601234567").count() == 1
    db.close()


def test_auto_create_employee_person_code_unique(client):
    """导入自动开户：同一 person_code 不重复建账号。"""
    from app.services.importer import ensure_staff_user
    _seed()
    db = _db()
    ensure_staff_user(db, "999", "新员工")
    ensure_staff_user(db, "999", "新员工")
    db.commit()
    db.close()
    db = _db()
    n = db.query(User).filter(User.role == "staff",
                              User.person_code == "999").count()
    assert n == 1
    db.close()


def test_no_delete_endpoint(client):
    _seed()
    _login(client, "admin", "pw123456")
    uid = _uid("emp111")
    # 页面里没有删除表单/按钮
    page = client.get("/staff-admin").text
    assert "/delete" not in page
    # 也没有删除路由
    r = client.post(f"/staff-admin/{uid}/delete",
                    data={"_ft": form_token(client), "csrf_token": _csrf(client)}, follow_redirects=False)
    assert r.status_code == 404 or r.status_code == 405 or r.status_code == 400


# ---------------- 员工编辑弹窗（2026-10-01 用户要求） ----------------
# "员工编辑单独做一个弹出框，别在这一行记录上改了"；编号（身份键）不支持在这里改。

def test_edit_via_dialog_updates_fields(client):
    _seed()
    _login(client, "admin", "pw123456")
    uid = _uid("emp111")
    r = client.post(f"/staff-admin/{uid}/edit",
                    data={"name": "新名字", "username": "emp1new", "status": "leave",
                          "lang": "ja", "password": "",
                          "csrf_token": _csrf(client), "_ft": form_token(client)},
                    follow_redirects=False)
    from urllib.parse import unquote
    assert r.status_code == 303
    assert "已保存" in unquote(r.headers["location"])
    db = appdb.SessionLocal()
    u = db.get(User, uid)
    assert u.display_name == "新名字" and u.username == "emp1new"
    assert u.status == "leave" and u.is_active is True and u.lang == "ja"
    p = db.get(Person, u.person_code)
    assert p.display_name == "新名字"          # 人员表姓名一起改（矩阵/导出用的是它）
    db.close()


def test_edit_rejects_duplicate_username_and_keeps_code(client):
    """登录名重复 → 拒绝且不改动；**编号字段不再参与编辑**（传了也不理）。"""
    _seed()
    _login(client, "admin", "pw123456")
    uid = _uid("emp111")
    db = appdb.SessionLocal()
    db.add(User(username="other", display_name="别人", role="staff",
                person_code="P9", password_hash="x", is_active=True, status="active"))
    db.commit()
    old_code = db.get(User, uid).person_code
    db.close()
    r = client.post(f"/staff-admin/{uid}/edit",
                    data={"name": "改名失败", "username": "other", "status": "active",
                          "lang": "", "code": "HACKED",
                          "csrf_token": _csrf(client), "_ft": form_token(client)},
                    follow_redirects=False)
    assert r.status_code == 303 and "err=" in r.headers["location"]
    db = appdb.SessionLocal()
    u = db.get(User, uid)
    assert u.username != "other" and u.display_name != "改名失败"
    assert u.person_code == old_code           # 编号没被改
    assert db.get(Person, "HACKED") is None    # 也没有偷偷建一个新编号
    db.close()
