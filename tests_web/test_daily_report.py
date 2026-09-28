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


def test_chart_series_window_and_threshold(client):
    """30 天窗口（含今天）；报过 3 天才 show=True；窗口外的数据不计。"""
    from datetime import date, timedelta
    from app.services import daily_report
    db = appdb.SessionLocal()
    today = date(2026, 9, 28)
    # 窗口内 2 天 + 窗口外 1 天（第 31 天，不应计入）
    for off, p1, p2 in ((0, 2, 1), (5, 3, 0), (30, 9, 9)):
        d = today - timedelta(days=off)
        db.add(StaffDailyReport(person_code="P1", report_date=d, area="x",
                                p1_cnt=p1, p2_cnt=p2, total_cnt=p1 + p2))
    db.commit()
    s = daily_report.chart_series(db, "P1", today=today)
    assert len(s["days"]) == 30 and s["start"] == today - timedelta(days=29)
    assert s["filled"] == 2 and s["show"] is False        # 少于 3 天 → 不给图
    assert s["total"] == 6                                # 窗口外那条不计
    db.add(StaffDailyReport(person_code="P1", report_date=today - timedelta(days=2),
                            area="x", p1_cnt=1, p2_cnt=1, total_cnt=2))
    db.commit()
    s2 = daily_report.chart_series(db, "P1", today=today)
    assert s2["filled"] == 3 and s2["show"] is True


def test_chart_geometry_breaks_on_gap(client):
    """缺数据的日子让曲线断开（分段），点只画在有数据的日子。"""
    from datetime import date
    from app.services import daily_report
    s = {"days": [{"date": date(2026, 9, 1), "filled": True, "p1": 1, "p2": 0, "total": 1},
                  {"date": date(2026, 9, 2), "filled": True, "p1": 3, "p2": 0, "total": 3},
                  {"date": date(2026, 9, 3), "filled": False, "p1": 0, "p2": 0, "total": 0},
                  {"date": date(2026, 9, 4), "filled": True, "p1": 2, "p2": 0, "total": 2},
                  {"date": date(2026, 9, 5), "filled": True, "p1": 4, "p2": 0, "total": 4}],
         "filled": 4, "start": date(2026, 9, 1), "end": date(2026, 9, 5),
         "p1": 10, "p2": 0, "total": 10, "max": 4, "min_filled": 3, "show": True}
    g = daily_report.chart_geometry(s, width=320, height=120)
    assert len(g["segments_p1"]) == 2                     # 3 号断开 → 两段
    assert len(g["dots_p1"]) == 4                         # 只有 4 天有数据
    assert g["max"] == 4
    # 最大值贴在顶部（y = pad_y）
    top = min(float(d["y"]) for d in g["dots_p1"])
    assert abs(top - g["pad_y"]) < 0.01


def test_my_report_page_chart_visibility(client):
    """报满 3 天 → 页面有 SVG；不足 3 天 → 只有提示、没有 SVG。"""
    from datetime import timedelta
    from app.services import daily_report
    db = appdb.SessionLocal()
    _seed_staff(client)
    today = daily_report.jst_today()
    db.add(StaffDailyReport(person_code="P1", report_date=today, area="x",
                            p1_cnt=1, p2_cnt=0, total_cnt=1))
    db.commit()
    _login_staff(client)
    html = client.get("/my/report").text
    assert 'data-testid="trend-svg"' not in html          # 才 1 天 → 不给图
    assert 'data-testid="trend-hint"' in html
    for off in (1, 2):
        db.add(StaffDailyReport(person_code="P1", report_date=today - timedelta(days=off),
                                area="x", p1_cnt=2, p2_cnt=1, total_cnt=3))
    db.commit()
    html2 = client.get("/my/report").text
    assert 'data-testid="trend-svg"' in html2
    assert "polyline" in html2


# ---------- Chunk 4：对比与准确率 ----------

def _seed_sys(db, code, day, p1, p2):
    from app.models import PersonDailyStat
    db.add(PersonDailyStat(person_code=code, ref_date=day, records=p1 + p2,
                           p1=p1, p2=p2, points=p1 + 2 * p2))


def test_compare_three_kinds_and_direction(client):
    """三类：both / 系统有员工没报 / 员工报了系统没有；Δ = 系统 − 自报（>0 少报）。"""
    from datetime import date
    from app.services import daily_report
    db = appdb.SessionLocal()
    db.add(Person(code="P1", display_name="甲"))
    _seed_sys(db, "P1", date(2026, 9, 16), 4, 2)      # 与自报相同 → 一致
    _seed_sys(db, "P1", date(2026, 9, 17), 5, 0)      # 自报 2 → 少报 3
    _seed_sys(db, "P1", date(2026, 9, 18), 1, 0)      # 员工没报 → 漏填报
    for d, p1, p2 in ((date(2026, 9, 16), 4, 2), (date(2026, 9, 17), 2, 0),
                      (date(2026, 9, 19), 3, 1)):     # 19 号系统没有 → 自报无记录
        db.add(StaffDailyReport(person_code="P1", report_date=d, area="渋谷",
                                p1_cnt=p1, p2_cnt=p2, total_cnt=p1 + p2))
    db.commit()
    res = daily_report.compare(db, date(2026, 9, 16), date(2026, 9, 19))
    c = res["counts"]
    assert c["both"] == 2 and c["missing_report"] == 1 and c["missing_system"] == 1
    p = res["persons"][0]
    assert (p["sys_total"], p["rep_total"]) == (4 + 2 + 5 + 1, 6 + 2 + 4) == (12, 12)
    assert p["days_system"] == 3 and p["days_filled"] == 3
    assert p["gaps"] == 1                               # 18 号应填未填
    assert p["both_consistent"] == 1                    # 16 号一致
    row17 = [r for r in res["daily"] if r["date"] == date(2026, 9, 17)][0]
    assert row17["kind"] == "both" and row17["dt"] == 3   # 系统5 − 自报2 = +3（少报）
    assert res["summary"]["matched_cnt"] == 2
    assert res["summary"]["consistent_cnt"] == 1


def test_accuracy_does_not_offset(client):
    """**准确率不抵消**：+5 与 −5 两天，净差为 0，但准确率必须 < 1。"""
    from datetime import date
    from app.services import daily_report
    db = appdb.SessionLocal()
    db.add(Person(code="P2", display_name="乙"))
    _seed_sys(db, "P2", date(2026, 9, 20), 10, 0)       # 自报 5 → 少报 5
    _seed_sys(db, "P2", date(2026, 9, 21), 5, 0)         # 自报 10 → 多报 5
    db.add(StaffDailyReport(person_code="P2", report_date=date(2026, 9, 20),
                            area="x", p1_cnt=5, p2_cnt=0, total_cnt=5))
    db.add(StaffDailyReport(person_code="P2", report_date=date(2026, 9, 21),
                            area="x", p1_cnt=10, p2_cnt=0, total_cnt=10))
    db.commit()
    res = daily_report.compare(db, date(2026, 9, 20), date(2026, 9, 21))
    p = res["persons"][0]
    assert p["d_total"] == 0                             # 净差为 0（会骗人的口径）
    assert p["abs_dt"] == 10                             # Σ|Δ| = 10（真相）
    assert abs(p["acc"] - (1 - 10 / 15)) < 1e-9          # 1 − 10/15 ≈ 0.333
    assert p["acc"] < 1.0


def test_accuracy_excludes_missing_report(client):
    """漏填报不计入准确率（单独算已填/应填），避免"忘了填"和"报不准"混为一谈。"""
    from datetime import date
    from app.services import daily_report
    db = appdb.SessionLocal()
    db.add(Person(code="P3", display_name="丙"))
    _seed_sys(db, "P3", date(2026, 9, 22), 3, 0)
    _seed_sys(db, "P3", date(2026, 9, 23), 100, 0)       # 这天员工没报
    db.add(StaffDailyReport(person_code="P3", report_date=date(2026, 9, 22),
                            area="x", p1_cnt=3, p2_cnt=0, total_cnt=3))
    db.commit()
    res = daily_report.compare(db, date(2026, 9, 22), date(2026, 9, 23))
    p = res["persons"][0]
    assert p["acc"] == 1.0                               # 只有一个对照日，完全一致
    assert p["gaps"] == 1 and p["days_system"] == 2 and p["days_filled"] == 1


def test_compare_skips_persons_with_no_data_either_side(client):
    """两侧都没数据的身份不进报告（历史遗留账号不产生噪音行）。"""
    from datetime import date
    from app.services import daily_report
    db = appdb.SessionLocal()
    db.add(Person(code="GHOST", display_name="幽灵"))
    _seed_sys(db, "P1", date(2026, 9, 24), 1, 0)
    db.commit()
    res = daily_report.compare(db, date(2026, 9, 24), date(2026, 9, 24))
    assert [p["person_code"] for p in res["persons"]] == ["P1"]


def test_suggest_period_uses_latest_import_range(client):
    """默认区间 = 最近一次上传文件在正式表里的日期范围。"""
    from datetime import date
    from app.models import FormalRecord, ImportFile, RawRecord
    from app.services import daily_report
    db = appdb.SessionLocal()
    imp = ImportFile(file_name="t.xlsx", file_sha256="sha-sp", file_size=1,
                     stored_path="/tmp/t.xlsx", uploaded_by=1, status="parsed")
    db.add(imp)
    db.commit()
    raws = []
    for i, d in enumerate((date(2026, 9, 16), date(2026, 9, 30))):
        raw = RawRecord(import_id=imp.id, sheet_name="s", excel_row=2 + i,
                        store_id_raw="S%d" % (i + 1), submitter_raw="甲(1)",
                        submitter_code="1")
        db.add(raw)
        db.commit()
        raws.append((raw, d))
    for raw, d in raws:                     # raw_record_id 唯一 → 一条 raw 一条正式记录
        db.add(FormalRecord(import_id=imp.id, raw_record_id=raw.id, person_code="1",
                            store_id_raw=raw.store_id_raw, japan_date=d, points=1))
    db.commit()
    assert daily_report.suggest_period(db) == (date(2026, 9, 16), date(2026, 9, 30))


# ---------- Chunk 4：管理端页面与导出 ----------

def _seed_admin_staff_and_data(client):
    from datetime import date
    _seed_admin(client)
    db = appdb.SessionLocal()
    if db.get(Person, "P1") is None:
        db.add(Person(code="P1", display_name="甲"))
    _seed_sys(db, "P1", date(2026, 9, 16), 4, 2)
    _seed_sys(db, "P1", date(2026, 9, 17), 5, 0)
    db.add(StaffDailyReport(person_code="P1", report_date=date(2026, 9, 16),
                            area="渋谷", p1_cnt=4, p2_cnt=2, total_cnt=6))
    db.add(StaffDailyReport(person_code="P1", report_date=date(2026, 9, 17),
                            area="新宿", p1_cnt=2, p2_cnt=0, total_cnt=2))
    db.commit()
    db.close()


def test_admin_reports_and_compare_pages(client):
    """管理端列表页与对比页可访问，含关键数字与三类计数。"""
    _seed_admin_staff_and_data(client)
    _login_admin(client)
    r = client.get("/staff-reports?start=2026-09-16&end=2026-09-17")
    assert r.status_code == 200
    assert "自报条数" in r.text and "渋谷" in r.text and "新宿" in r.text
    assert "P1" in r.text          # 员工编号列必须有值（_row_dict 漏字段时这里会挂）
    c = client.get("/staff-reports/compare?start=2026-09-16&end=2026-09-17")
    assert c.status_code == 200
    assert "准确率" in c.text and "应填未填" in c.text
    # 16 号一致(Δ0)、17 号系统 5 / 自报 2 → Δ=+3；Σ|Δ|=3，Σ系统=6+5=11
    # 准确率 = 1 − 3/11 ≈ 72.7%（用绝对值之和，不抵消）
    assert "72.7%" in c.text


def test_admin_pages_forbidden_for_staff(client):
    """员工身份不能访问管理端填报/对比页。"""
    _seed_staff(client)
    _login_staff(client)
    for path in ("/staff-reports", "/staff-reports/compare",
                 "/staff-reports/export?kind=compare"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code in (302, 307), path


def test_export_xlsx_both_kinds(client):
    """两个导出都能生成合法 xlsx（openpyxl 可打开）且表头正确。"""
    import io as _io

    from openpyxl import load_workbook
    _seed_admin_staff_and_data(client)
    _login_admin(client)
    r = client.get("/staff-reports/export?kind=reports&start=2026-09-16&end=2026-09-17")
    assert r.status_code == 200
    wb = load_workbook(_io.BytesIO(r.content))
    ws = wb.active
    assert [c.value for c in ws[1]][:5] == ["日期", "员工编号", "姓名", "担当区域", "1点店铺数"]
    assert ws.max_row == 3                                  # 表头 + 2 条自报
    r2 = client.get("/staff-reports/export?kind=compare&start=2026-09-16&end=2026-09-17")
    assert r2.status_code == 200
    wb2 = load_workbook(_io.BytesIO(r2.content))
    assert wb2.sheetnames[:3] == ["对比(按人)", "对比(逐日)", "汇总"]
    assert "准确率%" in [c.value for c in wb2["对比(按人)"][1]]
