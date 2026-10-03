# -*- coding: utf-8 -*-
"""员工每日填报 + 区间对比分析报告（规格 v7）。

阶段 1：数据层（两张新表的读写与约束）。后续阶段在此文件继续追加。
"""
from datetime import date

import re

import pytest
from sqlalchemy.exc import IntegrityError

import app.db as appdb
from app.services import report_chart, report_store
from tests.helpers import form_token
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
    from app.services import staff_accounts as sa, report_chart
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
                    data={"_ft": form_token(client), "csrf_token": csrf, "code": "2188240600000009",
                          "name": "新人甲", "username": "", "password": ""},
                    follow_redirects=False)
    assert r.status_code == 303 and "msg=" in r.headers["location"]
    db = appdb.SessionLocal()
    assert db.get(Person, "2188240600000009") is not None
    r2 = client.post("/staff-admin/create",
                     data={"_ft": form_token(client), "csrf_token": csrf, "code": "2188240600000009",
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
    from app.services import daily_report, report_compare
    assert daily_report.jst_today() == datetime.now(timezone(timedelta(hours=9))).date()


def test_submit_report_service(client):
    """提交：合计自动算；重复 → AlreadySubmitted；非法数字/超范围 → ValueError。"""
    from app.services import daily_report, report_compare
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
    from app.services import daily_report, report_compare
    _seed_staff(client)
    csrf = _login_staff(client)
    page = client.get("/my/report")
    assert page.status_code == 200
    assert daily_report.jst_today().isoformat() in page.text
    r = client.post("/my/report", data={"_ft": form_token(client), 
        "csrf_token": csrf, "area": "渋谷", "p1_cnt": "4", "p2_cnt": "1"},
        follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/my/report"        # 成功不留 URL 参数
    db = appdb.SessionLocal()
    row = db.query(StaffDailyReport).one()
    assert (row.area, row.p1_cnt, row.p2_cnt, row.total_cnt) == ("渋谷", 4, 1, 5)
    # 重复提交 → err，且不新增行
    r2 = client.post("/my/report", data={"_ft": form_token(client), 
        "csrf_token": csrf, "area": "渋谷", "p1_cnt": "9", "p2_cnt": "9"},
        follow_redirects=False)
    assert r2.status_code == 303 and "err=" in r2.headers["location"]
    assert db.query(StaffDailyReport).count() == 1
    # 已填报 → 页面变只读（表单消失，只显示数字）
    page2 = client.get("/my/report").text
    assert "提交填报" not in page2
    assert ">4<" in page2 and ">1<" in page2               # 1点 4 / 2点 1 已回显


def test_my_report_requires_csrf_and_staff(client):
    """无 CSRF → 400；管理员访问员工填报页被拒。"""
    from app.auth import hash_password
    from app.models import User
    _seed_staff(client)
    csrf = _login_staff(client)
    r = client.post("/my/report", data={"_ft": form_token(client), "csrf_token": "", "area": "x",
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
    from app.services import daily_report, report_compare
    db = appdb.SessionLocal()
    _seed_staff(client)
    # 用**固定历史月**造数（不能用"本月"，否则每月 1、2 号这条测试必挂：页面只列到今天）
    today = date(2026, 3, 20)
    first = today.replace(day=1)
    for d, p1, p2 in ((first, 3, 1), (date(first.year, first.month, 3), 2, 2)):
        db.add(StaffDailyReport(person_code="P1", report_date=d, area="渋谷",
                                p1_cnt=p1, p2_cnt=p2, total_cnt=p1 + p2))
    db.commit()
    v = daily_report.month_days(db, "P1", "2026-03", today=today)
    assert v["visible_days"] == 20                    # 只列到传入的"今天"
    assert all(d["future"] is False for d in v["days"])
    assert v["days"][0]["date"] == first and v["days"][0]["empty"] is False
    assert v["filled"] == len([d for d in v["days"] if not d["empty"]]) == 2
    empty = [d for d in v["days"] if d["empty"]]
    assert all(d["p1"] == 0 and d["total"] == 0 for d in empty)
    assert v["p1"] == 5 and v["p2"] == 3 and v["total"] == 8


def test_month_days_historical_month_is_full(client):
    """历史月份：整月逐日列出（月末到月末）。"""
    from datetime import date
    from app.services import daily_report, report_compare
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
    """页面列出整月逐日（未填报的显示 —，日期带星期括号标注）。"""
    from datetime import date, timedelta
    from app.services import daily_report, report_compare
    db = appdb.SessionLocal()
    _seed_staff(client)
    today = daily_report.jst_today()
    if today.day >= 2:                                # 造前一天的数据，今天留空
        db.add(StaffDailyReport(person_code="P1", report_date=today - timedelta(days=1),
                                area="渋谷", p1_cnt=2, p2_cnt=1, total_cnt=3))
        db.commit()
    _login_staff(client)
    # 状态列已去掉（用户要求）：未填报靠"—"区分；日期与星期合并成一列（星期进括号）
    html = client.get("/my/report").text
    assert "未填报" not in html and ">状态<" not in html and ">星期<" not in html
    wd_set = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
    if today.day >= 2:                                   # 昨天那条：日期带（周X）
        yest = today - timedelta(days=1)
        m = re.search(r'>%s<span class="hint">（([^）]+)）</span></td>'
                      % yest.isoformat(), html)
        assert m and m.group(1) in wd_set, "日期与星期应合并成一列"
        # 今天还没填报 → 数字列是 —
        m2 = re.search(r'>%s<span class="hint">（[^）]+）</span></td>(?:\s*<td[^>]*>[^<]*</td>){3}'
                       % today.isoformat(), html)
        assert m2, "今天应有逐日行（未填报 → 数字为 —）"
    assert "本月合计" in html
    if today.day >= 2:
        assert "已填报" in html


def test_chart_series_window_and_threshold(client):
    """30 天窗口（含今天）；报过 3 天才 show=True；窗口外的数据不计。"""
    from datetime import date, timedelta
    from app.services import daily_report, report_compare
    db = appdb.SessionLocal()
    today = date(2026, 9, 28)
    # 窗口内 2 天 + 窗口外 1 天（第 31 天，不应计入）
    for off, p1, p2 in ((0, 2, 1), (5, 3, 0), (30, 9, 9)):
        d = today - timedelta(days=off)
        db.add(StaffDailyReport(person_code="P1", report_date=d, area="x",
                                p1_cnt=p1, p2_cnt=p2, total_cnt=p1 + p2))
    db.commit()
    s = report_chart.chart_series(db, "P1", today=today)
    # 横轴自适应：右端=今天，左端取"第一个有数据的日子"与"今天-6"中更早者（最少铺开 7 天）
    assert s["start"] == today - timedelta(days=6) and s["span_days"] == 7
    assert s["window_days"] == 30                         # 上限仍是 30 天
    assert s["filled"] == 2 and s["show"] is False        # 少于 3 天 → 不给图
    assert s["total"] == 6                                # 第 31 天那条不计（超上限）
    db.add(StaffDailyReport(person_code="P1", report_date=today - timedelta(days=2),
                            area="x", p1_cnt=1, p2_cnt=1, total_cnt=2))
    db.commit()
    s2 = report_chart.chart_series(db, "P1", today=today)
    assert s2["filled"] == 3 and s2["show"] is True


def _series(days):
    if not days:
        return {"days": [], "filled": 0, "start": None, "end": None, "p1": 0,
                "p2": 0, "total": 0, "max": 0, "min_filled": 3, "show": False}
    return {"days": days, "filled": sum(1 for d in days if d["filled"]),
            "start": days[0]["date"], "end": days[-1]["date"],
            "p1": 0, "p2": 0, "total": 0,
            "max": max([d["p1"] for d in days] + [d["p2"] for d in days] or [0]),
            "min_filled": 3, "show": True}


def test_chart_geometry_breaks_on_gap(client):
    """缺数据的日子让曲线断开（分段），点只画在有数据的日子；两条曲线独立。"""
    from datetime import date
    from app.services import daily_report, report_compare
    s = _series([{"date": date(2026, 9, 1), "filled": True, "p1": 1, "p2": 0, "total": 1},
                 {"date": date(2026, 9, 2), "filled": True, "p1": 3, "p2": 0, "total": 3},
                 {"date": date(2026, 9, 3), "filled": False, "p1": 0, "p2": 0, "total": 0},
                 {"date": date(2026, 9, 4), "filled": True, "p1": 2, "p2": 0, "total": 2},
                 {"date": date(2026, 9, 5), "filled": True, "p1": 4, "p2": 0, "total": 4}])
    g = report_chart.chart_geometry(s, width=320, height=120)
    assert len(g["paths_p1"]) == 2 and len(g["areas_p1"]) == 2   # 3 号断开 → 两段
    # 所有路径必须以 M 开头（浏览器会拒绝没有 moveto 的 d；孤立点不能连成非法路径）
    for k in ("paths_p1", "paths_p2", "areas_p1", "areas_p2"):
        assert all(x.startswith("M") for x in g[k]), k
    assert len(g["dots_p1"]) == 4                                 # 只有 4 天有数据
    assert all(" C" in p for p in g["paths_p1"])                  # 平滑曲线（贝塞尔）
    assert g["dots_p1"][0]["date"] == "2026-09-01"                # 悬停提示带日期


def test_chart_geometry_axes(client):
    """坐标轴：纵轴好看的上限 + 3 条刻度；横轴最多 4 个 MM-DD 标签；今天虚线。"""
    from datetime import date, timedelta
    from app.services import daily_report, report_compare
    d0 = date(2026, 9, 20)
    days = [{"date": d0 + timedelta(days=i), "filled": True, "p1": i + 1,
             "p2": 0, "total": i + 1} for i in range(9)]      # 最大 9 → 上限抬到 10
    g = report_chart.chart_geometry(_series(days))
    assert g["top"] == 10 and [tk["v"] for tk in g["ticks"]] == [10, 5, 0]
    assert g["ticks"][0]["y"] == g["top_y"] and g["ticks"][-1]["y"] == g["base_y"]
    assert 2 <= len(g["x_ticks"]) <= 4
    assert g["x_ticks"][0]["label"] == "09-20" and g["x_ticks"][-1]["label"] == "09-28"
    assert g["today_x"] == g["x_ticks"][-1]["x"]
    # 数据最大值 9 → 落在 y(9)，刻度上限 10 的线在它上方
    y9 = g["pad_t"] + g["plot_h"] * (1 - 9 / g["top"])
    assert abs(min(float(d["y"]) for d in g["dots_p1"]) - y9) < 0.3
    assert min(float(d["y"]) for d in g["dots_p1"]) > g["top_y"]


def test_chart_geometry_empty_and_single_point(client):
    """空数据 → empty；只有一天 → 只画点、不画线。"""
    from datetime import date
    from app.services import daily_report, report_compare
    assert report_chart.chart_geometry(_series([]))["empty"] is True
    g = report_chart.chart_geometry(_series(
        [{"date": date(2026, 9, 1), "filled": True, "p1": 2, "p2": 1, "total": 3}]))
    assert g["paths_p1"] == [] and len(g["dots_p1"]) == 1


def test_my_report_page_chart_visibility(client):
    """报满 3 天 → 页面有 SVG；不足 3 天 → 只有提示、没有 SVG。"""
    from datetime import timedelta
    from app.services import daily_report, report_compare
    db = appdb.SessionLocal()
    _seed_staff(client)
    today = daily_report.jst_today()
    db.add(StaffDailyReport(person_code="P1", report_date=today, area="x",
                            p1_cnt=1, p2_cnt=0, total_cnt=1))
    db.commit()
    _login_staff(client)
    html = client.get("/my/report").text
    assert 'data-testid="trend-svg"' not in html          # 才 1 天 → 不给图
    assert 'data-testid="trend-card"' not in html        # 也不给任何提示（用户要求不加解释文案）
    for off in (1, 2):
        db.add(StaffDailyReport(person_code="P1", report_date=today - timedelta(days=off),
                                area="x", p1_cnt=2, p2_cnt=1, total_cnt=3))
    db.commit()
    html2 = client.get("/my/report").text
    assert 'data-testid="trend-svg"' in html2
    assert "<path" in html2 and 'stroke="#2f5bea"' in html2      # 平滑曲线 + 面积填充
    assert "url(#g1)" in html2                                    # 渐变面积
    assert "<title>" in html2                                     # 节点悬停数值


# ---------- Chunk 4：对比与准确率 ----------

def _seed_sys(db, code, day, p1, p2):
    from app.models import PersonDailyStat
    db.add(PersonDailyStat(person_code=code, ref_date=day, records=p1 + p2,
                           p1=p1, p2=p2, points=p1 + 2 * p2))


def test_compare_three_kinds_and_direction(client):
    """三类：both / 系统有员工没报 / 员工报了系统没有；Δ = 系统 − 自报（>0 少报）。"""
    from datetime import date
    from app.services import daily_report, report_compare
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
    res = report_compare.compare(db, date(2026, 9, 16), date(2026, 9, 19))
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
    from app.services import daily_report, report_compare
    db = appdb.SessionLocal()
    db.add(Person(code="P2", display_name="乙"))
    _seed_sys(db, "P2", date(2026, 9, 20), 10, 0)       # 自报 5 → 少报 5
    _seed_sys(db, "P2", date(2026, 9, 21), 5, 0)         # 自报 10 → 多报 5
    db.add(StaffDailyReport(person_code="P2", report_date=date(2026, 9, 20),
                            area="x", p1_cnt=5, p2_cnt=0, total_cnt=5))
    db.add(StaffDailyReport(person_code="P2", report_date=date(2026, 9, 21),
                            area="x", p1_cnt=10, p2_cnt=0, total_cnt=10))
    db.commit()
    res = report_compare.compare(db, date(2026, 9, 20), date(2026, 9, 21))
    p = res["persons"][0]
    assert p["d_total"] == 0                             # 净差为 0（会骗人的口径）
    assert p["abs_dt"] == 10                             # Σ|Δ| = 10（真相）
    assert abs(p["acc"] - (1 - 10 / 15)) < 1e-9          # 1 − 10/15 ≈ 0.333
    assert p["acc"] < 1.0


def test_accuracy_excludes_missing_report(client):
    """漏填报不计入准确率（单独算已填/应填），避免"忘了填"和"报不准"混为一谈。"""
    from datetime import date
    from app.services import daily_report, report_compare
    db = appdb.SessionLocal()
    db.add(Person(code="P3", display_name="丙"))
    _seed_sys(db, "P3", date(2026, 9, 22), 3, 0)
    _seed_sys(db, "P3", date(2026, 9, 23), 100, 0)       # 这天员工没报
    db.add(StaffDailyReport(person_code="P3", report_date=date(2026, 9, 22),
                            area="x", p1_cnt=3, p2_cnt=0, total_cnt=3))
    db.commit()
    res = report_compare.compare(db, date(2026, 9, 22), date(2026, 9, 23))
    p = res["persons"][0]
    assert p["acc"] == 1.0                               # 只有一个对照日，完全一致
    assert p["gaps"] == 1 and p["days_system"] == 2 and p["days_filled"] == 1


def test_compare_skips_persons_with_no_data_either_side(client):
    """两侧都没数据的身份不进报告（历史遗留账号不产生噪音行）。"""
    from datetime import date
    from app.services import daily_report, report_compare
    db = appdb.SessionLocal()
    db.add(Person(code="GHOST", display_name="幽灵"))
    _seed_sys(db, "P1", date(2026, 9, 24), 1, 0)
    db.commit()
    res = report_compare.compare(db, date(2026, 9, 24), date(2026, 9, 24))
    assert [p["person_code"] for p in res["persons"]] == ["P1"]


def test_suggest_period_uses_latest_import_range(client):
    """默认区间 = 最近一次上传文件在正式表里的日期范围。"""
    from datetime import date
    from app.models import FormalRecord, ImportFile, RawRecord
    from app.services import daily_report, report_compare
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
    assert report_compare.suggest_period(db) == (date(2026, 9, 16), date(2026, 9, 30))


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


# ---------- Chunk 5：AI 分析报告（全程 mock，不连外网） ----------

_VALID_JSON = ('{"overall_comment":"整体一致性一般","accuracy_notes":"偏差集中在少数人",'
               '"per_person":{"P1":{"comment":"16 号完全一致","off_days":'
               '[{"date":"2026-09-17","delta":3,"note":"少报 3 家"}],'
               '"questions":["17 号是否漏记"]},'
               '"P2":{"comment":"另一个人","off_days":[],"questions":[]}}}')


def _mock_ai(monkeypatch, text=_VALID_JSON, usage=True):
    from app.services import ai_chat as _ai
    calls = {"n": 0, "prompts": []}

    def fake_chat(prompt, timeout=600, retries=1, max_tokens=None, return_usage=False):
        calls["n"] += 1
        calls["prompts"].append(prompt)
        if usage and return_usage:
            return text, {"total_tokens": 1234}
        return text
    monkeypatch.setattr(_ai, "chat", fake_chat)
    monkeypatch.setattr(_ai, "configured", lambda: True)
    return calls


def _no_thread(monkeypatch):
    """让 start_analysis 不起真线程（测试里手动跑 run_analysis，避免竞态）。"""
    from app.services import report_ai
    class _T:
        def __init__(self, *a, **k):
            pass
        def start(self):
            pass
    monkeypatch.setattr(report_ai.threading, "Thread", _T)


def _seed_coverage(db, *dates, tag="cov"):
    """造"已导入文件的覆盖范围"（正式记录）——区间可用性以它为准。"""
    from app.models import FormalRecord, ImportFile, RawRecord
    imp = ImportFile(file_name="t.xlsx", file_sha256="sha-" + tag, file_size=1,
                     stored_path="/tmp/t.xlsx", uploaded_by=1, status="parsed")
    db.add(imp)
    db.commit()
    for i, d in enumerate(dates):
        raw = RawRecord(import_id=imp.id, sheet_name="s", excel_row=2 + i,
                        store_id_raw="S%d" % (i + 1), submitter_raw="甲(1)",
                        submitter_code="1")
        db.add(raw)
        db.commit()
        db.add(FormalRecord(import_id=imp.id, raw_record_id=raw.id, person_code="1",
                            store_id_raw=raw.store_id_raw, japan_date=d, points=1))
    db.commit()
    return imp


def _seed_compare_data(db):
    from datetime import date
    if db.get(Person, "P1") is None:
        db.add(Person(code="P1", display_name="甲"))
    if db.get(Person, "P2") is None:
        db.add(Person(code="P2", display_name="乙"))
    _seed_sys(db, "P1", date(2026, 9, 16), 4, 2)
    _seed_sys(db, "P1", date(2026, 9, 17), 5, 0)
    _seed_sys(db, "P2", date(2026, 9, 16), 3, 0)
    db.add(StaffDailyReport(person_code="P1", report_date=date(2026, 9, 16),
                            area="渋谷", p1_cnt=4, p2_cnt=2, total_cnt=6))
    db.add(StaffDailyReport(person_code="P1", report_date=date(2026, 9, 17),
                            area="新宿", p1_cnt=2, p2_cnt=0, total_cnt=2))
    db.add(StaffDailyReport(person_code="P2", report_date=date(2026, 9, 16),
                            area="池袋", p1_cnt=3, p2_cnt=0, total_cnt=3))
    db.commit()


def test_analysis_generate_and_payload(client, monkeypatch):
    """生成：数字来自程序、评语来自模型；双语各一份；token 留痕。"""
    from datetime import date
    from app.services import report_ai
    db = appdb.SessionLocal()
    _seed_compare_data(db)
    calls = _mock_ai(monkeypatch)
    _no_thread(monkeypatch)
    a, msg = report_ai.start_analysis(db, None, date(2026, 9, 16), date(2026, 9, 17))
    assert a is not None and a.status == "pending"
    report_ai.run_analysis(a.id)
    db.expire_all()
    a = db.get(type(a), a.id)
    assert a.status == "done"
    assert set(a.payload["by_lang"].keys()) == {"zh", "ja"}      # 双语
    assert calls["n"] == 2                                       # 每语言一次调用
    assert a.payload["by_lang"]["zh"]["per_person"]["P1"]["comment"] == "16 号完全一致"
    assert a.ai_tokens == 2468 and a.ai_model                  # 2 × 1234
    assert a.summary["checkin_cnt"] == 3 and a.summary["fingerprint"]


def test_prompt_constraints_and_program_numbers(client, monkeypatch):
    """prompt 必须带硬约束，且**不许模型自己算数字**（只喂算好的汇总）。"""
    from datetime import date
    from app.services import daily_report, report_compare, report_ai
    db = appdb.SessionLocal()
    _seed_compare_data(db)
    calls = _mock_ai(monkeypatch)
    _no_thread(monkeypatch)
    res = report_compare.compare(db, date(2026, 9, 16), date(2026, 9, 17))
    p = report_ai.build_prompt(res, "ja")
    assert "只使用下面给出的数字" in p
    assert "不做人身评价" in p and "不做定性指控" in p
    assert "日本語" in p
    assert "P1" in p and "P2" in p


def test_analysis_reuse_same_fingerprint(client, monkeypatch):
    """同区间 + 同数据 → 复用已有报告，不再调模型、不新增记录。"""
    from datetime import date
    from app.models import StaffReportAnalysis
    from app.services import report_ai
    db = appdb.SessionLocal()
    _seed_compare_data(db)
    calls = _mock_ai(monkeypatch)
    _no_thread(monkeypatch)
    a, _ = report_ai.start_analysis(db, None, date(2026, 9, 16), date(2026, 9, 17))
    report_ai.run_analysis(a.id)
    db.expire_all()                     # run_analysis 用自己的会话写库 → 重新读
    n_after_first = calls["n"]
    a2, msg = report_ai.start_analysis(db, None, date(2026, 9, 16), date(2026, 9, 17))
    assert a2.id == a.id and "复用" in msg
    assert calls["n"] == n_after_first                       # 没再烧 token
    assert db.query(StaffReportAnalysis).count() == 1


def test_analysis_bad_json_fails_but_summary_kept(client, monkeypatch):
    """模型输出非法 JSON → failed，但对比数字（summary）仍然可用。"""
    from datetime import date
    from app.services import report_ai
    db = appdb.SessionLocal()
    _seed_compare_data(db)
    _mock_ai(monkeypatch, text="抱歉，我无法完成。")
    _no_thread(monkeypatch)
    a, _ = report_ai.start_analysis(db, None, date(2026, 9, 16), date(2026, 9, 17))
    report_ai.run_analysis(a.id)
    db.expire_all()
    a = db.get(type(a), a.id)
    assert a.status == "failed" and a.ai_error
    assert a.payload["by_lang"] == {}
    assert a.summary["checkin_cnt"] == 3                     # 数字照常在


def test_analysis_disabled_by_env(client, monkeypatch):
    """VISIT_REPORT_AI=0 → 不生成（对比数字不受影响）。"""
    from datetime import date
    from app.config import get_settings
    from app.services import report_ai
    monkeypatch.setenv("VISIT_REPORT_AI", "0")
    get_settings.cache_clear()
    try:
        assert report_ai.report_ai_enabled() is False
        db = appdb.SessionLocal()
        _seed_compare_data(db)
        a, msg = report_ai.start_analysis(db, None, date(2026, 9, 16), date(2026, 9, 17))
        assert a is None and "关闭" in msg
    finally:
        get_settings.cache_clear()



def test_feedback_page_numbers_only(client, monkeypatch):
    """员工端核对页：**只给数字**（准确率 + 逐日 Δ）；无评语、无状态列、无他人数据。"""
    from datetime import date
    from app.services import perf, report_ai
    monkeypatch.setattr(perf, "staff_visible_from", lambda db: "")   # 放行 9 月
    db = appdb.SessionLocal()
    _seed_compare_data(db)
    _mock_ai(monkeypatch)
    _no_thread(monkeypatch)
    _seed_coverage(db, date(2026, 9, 16), date(2026, 9, 17), tag="fb")
    a, _ = report_ai.start_analysis(db, None, date(2026, 9, 16), date(2026, 9, 17))
    report_ai.run_analysis(a.id)
    _seed_staff(client, person_code="P1", username="emp1", name="甲")
    _login_staff(client)
    html = client.get("/my/report/feedback").text
    # 数字在
    assert "我的准确率" in html and "自报" in html and "2026-09-17" in html
    # 评语不再下发给员工
    assert "我的评语" not in html
    assert "16 号完全一致" not in html          # 模型写的评语（mock 里的）
    assert "另一个人" not in html                # 别人的评语
    assert "是否漏记" not in html                # 管理端追问清单
    # 状态列去掉；标签改成「自报」
    assert "两侧都有" not in html and "系统有 / 未报" not in html
    assert "我报的" not in html
    # 他人数据
    assert "P2" not in html


def test_feedback_page_empty_state(client):
    """还没有报告时 → 空态提示，不报错。"""
    _seed_staff(client, person_code="P1", username="emp1", name="甲")
    _login_staff(client)
    r = client.get("/my/report/feedback")
    assert r.status_code == 200 and "还在核对中" in r.text


def test_analyze_and_retry_routes_admin_only(client, monkeypatch):
    """生成/重试：admin 可，员工被拦；生成后落一条 pending。"""
    from datetime import date
    from app.models import StaffReportAnalysis
    _seed_compare_data(appdb.SessionLocal())
    _mock_ai(monkeypatch)
    _no_thread(monkeypatch)
    _seed_admin(client)
    csrf = _login_admin(client)
    r = client.post("/staff-reports/compare/analyze",
                    data={"_ft": form_token(client), "csrf_token": csrf, "start": "2026-09-16", "end": "2026-09-17"},
                    follow_redirects=False)
    assert r.status_code == 303 and "msg=" in r.headers["location"]
    db = appdb.SessionLocal()
    a = db.query(StaffReportAnalysis).order_by(StaffReportAnalysis.id.desc()).first()
    assert a is not None and a.status == "pending"
    page = client.get("/staff-reports/compare?start=2026-09-16&end=2026-09-17")
    assert page.status_code == 200 and "AI 分析报告" in page.text
    # 员工被拦
    client.get("/logout")
    _seed_staff(client, person_code="P1", username="emp1", name="甲")
    _login_staff(client)
    r2 = client.post("/staff-reports/compare/analyze",
                     data={"csrf_token": "x", "start": "2026-09-16", "end": "2026-09-17"},
                     follow_redirects=False)
    assert r2.status_code in (302, 307)


def test_chart_series_autofit_long_history(client):
    """有 30 天以上历史时：整段 30 天都画（左端顶到上限），不会无限回溯。"""
    from datetime import date, timedelta
    from app.services import daily_report, report_compare
    db = appdb.SessionLocal()
    today = date(2026, 9, 28)
    for off in range(0, 45, 3):                    # 45 天里每 3 天一条
        d = today - timedelta(days=off)
        db.add(StaffDailyReport(person_code="P9", report_date=d, area="x",
                                p1_cnt=1, p2_cnt=0, total_cnt=1))
    db.commit()
    s = report_chart.chart_series(db, "P9", today=today)
    # 数据最早在 27 天前 → 左端就从那天起（28 天铺满图宽）
    assert s["start"] == today - timedelta(days=27)
    assert s["span_days"] == 28 and s["filled"] == 10
    assert s["show"] is True
    # 超出 30 天上限的数据：既不显示、也不影响横轴（查询阶段就过滤掉）
    db.add(StaffDailyReport(person_code="P9", report_date=today - timedelta(days=40),
                            area="x", p1_cnt=9, p2_cnt=9, total_cnt=18))
    db.commit()
    s2 = report_chart.chart_series(db, "P9", today=today)
    assert (s2["start"], s2["span_days"], s2["filled"]) == (
        s["start"], s["span_days"], s["filled"])
    assert s2["total"] == s["total"]                       # 40 天前那条不计


def test_available_periods_dedupe_and_file_coverage(client):
    """区间列表：同区间只留最新一份；没有文件覆盖（系统侧无数据）的区间不列。"""
    from datetime import date
    from app.models import StaffReportAnalysis
    from app.services import report_ai
    db = appdb.SessionLocal()
    _seed_coverage(db, date(2026, 9, 16), date(2026, 9, 17))   # 覆盖 9/16~9/17
    _seed_sys(db, "P1", date(2026, 9, 16), 4, 2)               # 该区间确有可对比数据
    db.commit()
    # 同区间两份（模拟重新生成过）+ 一份没有文件覆盖的区间（10 月）
    db.add(StaffReportAnalysis(period_start=date(2026, 9, 16), period_end=date(2026, 9, 17),
                               status="done", summary={}, payload={"by_lang": {"zh": {}}}))
    db.add(StaffReportAnalysis(period_start=date(2026, 9, 16), period_end=date(2026, 9, 17),
                               status="done", summary={}, payload={"by_lang": {"zh": {}}}))
    # 跨出文件覆盖范围（9/16 之后）的区间 → 也不该列
    db.add(StaffReportAnalysis(period_start=date(2026, 9, 16), period_end=date(2026, 9, 30),
                               status="done", summary={}, payload={"by_lang": {"zh": {}}}))
    db.add(StaffReportAnalysis(period_start=date(2026, 10, 1), period_end=date(2026, 10, 15),
                               status="done", summary={}, payload={"by_lang": {"zh": {}}}))
    # 失败的不算
    db.add(StaffReportAnalysis(period_start=date(2026, 9, 16), period_end=date(2026, 9, 17),
                               status="failed", summary={}, payload={}))
    db.commit()
    # 读接口只读物化表（不再懒补写）→ 显式给"最新的那份 9/16~9/17"落表
    from app.services import daily_report, report_compare as _dr
    newest = (db.query(StaffReportAnalysis)
              .filter(StaffReportAnalysis.status == "done",
                      StaffReportAnalysis.period_start == date(2026, 9, 16),
                      StaffReportAnalysis.period_end == date(2026, 9, 17))
              .order_by(StaffReportAnalysis.id.desc()).first())
    report_store.materialize(db, newest.id,
                          _dr.compare(db, date(2026, 9, 16), date(2026, 9, 17)))
    ps = report_store.available_periods(db)
    assert len(ps) == 1                                     # 去重 + 只留覆盖范围内的区间
    assert (ps[0].period_start, ps[0].period_end) == (date(2026, 9, 16), date(2026, 9, 17))


def test_auto_for_import_triggers_analysis(client, monkeypatch):
    """**文件入表后自动生成**该区间的对比报告；AI 未配置则跳过（不建空报告）。"""
    from datetime import date
    from app.models import StaffReportAnalysis
    from app.services import report_ai
    db = appdb.SessionLocal()
    imp = _seed_coverage(db, date(2026, 9, 16), date(2026, 9, 17), tag="auto")
    _seed_sys(db, "P1", date(2026, 9, 16), 4, 2)        # 对比读的是日统计表
    db.commit()
    # AI 未配置（测试环境默认）→ 跳过，不产生报告行
    assert "未配置" in report_ai.auto_for_import(db, imp.id)
    assert db.query(StaffReportAnalysis).count() == 0
    # AI 可用 → 建 pending（区间 = 该文件在正式表里的日期范围）
    _mock_ai(monkeypatch)
    _no_thread(monkeypatch)
    msg = report_ai.auto_for_import(db, imp.id)
    assert "2026-09-16" in msg and "2026-09-17" in msg
    a = db.query(StaffReportAnalysis).order_by(StaffReportAnalysis.id.desc()).first()
    assert (a.period_start, a.period_end) == (date(2026, 9, 16), date(2026, 9, 17))
    assert a.status == "pending"


def test_import_period_is_strict(client):
    """没有正式记录的文件 → (None, None)（**刻意不回退全局**：空文件不该触发生成）。"""
    from datetime import date
    from app.models import ImportFile
    from app.services import report_ai
    db = appdb.SessionLocal()
    _seed_coverage(db, date(2026, 9, 10), date(2026, 9, 20), tag="cov2")
    empty = ImportFile(file_name="e.xlsx", file_sha256="sha-empty", file_size=1,
                       stored_path="/tmp/e.xlsx", uploaded_by=1, status="parsed")
    db.add(empty)
    db.commit()
    assert report_store.import_period(db, empty.id) == (None, None)
    # 有正式记录的文件 → 该文件自己的范围（用现成的覆盖夹具）
    imp2 = _seed_coverage(db, date(2026, 9, 12), tag="has")
    assert report_store.import_period(db, imp2.id) == (date(2026, 9, 12), date(2026, 9, 12))


# ---------- 当天填报可修改 ----------

def test_update_today_route(client):
    """当天填报可修改（区域/1点/2点），合计自动重算；页面出现「修改」按钮。"""
    from app.services import daily_report, report_compare
    _seed_staff(client)
    csrf = _login_staff(client)
    client.post("/my/report", data={"_ft": form_token(client), "csrf_token": csrf, "area": "渋谷",
                                    "p1_cnt": "4", "p2_cnt": "1"},
                follow_redirects=False)
    r = client.post("/my/report/update",
                    data={"_ft": form_token(client), "csrf_token": csrf, "area": "池袋", "p1_cnt": "7",
                          "p2_cnt": "3"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/my/report"
    db = appdb.SessionLocal()
    row = db.query(StaffDailyReport).one()
    assert (row.area, row.p1_cnt, row.p2_cnt, row.total_cnt) == ("池袋", 7, 3, 10)
    assert row.report_date == daily_report.jst_today()
    html = client.get("/my/report").text
    assert 'data-testid="edit-report"' in html and 'data-testid="today-summary"' in html
    assert 'data-testid="save-report"' in html


def test_update_today_requires_existing_report(client):
    """没有当天记录 → NoReport（跨天不能被改：昨天那条今天改不了）。"""
    from datetime import timedelta
    from app.services import daily_report, report_compare
    _seed_staff(client)
    db = appdb.SessionLocal()
    from app.models import User
    u = db.query(User).filter(User.username == "emp1").one()
    today = daily_report.jst_today()
    db.add(StaffDailyReport(person_code="P1", report_date=today - timedelta(days=1),
                            area="昨天", p1_cnt=1, p2_cnt=1, total_cnt=2))
    db.commit()
    with pytest.raises(daily_report.NoReport):
        daily_report.update_today(db, u, area="改成今天", p1_cnt=9, p2_cnt=9)
    assert db.query(StaffDailyReport).one().area == "昨天"     # 昨天那条没被改


def test_update_today_validates_counts(client):
    """修改同样做 0..999 校验。"""
    from app.services import daily_report, report_compare
    _seed_staff(client)
    db = appdb.SessionLocal()
    from app.models import User
    u = db.query(User).filter(User.username == "emp1").one()
    daily_report.submit_report(db, u, area="x", p1_cnt=1, p2_cnt=1)
    for bad in ("abc", "1000", "-1"):
        with pytest.raises(ValueError):
            daily_report.update_today(db, u, area="x", p1_cnt=bad, p2_cnt=0)


def test_chart_isolated_point_has_no_line_or_area(client):
    """孤立的一天（前后都断档）：只画点，不产生连线/面积（历史 bug：非法 path 报浏览器错误）。"""
    from datetime import date
    from app.services import daily_report, report_compare
    s = _series([{"date": date(2026, 9, 1), "filled": False, "p1": 0, "p2": 0, "total": 0},
                 {"date": date(2026, 9, 2), "filled": True, "p1": 5, "p2": 2, "total": 7},
                 {"date": date(2026, 9, 3), "filled": False, "p1": 0, "p2": 0, "total": 0}])
    g = report_chart.chart_geometry(s)
    assert len(g["dots_p1"]) == 1                       # 点还在
    assert g["paths_p1"] == [] and g["areas_p1"] == []  # 连线和面积都为空
    assert g["paths_p2"] == [] and g["areas_p2"] == []


# ---------- 分数（点数）与比例（2026-10-02 用户要求：自动展示，不落库） ----------

def test_points_and_rate_helpers():
    """分数 = 1点×1 + 2点×2；**比例 = 2点分数 ÷ 总分数**（2026-10-02 用户明确）。"""
    from app.services.report_compare import p2_score_share, points_of
    assert points_of(3, 1) == 5 and points_of(0, 0) == 0 and points_of(10, 0) == 10
    assert points_of(None, None) == 0
    # 1点6家/2点2家 → 2点分数 4 ÷ 总分数 10 = 40%（不是店数口径的 25%）
    assert p2_score_share(6, 2) == 0.4
    assert p2_score_share(3, 1) == 0.4
    assert p2_score_share(0, 5) == 1.0 and p2_score_share(7, 0) == 0.0
    assert p2_score_share(0, 0) is None               # 没店 → 没有占比（页面显示 —）


def test_list_and_compare_carry_points_and_rate(client):
    """列表/对比的每条数据都带 points 与 rate（现算，表里没有这两列）。"""
    from app.services import report_compare
    db = appdb.SessionLocal()
    _person(db, "P1", "甲")
    db.add(StaffDailyReport(person_code="P1", report_date=date(2026, 9, 16),
                            p1_cnt=3, p2_cnt=1, total_cnt=4))
    db.commit()
    rows = report_compare.list_reports(db, start="2026-09-16", end="2026-09-16",
                                       page=1, per=10)["rows"]
    assert rows[0]["points"] == 5 and rows[0]["rate"] == 0.4      # 2点分数 2 / 5
    res = report_compare.compare(db, "2026-09-16", "2026-09-16")
    p = res["persons"][0]
    assert p["rep_points"] == 5 and abs(p["rep_rate"] - 0.4) < 1e-9
    assert p["sys_points"] == 0 and p["sys_rate"] is None
    d = res["daily"][0]
    assert d["rep_points"] == 5 and abs(d["rep_rate"] - 0.4) < 1e-9
    db.close()


def test_pages_show_points_and_rate(client):
    """员工端（回显 + 历史表 + 实时字段）与管理端（列表 + 对比页）都要展示。"""
    _seed_admin_staff_and_data(client)          # 建 admin + P1 两条自报（9/16: 4/2，9/17: 2/0）
    _seed_staff(client, "P1", "emp1", "甲")      # 员工账号
    # 员工端（2026-10-02 用户要求）：自动算 6 个数——1点分数/2点分数/总分数/总店铺数/两个占比
    _login_staff(client, "emp1")
    h = client.get("/my/report?month=2026-09").text
    for tid in ("live-stores", "live-p1pts", "live-p2pts", "live-points",
                "live-rate", "live-store-rate"):
        assert 'data-testid="%s"' % tid in h, tid          # 表单里实时算的 6 个字段
    tbl = h[h.index('data-testid="my-reports"'):]
    for label in ("总店铺数", "1点分数", "2点分数", "总分数", "2点分数占比", "2点店铺占比"):
        assert label in tbl, label
    assert "33.3%" in tbl                       # 9/16：2点店 2 / 总店 6
    assert "50.0%" in tbl                       # 9/16：2点分数 4 / 总分数 8
    assert "40.0%" in tbl and "25.0%" in tbl    # 本月合计：分数 4/10、店数 2/8
    assert ">10<" in tbl                        # 本月总分 = 4+2*2 + 2+0 = 10
    # 管理端
    _login_admin(client)
    a = client.get("/staff-reports?start=2026-09-16&end=2026-09-17").text
    assert "分数（点数）" in a and "2点分数占比" in a
    assert 'data-testid="points-' in a and "50.0%" in a          # 4/2 → 4/8 = 50.0%
    cmp_page = client.get("/staff-reports/compare?start=2026-09-16&end=2026-09-17").text
    assert "系统分数" in cmp_page and "自报分数" in cmp_page
    # 系统 9/16=4/2 → 8 分；9/17=5/0 → 5 分；合计 13 分；自报 4/2+2/0 → 8+2 = 10 分
    assert 'data-testid="sys-points-P1">13' in cmp_page
    assert 'data-testid="rep-points-P1">10' in cmp_page


def test_export_includes_points_and_rate(client):
    """导出也要带上分数与比例（自报明细 + 对比两页）。"""
    import io as _io

    from openpyxl import load_workbook
    _seed_admin_staff_and_data(client)
    _login_admin(client)
    r = client.get("/staff-reports/export?kind=reports&start=2026-09-16&end=2026-09-16")
    ws = load_workbook(_io.BytesIO(r.content)).active
    head = [c.value for c in ws[1]]
    assert "分数(点数)" in head and "2点分数占比%" in head
    row = [c.value for c in ws[2]]
    assert row[head.index("分数(点数)")] == 8          # 4×1 + 2×2
    assert row[head.index("2点分数占比%")] == 50.0      # 2点分数 4 / 总分数 8
    r2 = client.get("/staff-reports/export?kind=compare&start=2026-09-16&end=2026-09-17")
    wb2 = load_workbook(_io.BytesIO(r2.content))
    per_head = [c.value for c in wb2["对比(按人)"][1]]
    assert "系统分数" in per_head and "自报分数" in per_head and "自报2点分数占比%" in per_head
    day_head = [c.value for c in wb2["对比(逐日)"][1]]
    assert "系统分数" in day_head and "自报分数" in day_head


def test_reports_summary_and_cards(client):
    """统计卡：自报条数 + 总点数（分数）+ 2点比例（现算，不受分页影响）。"""
    from app.services import report_compare
    _seed_admin_staff_and_data(client)          # P1：9/16 = 4点1点/2点2家；9/17 = 2点1点/0
    db = appdb.SessionLocal()
    s = report_compare.reports_summary(db, start="2026-09-16", end="2026-09-17")
    assert s == {"count": 2, "p1": 6, "p2": 2, "stores": 8,
                 "p1_points": 6, "p2_points": 4, "points": 10,
                 "store_rate": 0.25, "rate": 0.4}
    # 单人工/日筛选也走同一口径
    one = report_compare.reports_summary(db, start="2026-09-16", end="2026-09-16")
    assert one["count"] == 1 and one["points"] == 8
    assert abs(one["rate"] - 4 / 8) < 1e-9                 # 2点分数 4 / 总分数 8
    db.close()
    _login_admin(client)
    h = client.get("/staff-reports?start=2026-09-16&end=2026-09-17").text
    # 自报汇总模块（2026-10-02 用户："8 个小块分两行"）：8 个数字都要在
    assert 'data-testid="report-summary"' in h
    mod = h[h.index('data-testid="report-summary"'):h.index('data-testid="reports-table"')]
    for tid, val in (("sum-stores-p1", "6"),      # 1点店铺数
                     ("sum-stores-p2", "2"),      # 2点店铺数
                     ("sum-stores", "8"),         # 总店铺数
                     ("sum-store-rate", "25.0%"),  # 2点店铺占比 = 2/8
                     ("sum-points-p1", "6"),      # 1点分数
                     ("sum-points-p2", "4"),      # 2点分数
                     ("sum-points", "10"),        # 总分数
                     ("sum-rate", "40.0%")):      # 2点分数占比 = 4/10
        assert ('data-testid="%s"' % tid) in mod, tid
        seg = mod[mod.index('data-testid="%s"' % tid):]
        assert val in seg[:160], (tid, val)
    assert mod.count("sum-tile") >= 8                # 8 个小块
    for label in ("1点店铺数", "2点店铺数", "总店铺数", "2点店铺占比",
                  "1点分数", "2点分数", "总分数", "2点分数占比"):
        assert label in mod, label
    assert 'class="stat-grid"' not in h.split('data-testid="reports-table"')[0]


def test_employee_selects_have_filter(client):
    """所有员工下拉都挂上搜索过滤组件（2026-10-02 用户："输入后五位或姓名一个字就能筛"）。"""
    _seed_admin_staff_and_data(client)
    _login_admin(client)
    # 看板要有一条月度统计才渲染"员工维度分析"块（否则是空态）
    from app.models import MonthPerfRecord
    db = appdb.SessionLocal()
    db.add(MonthPerfRecord(month="2026-09", person_code="P1", records=6,
                           p1=4, p2=2, points=8, salary=2000))
    db.commit()
    db.close()
    for url in ("/staff-reports?start=2026-09-16&end=2026-09-17",
                "/staff-reports/compare?start=2026-09-16&end=2026-09-17",
                "/dashboard"):
        h = client.get(url).text
        assert "data-emp-filter" in h, url
        assert "/static/emp_select.js" in h, url          # 组件脚本在所有页面都加载
    # 自报页有两处（筛选 + 补录卡片）
    page = client.get("/staff-reports?start=2026-09-16&end=2026-09-17").text
    assert page.count("data-emp-filter") >= 2
    assert "筛选：编号后5位 或 姓名一个字" in page
    # 静态资源可访问
    assert client.get("/static/emp_select.js").status_code == 200
