# -*- coding: utf-8 -*-
"""员工登录名导出 + 「首登强制改密」流程（发号前的整套检查）。"""
from datetime import date

import app.db as appdb
from app.auth import SESSION_COOKIE, hash_password, read_session_token
from app.models import Person, User
from tests.helpers import form_token


def _mk(client_list, username, name, code, role="staff", status="active",
        active=True, must_change=False, password="demo123"):
    db = appdb.SessionLocal()
    if code and db.get(Person, code) is None:
        db.add(Person(code=code, display_name=name))
    if db.query(User).filter(User.username == username).first() is None:
        db.add(User(username=username, password_hash=hash_password(password),
                    display_name=name, role=role, person_code=code,
                    status=status, is_active=active,
                    must_change_password=must_change, lang=None))
    db.commit()
    db.close()


def _login(client, username, password="demo123"):
    return client.post("/login", data={"username": username, "password": password},
                       follow_redirects=False)


def _csrf(client):
    return (read_session_token(client.cookies.get(SESSION_COOKIE)) or {}).get("csrf", "")


def _read_xlsx(data):
    import io as _io

    from openpyxl import load_workbook
    return load_workbook(_io.BytesIO(data))


# ---------- 导出 ----------

def test_export_accounts_admin_only(client):
    _mk(client, "admin", "管理员", None, role="admin")
    _mk(client, "empA", "员工A", "PA")
    _login(client, "admin")
    r = client.get("/staff-admin/accounts-export")
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]
    wb = _read_xlsx(r.content)
    assert wb.sheetnames == ["员工登录名", "使用说明"]
    rows = list(wb["员工登录名"].values)
    assert rows[0][:4] == ("登录名", "姓名", "员工编号", "在职状态")
    assert ("empA", "员工A", "PA") == tuple(rows[1][:3])


def test_export_only_active_variant(client):
    """active=1 → 只导在岗且启用的；离职员工不出现。"""
    _mk(client, "admin", "管理员", None, role="admin")
    _mk(client, "empA", "在岗甲", "PA")
    _mk(client, "empB", "离职乙", "PB", status="resigned", active=False)
    _login(client, "admin")
    all_names = [r[0] for r in list(_read_xlsx(client.get(
        "/staff-admin/accounts-export").content)["员工登录名"].values)][1:]
    act_names = [r[0] for r in list(_read_xlsx(client.get(
        "/staff-admin/accounts-export?active=1").content)["员工登录名"].values)][1:]
    assert "empA" in all_names and "empB" in all_names
    assert "empA" in act_names and "empB" not in act_names


def test_export_writes_password_only_when_must_change(client):
    """初始口令列：待改密的写默认口令；已自设密码的留空。"""
    _mk(client, "admin", "管理员", None, role="admin")
    _mk(client, "empMust", "待改密", "PM", must_change=True)
    _mk(client, "empDone", "已自设", "PD", must_change=False)
    _login(client, "admin")
    rows = {r[0]: r for r in list(_read_xlsx(client.get(
        "/staff-admin/accounts-export").content)["员工登录名"].values)[1:]}
    assert rows["empMust"][5] == "是" and rows["empMust"][6] == "demo123"
    assert not rows["empDone"][5] and not rows["empDone"][6]   # 空（openpyxl 读出 None）


def test_export_blocked_for_staff(client):
    _mk(client, "empA", "员工A", "PA")
    _login(client, "empA")
    r = client.get("/staff-admin/accounts-export", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/my/perf"


# ---------- 首登强制改密 ----------

def test_first_login_forces_password_change(client):
    """重置后（must_change=1）：除改密页与登出外一律被拦到改密页；改完恢复正常。"""
    _mk(client, "empA", "员工A", "PA", must_change=True)
    assert _login(client, "empA").status_code == 302
    for path in ("/my/perf", "/my/report", "/my/report/feedback"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"].startswith(
            "/my/password?must=1"), path
    # 旧口令不对 → 留在改密页
    bad = client.post("/my/password", data={"old_password": "wrong",
                                            "new_password": "newpass123",
                                            "csrf_token": _csrf(client),
                                            "_ft": form_token(client)},
                      follow_redirects=False)
    assert bad.status_code == 400 and "原密码不对" in bad.text   # 原密码错误：400 + 提示
    # 正确改密 → 放行
    ok = client.post("/my/password", data={"old_password": "demo123",
                                           "new_password": "newpass123",
                                           "csrf_token": _csrf(client),
                                           "_ft": form_token(client)},
                     follow_redirects=False)
    assert ok.status_code == 303
    db = appdb.SessionLocal()
    u = db.query(User).filter(User.username == "empA").one()
    assert u.must_change_password is False
    db.close()
    assert client.get("/my/perf", follow_redirects=False).status_code == 200
    # 新口令可登录、旧口令不行
    client.get("/logout")
    assert _login(client, "empA", "newpass123").status_code == 302
    client.get("/logout")
    assert _login(client, "empA", "demo123").status_code == 401   # 老口令已失效（401 + 登录页）


def test_reset_all_sets_default_and_must_change(client):
    """批量重置：全部员工口令 → 默认口令 + must_change=True；管理员不受影响。"""
    _mk(client, "admin", "管理员", None, role="admin", password="adminpw")
    _mk(client, "empA", "甲", "PA", must_change=False, password="ownpass")
    _mk(client, "empB", "乙", "PB", must_change=False, password="ownpass")
    _login(client, "admin", "adminpw")
    r = client.post("/staff-admin/reset-all",
                    data={"csrf_token": _csrf(client), "_ft": form_token(client),
                          "password": "demo123"}, follow_redirects=False)
    assert r.status_code == 303 and "msg=" in r.headers["location"]
    db = appdb.SessionLocal()
    for name in ("empA", "empB"):
        u = db.query(User).filter(User.username == name).one()
        assert u.must_change_password is True
    assert db.query(User).filter(User.username == "admin").one() \
        .must_change_password is False               # 管理员不动
    db.close()
    # 用默认口令能登录，且被强制去改密
    assert _login(client, "empA", "demo123").status_code == 302
    r2 = client.get("/my/perf", follow_redirects=False)
    assert r2.status_code == 302 and r2.headers["location"].startswith("/my/password")
