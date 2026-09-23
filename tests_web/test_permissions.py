# -*- coding: utf-8 -*-
"""权限/越权测试：管理员 vs 员工、员工之间隔离、未登录拦截、申诉归属校验。"""
from datetime import date

import app.db as appdb
from app.models import (FormalRecord, ImportFile, Person, PersonDailyStat,
                        RawRecord, User)
from app.auth import hash_password
from tests_web.test_flow import _seed_admin


def _staff(client, username, display, person_code, password="demo123"):
    """建员工账号并登录（幂等）。"""
    db = appdb.SessionLocal()
    u = db.query(User).filter(User.username == username).first()
    if u is None:
        u = User(username=username, display_name=display, role="staff",
                 is_active=True, status="active", person_code=person_code,
                 password_hash=hash_password(password),
                 must_change_password=False)
        db.add(u)
    if db.get(Person, person_code) is None:
        db.add(Person(code=person_code, display_name=display))
    db.commit()
    db.close()
    client.post("/login", data={"username": username, "password": password},
                follow_redirects=False)


def test_staff_blocked_from_admin_pages(client):
    """员工访问管理端页面一律被拦（中间件 + 路由双层）。"""
    _staff(client, "emp1", "员工甲", "P1")
    for path in ("/perf?month=2026-08", "/files", "/stores",
                 "/recon", "/payroll-settle",
                 "/recon/export?task_id=0", "/perf/export?month=2026-08",
                 "/payroll-settle/export?month=2026-08"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 302, f"{path} 应被拦(302)，实际 {r.status_code}"
        assert r.headers["location"].startswith(
            ("/my/perf", "/login")), f"{path} 应拦到员工首页/登录，实际 {r.headers['location']}"


def test_admin_can_access_admin_pages(client):
    """管理员能正常访问管理端页面。"""
    _seed_admin(client)
    for path in ("/perf?month=2026-08", "/files", "/recon", "/dashboard", "/config"):
        r = client.get(path)
        assert r.status_code == 200, f"{path} 管理员应 200，实际 {r.status_code}"


def test_unauthenticated_redirect_to_login(client):
    """未登录访问受保护页面 → 302 /login。"""
    for path in ("/perf", "/recon", "/files", "/my/perf"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"].startswith(
            "/login"), f"{path} 未登录应 302 /login"


def test_staff_perf_isolation(client):
    """员工之间隔离：/my/perf 只见自己的数据（起始月=2026-10，8 月数据员工不可见）。"""
    _staff(client, "emp1", "员工甲", "P1")
    _staff(client, "emp2", "员工乙", "P2")
    db = appdb.SessionLocal()
    imp = ImportFile(file_name="t.xlsx", file_sha256="s1", file_size=0,
                     stored_path="t.xlsx", status="parsed", uploaded_by=1)
    db.add(imp)
    db.commit()
    db.add(FormalRecord(import_id=imp.id, raw_record_id=1,
                        person_code="P1", japan_date=date(2026, 10, 1),
                        points=1))
    db.add(FormalRecord(import_id=imp.id, raw_record_id=2,
                        person_code="P2", japan_date=date(2026, 10, 1),
                        points=2))
    db.commit()
    db.close()
    # 员工甲登录 → 只见 甲
    _staff(client, "emp1", "员工甲", "P1")
    p = client.get("/my/perf?month=2026-10").text
    assert "员工甲" in p and "员工乙" not in p
    # 员工乙登录 → 只见 乙
    _staff(client, "emp2", "员工乙", "P2")
    p = client.get("/my/perf?month=2026-10").text
    assert "员工乙" in p and "员工甲" not in p


def test_staff_hidden_pre_launch_months(client):
    """员工可见起始月：员工看不到 2026-08 数据（直链回退到可见月/空态），管理员可见。"""
    from app.services import perf as _pf
    _staff(client, "emp1", "员工甲", "P1")
    db = appdb.SessionLocal()
    imp = ImportFile(file_name="t2.xlsx", file_sha256="s2", file_size=0,
                     stored_path="t2.xlsx", status="parsed", uploaded_by=1)
    db.add(imp)
    db.commit()
    db.add(FormalRecord(import_id=imp.id, raw_record_id=3,
                        person_code="P1", japan_date=date(2026, 8, 3),
                        points=1))
    db.add(FormalRecord(import_id=imp.id, raw_record_id=4,
                        person_code="P1", japan_date=date(2026, 10, 3),
                        points=2))
    db.commit()
    _pf.sync_month_perf(db, "2026-10")
    db.close()
    # 默认起始月 2026-10 → 员工月份下拉只有 2026-10
    _staff(client, "emp1", "员工甲", "P1")
    p = client.get("/my/perf").text
    assert "2026-08" not in p, "员工不应看到 2026-08 月份选项"
    assert "2026-10" in p, "员工应看到 2026-10 月份选项"
    # 直链隐藏月 → 回退到可见的最新月（2026-10），不泄漏 8 月数据
    p = client.get("/my/perf?month=2026-08").text
    assert "2026-08-03" not in p
    assert "2026-10-03" in p
    # 员工只存在被隐藏月份的数据（无可见月）→ 空态提示，不读全量
    _staff(client, "emp2", "员工乙", "P2")
    db = appdb.SessionLocal()
    imp2 = ImportFile(file_name="t3.xlsx", file_sha256="s3", file_size=0,
                      stored_path="t3.xlsx", status="parsed", uploaded_by=1)
    db.add(imp2)
    db.commit()
    db.add(FormalRecord(import_id=imp2.id, raw_record_id=5,
                        person_code="P2", japan_date=date(2026, 8, 4),
                        points=1))
    db.commit()
    db.close()
    _staff(client, "emp2", "员工乙", "P2")
    p = client.get("/my/perf?month=2026-08").text
    assert "该月暂无计入绩效的记录" in p
    assert "2026-08-04" not in p
    # 管理员不受起始月限制，仍可见 2026-08
    _seed_admin(client)
    client.post("/login", data={"username": "admin", "password": "pw123456"},
                follow_redirects=False)
    p = client.get("/perf?month=2026-08").text
    assert "2026-08" in p


def test_staff_visible_from_config_roundtrip(client):
    """/config 可配置员工可见起始月并保存生效（保存空=不限制）。"""
    _seed_admin(client)
    client.post("/login", data={"username": "admin", "password": "pw123456"},
                follow_redirects=False)
    db = appdb.SessionLocal()
    r = client.get("/config").text
    assert "员工可见起始月" in r and "2026-10" in r
    # 保存为 2026-09 → 员工可见 9/10 月
    db.query(FormalRecord).delete()
    imp = ImportFile(file_name="t4.xlsx", file_sha256="s4", file_size=0,
                     stored_path="t4.xlsx", status="parsed", uploaded_by=1)
    db.add(imp)
    db.commit()
    db.add(FormalRecord(import_id=imp.id, raw_record_id=6,
                        person_code="P1", japan_date=date(2026, 9, 1),
                        points=1))
    db.commit()
    from app.services import perf as _pf
    _pf.sync_month_perf(db, "2026-09")
    db.close()
    _staff(client, "emp1", "员工甲", "P1")
    p = client.get("/my/perf").text
    assert "2026-09" not in p, "默认起始月 2026-10 → 员工不应看到 2026-09"
    client.post("/login", data={"username": "admin", "password": "pw123456"},
                follow_redirects=False)
    csrf = _csrf_of(client, "/config")
    r = client.post("/config/save", data={
        "csrf_token": csrf, "per_point": 250, "bonus_group": 68,
        "bonus_amount": 3000, "staff_visible_from": "2026-09"},
        follow_redirects=False)
    assert r.status_code == 303
    _staff(client, "emp1", "员工甲", "P1")
    p = client.get("/my/perf").text
    assert "2026-09" in p, "改起始月为 2026-09 后员工应看到 2026-09"
    # 非法格式被拒
    client.post("/login", data={"username": "admin", "password": "pw123456"},
                follow_redirects=False)
    csrf = _csrf_of(client, "/config")
    r = client.post("/config/save", data={
        "csrf_token": csrf, "per_point": 250, "bonus_group": 68,
        "bonus_amount": 3000, "staff_visible_from": "bad"},
        follow_redirects=False)
    assert r.status_code == 303
    from urllib.parse import unquote
    assert "员工可见起始月" in unquote(r.headers["location"])


def _csrf_of(client, path="/config"):
    import re
    page = client.get(path).text
    m = re.search(r'name="csrf_token" value="([^"]+)"', page)
    return m.group(1) if m else ""


