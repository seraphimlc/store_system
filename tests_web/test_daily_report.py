# -*- coding: utf-8 -*-
"""员工每日填报 + 区间对比分析报告（规格 v7）。

阶段 1：数据层（两张新表的读写与约束）。后续阶段在此文件继续追加。
"""
from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

import app.db as appdb
from app.models import Person, StaffDailyReport, StaffReportAnalysis


def _person(db, code="P1", name="甲"):
    if db.get(Person, code) is None:
        db.add(Person(code=code, display_name=name))
        db.commit()
    return code


def test_daily_report_roundtrip(client):
    """填报一条：区域/1点/2点/总数落库，submitted_at 自动填。"""
    db = appdb.SessionLocal()
    code = _person(db)
    db.add(StaffDailyReport(person_code=code, report_date=date(2026, 9, 20),
                            area="渋谷", p1_cnt=3, p2_cnt=2, total_cnt=5))
    db.commit()
    r = db.query(StaffDailyReport).one()
    assert (r.area, r.p1_cnt, r.p2_cnt, r.total_cnt) == ("渋谷", 3, 2, 5)
    assert r.submitted_at is not None
    assert r.source == "web"


def test_daily_report_unique_per_person_day(client):
    """一天一条：同人同日重复插入被唯一约束拦住。"""
    db = appdb.SessionLocal()
    code = _person(db)
    db.add(StaffDailyReport(person_code=code, report_date=date(2026, 9, 20),
                            p1_cnt=1, p2_cnt=0, total_cnt=1))
    db.commit()
    db.add(StaffDailyReport(person_code=code, report_date=date(2026, 9, 20),
                            p1_cnt=9, p2_cnt=9, total_cnt=18))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    assert db.query(StaffDailyReport).count() == 1


def test_analysis_roundtrip(client):
    """报告记录：summary/payload 是 JSON，status 默认 pending，tokens 默认 0。"""
    db = appdb.SessionLocal()
    db.add(StaffReportAnalysis(period_start=date(2026, 9, 16),
                               period_end=date(2026, 9, 30),
                               summary={"total": 2},
                               payload={"by_lang": {"zh": {"overall_comment": "ok"}}}))
    db.commit()
    a = db.query(StaffReportAnalysis).one()
    assert a.status == "pending"
    assert a.summary == {"total": 2}
    assert a.payload["by_lang"]["zh"]["overall_comment"] == "ok"
    assert a.ai_tokens == 0 and a.finished_at is None


# ---------- Chunk 2：手工建号（编号即身份键） ----------

def _seed_admin(client):
    from app.auth import hash_password
    from app.models import User
    db = appdb.SessionLocal()
    if db.query(User).filter(User.username == "admin").first() is None:
        db.add(User(username="admin", password_hash=hash_password("pw123456"),
                    display_name="管理员", role="admin", is_active=True))
        db.commit()
    db.close()


def _login_admin(client):
    from app.auth import SESSION_COOKIE, read_session_token
    client.post("/login", data={"username": "admin", "password": "pw123456"},
                follow_redirects=False)
    return read_session_token(client.cookies.get(SESSION_COOKIE))["csrf"]


def test_create_staff_normalize_duplicate_and_validation(client):
    """编号 NFKC 归一；重复 → CodeExists；编号/姓名/长度校验。"""
    from app.models import User
    from app.services import staff_accounts as sa
    db = appdb.SessionLocal()
    assert sa.normalize_code(" ２１８８２４０６０００００００１ ") == "2188240600000001"
    p = sa.create_staff(db, code=" ２１８８２４０６０００００００１ ", name="テスト太郎")
    assert p.code == "2188240600000001"
    u = db.query(User).filter(User.person_code == p.code).one()
    assert u.role == "staff" and u.must_change_password is True and u.is_active is True
    assert p.first_seen_import_id is None          # 手工建号：来源为空
    with pytest.raises(sa.CodeExists):
        sa.create_staff(db, code="2188240600000001", name="别人")
    with pytest.raises(ValueError):
        sa.create_staff(db, code="", name="无编号")
    with pytest.raises(ValueError):
        sa.create_staff(db, code="A" * 33, name="太长")
    with pytest.raises(ValueError):
        sa.create_staff(db, code="2188240600000099", name="")


def test_suggest_login_name_rule_fallback_and_unique(client):
    """测试环境未配置 AI → source=rule（拼音）；同名自动去重。"""
    from app.services import staff_accounts as sa
    db = appdb.SessionLocal()
    r = sa.suggest_login_name(db, "陈嘉溢", "2188240626279038")
    assert r["source"] == "rule" and r["username"] == "chenjiayi"
    sa.create_staff(db, code="2188240600000002", name="陈嘉溢")
    r2 = sa.suggest_login_name(db, "陈嘉溢", "2188240600000003")
    assert r2["username"] != "chenjiayi"


def test_staff_admin_create_route(client):
    """admin 建号 → 303 + msg；编号重复 → 只提示（err），不建第二个。"""
    from app.models import User
    _seed_admin(client)
    csrf = _login_admin(client)
    r = client.post("/staff-admin/create",
                    data={"csrf_token": csrf, "code": "2188240600000009",
                          "name": "新人甲", "username": "", "password": ""},
                    follow_redirects=False)
    assert r.status_code == 303 and "msg=" in r.headers["location"]
    db = appdb.SessionLocal()
    assert db.get(Person, "2188240600000009") is not None
    r2 = client.post("/staff-admin/create",
                     data={"csrf_token": csrf, "code": "2188240600000009",
                           "name": "重复的人"},
                     follow_redirects=False)
    assert r2.status_code == 303 and "err=" in r2.headers["location"]
    assert db.query(User).filter(User.person_code == "2188240600000009").count() == 1
    assert db.query(Person).filter(Person.code == "2188240600000009").count() == 1


def test_staff_admin_create_forbidden_for_staff(client):
    """员工身份不能建号（中间件 + 路由双层）。"""
    from app.auth import hash_password
    from app.models import User
    db = appdb.SessionLocal()
    db.add(Person(code="P9", display_name="员工九"))
    db.add(User(username="emp9", password_hash=hash_password("pw123456"),
                display_name="员工九", role="staff", person_code="P9", is_active=True))
    db.commit()
    db.close()
    client.post("/login", data={"username": "emp9", "password": "pw123456"},
                follow_redirects=False)
    r = client.post("/staff-admin/create",
                    data={"code": "HACK1", "name": "黑客"}, follow_redirects=False)
    assert r.status_code in (302, 307, 403)
    db = appdb.SessionLocal()
    assert db.get(Person, "HACK1") is None


def test_ensure_persons_backfills_first_seen(client):
    """手工建号的人首次出现在文件里 → 补写 first_seen_import_id，姓名仍用系统里的。"""
    from app.models import ImportFile, RawRecord
    from app.services import flow
    from app.services import staff_accounts as sa
    db = appdb.SessionLocal()
    sa.create_staff(db, code="2188240600000011", name="手工甲")
    imp = ImportFile(file_name="t.xlsx", file_sha256="sha-x1", file_size=1,
                     stored_path="/tmp/t.xlsx", uploaded_by=1, status="parsed")
    db.add(imp)
    db.commit()
    db.add(RawRecord(import_id=imp.id, sheet_name="s", excel_row=2,
                     store_id_raw="S1", submitter_raw="手工甲(2188240600000011)",
                     submitter_code="2188240600000011"))
    db.commit()
    flow.ensure_persons(db, imp)
    p = db.get(Person, "2188240600000011")
    assert p.first_seen_import_id == imp.id
    assert p.display_name == "手工甲"


# ---------- Chunk 3：员工端每日填报 ----------

def _seed_staff(client, person_code="P1", username="emp1", name="甲"):
    from app.auth import hash_password
    from app.models import User
    db = appdb.SessionLocal()
    if db.get(Person, person_code) is None:
        db.add(Person(code=person_code, display_name=name))
    if db.query(User).filter(User.username == username).first() is None:
        db.add(User(username=username, password_hash=hash_password("pw123456"),
                    display_name=name, role="staff", person_code=person_code,
                    is_active=True, status="active", must_change_password=False))
    db.commit()
    db.close()


def _login_staff(client, username="emp1"):
    from app.auth import SESSION_COOKIE, read_session_token
    client.post("/login", data={"username": username, "password": "pw123456"},
                follow_redirects=False)
    return read_session_token(client.cookies.get(SESSION_COOKIE))["csrf"]


def test_jst_today_is_japan_time(client):
    """业务日按 JST（+09:00）。"""
    from datetime import datetime, timedelta, timezone
    from app.services import daily_report
    assert daily_report.jst_today() == datetime.now(timezone(timedelta(hours=9))).date()


def test_submit_report_service(client):
    """提交：合计自动算；重复 → AlreadySubmitted；非法数字/超范围 → ValueError。"""
    from app.services import daily_report
    db = appdb.SessionLocal()
    _seed_staff(client)
    from app.models import User
    u = db.query(User).filter(User.username == "emp1").one()
    row = daily_report.submit_report(db, u, area=" 渋谷 ", p1_cnt="3", p2_cnt="2")
    assert (row.area, row.p1_cnt, row.p2_cnt, row.total_cnt) == ("渋谷", 3, 2, 5)
    assert row.report_date == daily_report.jst_today()
    with pytest.raises(daily_report.AlreadySubmitted):
        daily_report.submit_report(db, u, area="x", p1_cnt=1, p2_cnt=1)
    for bad in ("abc", "-1", "1.5", "1000"):
        with pytest.raises(ValueError):
            daily_report.to_count(bad, "1点店铺数")
    assert daily_report.to_count("", "1点店铺数") == 0
    assert daily_report.to_count("999", "x") == 999


def test_my_report_page_and_submit_route(client):
    """页面可访问；提交 303 落库；重复提交提示；已填报后页面只读展示。"""
    from app.services import daily_report
    _seed_staff(client)
    csrf = _login_staff(client)
    page = client.get("/my/report")
    assert page.status_code == 200
    assert daily_report.jst_today().isoformat() in page.text
    r = client.post("/my/report", data={
        "csrf_token": csrf, "area": "渋谷", "p1_cnt": "4", "p2_cnt": "1"},
        follow_redirects=False)
    assert r.status_code == 303 and "msg=" in r.headers["location"]
    db = appdb.SessionLocal()
    row = db.query(StaffDailyReport).one()
    assert (row.area, row.p1_cnt, row.p2_cnt, row.total_cnt) == ("渋谷", 4, 1, 5)
    # 重复提交 → err，且不新增行
    r2 = client.post("/my/report", data={
        "csrf_token": csrf, "area": "渋谷", "p1_cnt": "9", "p2_cnt": "9"},
        follow_redirects=False)
    assert r2.status_code == 303 and "err=" in r2.headers["location"]
    assert db.query(StaffDailyReport).count() == 1
    # 已填报 → 页面显示只读结果与提示
    page2 = client.get("/my/report").text
    assert "今天已经填报过了" in page2
    assert "提交填报" not in page2


def test_my_report_requires_csrf_and_staff(client):
    """无 CSRF → 400；管理员访问员工填报页被拒。"""
    from app.auth import hash_password
    from app.models import User
    _seed_staff(client)
    csrf = _login_staff(client)
    r = client.post("/my/report", data={"csrf_token": "", "area": "x",
                                        "p1_cnt": "1", "p2_cnt": "0"},
                    follow_redirects=False)
    assert r.status_code == 400
    client.get("/logout")
    db = appdb.SessionLocal()
    if db.query(User).filter(User.username == "admin").first() is None:
        db.add(User(username="admin", password_hash=hash_password("pw123456"),
                    display_name="管理员", role="admin", is_active=True))
        db.commit()
    db.close()
    client.post("/login", data={"username": "admin", "password": "pw123456"},
                follow_redirects=False)
    r2 = client.get("/my/report", follow_redirects=False)
    assert r2.status_code in (302, 307)


def test_month_days_leaves_gap_rows(client):
    """逐日视图：本月 1 号到今天，缺填报的日子留空行（员工要看得到"哪天没数据"）。"""
    from datetime import date
    from app.services import daily_report
    db = appdb.SessionLocal()
    _seed_staff(client)
    # 直接造历史数据：本月 1 号、3 号有填报，2 号缺
    today = daily_report.jst_today()
    first = today.replace(day=1)
    for d, p1, p2 in ((first, 3, 1), (date(first.year, first.month, 3), 2, 2)):
        db.add(StaffDailyReport(person_code="P1", report_date=d, area="渋谷",
                                p1_cnt=p1, p2_cnt=p2, total_cnt=p1 + p2))
    db.commit()
    v = daily_report.month_days(db, "P1", today.strftime("%Y-%m"), today=today)
    assert v["visible_days"] == today.day            # 1 号到今天
    assert v["days"][0]["date"] == first and v["days"][0]["empty"] is False
    assert v["filled"] == len([d for d in v["days"] if not d["empty"]]) == 2
    empty = [d for d in v["days"] if d["empty"]]
    assert all(d["p1"] == 0 and d["total"] == 0 for d in empty)
    assert v["p1"] == 5 and v["p2"] == 3 and v["total"] == 8


def test_month_days_historical_month_is_full(client):
    """历史月份：整月逐日列出（月末到月末）。"""
    from datetime import date
    from app.services import daily_report
    db = appdb.SessionLocal()
    db.add(StaffDailyReport(person_code="P1", report_date=date(2026, 2, 10),
                            area="新宿", p1_cnt=1, p2_cnt=0, total_cnt=1))
    db.commit()
    v = daily_report.month_days(db, "P1", "2026-02", today=date(2026, 9, 28))
    assert v["visible_days"] == 28                   # 2026-02 是 28 天
    assert v["filled"] == 1
    assert [d["date"] for d in v["days"] if not d["empty"]] == [date(2026, 2, 10)]
    # 未来月份 → 不列任何天
    v2 = daily_report.month_days(db, "P1", "2026-12", today=date(2026, 9, 28))
    assert v2["days"] == [] and v2["visible_days"] == 0


def test_my_report_page_shows_gap_rows(client):
    """页面把没填报的日子显示为「未填报」。"""
    from datetime import date, timedelta
    from app.services import daily_report
    db = appdb.SessionLocal()
    _seed_staff(client)
    today = daily_report.jst_today()
    if today.day >= 2:                                # 造前一天的数据，今天留空
        db.add(StaffDailyReport(person_code="P1", report_date=today - timedelta(days=1),
                                area="渋谷", p1_cnt=2, p2_cnt=1, total_cnt=3))
        db.commit()
    _login_staff(client)
    html = client.get("/my/report").text
    assert "未填报" in html
    assert "本月合计" in html
    if today.day >= 2:
        assert "已填报" in html
