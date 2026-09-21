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
    """员工之间隔离：/my/perf 只见自己的数据。"""
    _staff(client, "emp1", "员工甲", "P1")
    _staff(client, "emp2", "员工乙", "P2")
    db = appdb.SessionLocal()
    imp = ImportFile(file_name="t.xlsx", file_sha256="s1", file_size=0,
                     stored_path="t.xlsx", status="parsed", uploaded_by=1)
    db.add(imp)
    db.commit()
    db.add(FormalRecord(import_id=imp.id, raw_record_id=1,
                        person_code="P1", japan_date=date(2026, 8, 1),
                        points=1))
    db.add(FormalRecord(import_id=imp.id, raw_record_id=2,
                        person_code="P2", japan_date=date(2026, 8, 1),
                        points=2))
    db.commit()
    db.close()
    # 员工甲登录 → 只见 甲
    _staff(client, "emp1", "员工甲", "P1")
    p = client.get("/my/perf?month=2026-08").text
    assert "员工甲" in p and "员工乙" not in p
    # 员工乙登录 → 只见 乙
    _staff(client, "emp2", "员工乙", "P2")
    p = client.get("/my/perf?month=2026-08").text
    assert "员工乙" in p and "员工甲" not in p


