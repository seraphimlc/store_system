# -*- coding: utf-8 -*-
"""管理员补录/修改自报（规则：**已对账的日期锁定**——日期 ≤ 正式数据最后一天不可改）。

规则来源（2026-09-28 用户明确）：15 号的数据已导入 → 15 号及之前的自报不能再改，
16 号及以后的还可以补录/修改。
"""
from datetime import date

import app.db as appdb
from app.auth import SESSION_COOKIE, hash_password, read_session_token
from app.models import (FormalRecord, ImportFile, Person, PersonDailyStat,
                        RawRecord, StaffDailyReport, StaffReportAnalysis,
                        StaffReportComparePerson, User)
from app.services import bd_teams, daily_report, report_store
from tests.helpers import form_token


def _person(db, code="P1", name="甲"):
    if not code:                      # 管理员没有员工编号
        return
    if db.get(Person, code) is None:
        db.add(Person(code=code, display_name=name))
        db.commit()


def _user(client, username="emp1", code="P1", name="甲", role="staff"):
    db = appdb.SessionLocal()
    _person(db, code, name)
    if db.query(User).filter(User.username == username).first() is None:
        db.add(User(username=username, password_hash=hash_password("pw123456"),
                    display_name=name, role=role, person_code=code,
                    is_active=True, status="active",
                    must_change_password=False))
        db.commit()
    db.close()


def _login(client, username="admin", password="pw123456"):
    client.post("/login", data={"username": username, "password": password},
                follow_redirects=False)
    return read_session_token(client.cookies.get(SESSION_COOKIE))["csrf"]


def _coverage(db, *dates, tag="cov"):
    """造正式数据（= 已导入文件）→ 决定"已对账到哪天"。"""
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
        db.add(FormalRecord(import_id=imp.id, raw_record_id=raw.id,
                            person_code="P1", store_id_raw=raw.store_id_raw,
                            japan_date=d, points=1))
    db.commit()
    return imp


def _admin_save(client, csrf, **kw):
    data = {"csrf_token": csrf, "_ft": form_token(client), "start": "", "end": ""}
    data.update(kw)
    return client.post("/staff-reports/report/save", data=data,
                       follow_redirects=False)


# ---------- 规则：已对账即锁定 ----------

def test_coverage_end_decides_lock(client):
    """锁定判定 = 日期 ≤ 正式数据最后一天。"""
    db = appdb.SessionLocal()
    _person(db)
    _coverage(db, date(2026, 9, 14), date(2026, 9, 15), tag="lock")
    assert daily_report.coverage_end(db) == date(2026, 9, 15)
    assert daily_report.is_locked(db, date(2026, 9, 15)) is True     # 15 号（含）之前
    assert daily_report.is_locked(db, date(2026, 9, 14)) is True
    assert daily_report.is_locked(db, date(2026, 9, 16)) is False    # 16 号及以后可改
    db.close()


def test_admin_adds_report_for_unlocked_date(client):
    """管理员给未对账日期补录 → 落库（source=admin）。"""
    db = appdb.SessionLocal()
    _person(db)
    _coverage(db, date(2026, 9, 15), tag="add")
    db.close()
    _user(client, "admin", None, "管理员", role="admin")
    csrf = _login(client)
    r = _admin_save(client, csrf, person_code="P1", report_date="2026-09-20",
                    area="渋谷", p1_cnt="4", p2_cnt="3")
    assert r.status_code == 303 and "msg=" in r.headers["location"]
    db = appdb.SessionLocal()
    row = db.query(StaffDailyReport).filter(
        StaffDailyReport.report_date == date(2026, 9, 20)).one()
    assert (row.area, row.p1_cnt, row.p2_cnt, row.total_cnt, row.source) == \
        ("渋谷", 4, 3, 7, "admin")
    db.close()


def test_admin_updates_existing_unlocked_report(client):
    """管理员改员工已报的未对账日期 → 覆盖（含合计重算）。"""
    db = appdb.SessionLocal()
    _person(db)
    _coverage(db, date(2026, 9, 15), tag="upd")
    db.add(StaffDailyReport(person_code="P1", report_date=date(2026, 9, 18),
                            area="旧", p1_cnt=1, p2_cnt=1, total_cnt=2))
    db.commit()
    db.close()
    _user(client, "admin", None, "管理员", role="admin")
    csrf = _login(client)
    r = _admin_save(client, csrf, person_code="P1", report_date="2026-09-18",
                    area="新", p1_cnt="9", p2_cnt="2")
    assert r.status_code == 303 and "msg=" in r.headers["location"]
    db = appdb.SessionLocal()
    row = db.query(StaffDailyReport).filter(
        StaffDailyReport.report_date == date(2026, 9, 18)).one()
    assert (row.area, row.p1_cnt, row.p2_cnt, row.total_cnt, row.source) == \
        ("新", 9, 2, 11, "admin")
    assert db.query(StaffDailyReport).count() == 1        # 没有新增第二条
    db.close()


def test_admin_cannot_touch_locked_dates(client):
    """已对账的日期：补录与修改都被拒（且不落库、不改动已有值）。"""
    db = appdb.SessionLocal()
    _person(db)
    _coverage(db, date(2026, 9, 10), date(2026, 9, 15), tag="locked")
    db.add(StaffDailyReport(person_code="P1", report_date=date(2026, 9, 10),
                            area="原值", p1_cnt=2, p2_cnt=2, total_cnt=4))
    db.commit()
    db.close()
    _user(client, "admin", None, "管理员", role="admin")
    csrf = _login(client)

    r1 = _admin_save(client, csrf, person_code="P1", report_date="2026-09-12",
                     area="补录", p1_cnt="5", p2_cnt="5")     # 未对账？→ 12 ≤ 15 已锁定
    assert "err=" in r1.headers["location"]
    assert "%E5%B7%B2%E5%AF%B9%E8%B4%A6" in r1.headers["location"]  # 「已对账」

    r2 = _admin_save(client, csrf, person_code="P1", report_date="2026-09-10",
                     area="篡改", p1_cnt="99", p2_cnt="0")
    assert "err=" in r2.headers["location"]

    db = appdb.SessionLocal()
    row = db.query(StaffDailyReport).filter(
        StaffDailyReport.report_date == date(2026, 9, 10)).one()
    assert (row.area, row.p1_cnt, row.total_cnt) == ("原值", 2, 4)   # 原样
    assert db.query(StaffDailyReport).count() == 1                   # 没有补进去
    db.close()


def test_admin_edit_requires_admin(client):
    """员工调不动管理员补录接口。"""
    _user(client, "emp1", "P1", "甲")
    csrf = _login(client, "emp1")
    r = _admin_save(client, csrf, person_code="P1", report_date="2026-09-20",
                    area="x", p1_cnt="1", p2_cnt="1")
    # 员工首页 = 待填报出勤计划 /my/plan，否则每日自报 /my/report（date_plan.staff_home）
    assert r.status_code == 302 and r.headers["location"].startswith(
        ("/my/plan", "/my/report", "/my/perf"))


def test_admin_edit_refreshes_materialized(client):
    """补录/修改后，覆盖该日期的报告物化行要跟着刷新（否则员工端是旧数字）。"""
    db = appdb.SessionLocal()
    _person(db)
    _coverage(db, date(2026, 9, 16), tag="mat")            # 已对账到 9/16
    db.add(PersonDailyStat(person_code="P1", ref_date=date(2026, 9, 16),
                           records=6, p1=4, p2=2, points=8))
    db.commit()
    res_start, res_end = date(2026, 9, 16), date(2026, 9, 30)
    a = StaffReportAnalysis(period_start=res_start, period_end=res_end,
                            status="done", summary={}, payload={"by_lang": {"zh": {}}})
    db.add(a)
    db.commit()
    from app.services import report_compare
    report_store.materialize(db, a.id, report_compare.compare(db, res_start, res_end))
    before = (db.query(StaffReportComparePerson)
              .filter(StaffReportComparePerson.analysis_id == a.id,
                      StaffReportComparePerson.person_code == "P1").one().rep_total)
    assert before == 0
    db.close()
    _user(client, "admin", None, "管理员", role="admin")
    csrf = _login(client)
    r = _admin_save(client, csrf, person_code="P1", report_date="2026-09-20",
                    area="渋谷", p1_cnt="7", p2_cnt="3")
    assert "msg=" in r.headers["location"]
    db = appdb.SessionLocal()
    after = (db.query(StaffReportComparePerson)
             .filter(StaffReportComparePerson.analysis_id == a.id,
                     StaffReportComparePerson.person_code == "P1").one().rep_total)
    assert after == 10                                     # 物化行已刷新
    db.close()


def test_admin_save_validates_input(client):
    """编号必填 / 编号必须存在 / 数字非法。"""
    db = appdb.SessionLocal()
    _person(db)
    _coverage(db, date(2026, 9, 15), tag="val")
    db.close()
    _user(client, "admin", None, "管理员", role="admin")
    csrf = _login(client)
    cases = (dict(person_code="", report_date="2026-09-20"),                 # 没选员工
             dict(person_code="NOPE", report_date="2026-09-20"),             # 编号不存在
             dict(person_code="P1", report_date="2026-09-20", p1_cnt="abc"))  # 数字非法
    for kw in cases:
        data = {"area": "x", "p1_cnt": "1", "p2_cnt": "1"}
        data.update(kw)
        r = _admin_save(client, csrf, **data)
        assert "err=" in r.headers["location"], kw
    db = appdb.SessionLocal()
    assert db.query(StaffDailyReport).count() == 0
    db.close()


def test_employee_cannot_edit_today_once_reconciled(client):
    """员工当天修改走同一条规则：今天已对账 → 不能改。"""
    db = appdb.SessionLocal()
    _person(db)
    _coverage(db, daily_report.jst_today(), tag="emp")     # 今天的文件已导入
    db.add(StaffDailyReport(person_code="P1",
                            report_date=daily_report.jst_today(),
                            area="原值", p1_cnt=1, p2_cnt=1, total_cnt=2))
    db.commit()
    db.close()
    _user(client, "emp1", "P1", "甲")
    csrf = _login(client, "emp1")
    r = client.post("/my/report/update",
                    data={"csrf_token": csrf, "_ft": form_token(client),
                          "area": "改", "p1_cnt": "9", "p2_cnt": "9"},
                    follow_redirects=False)
    assert r.status_code == 303 and "err=" in r.headers["location"]
    db = appdb.SessionLocal()
    assert db.query(StaffDailyReport).one().area == "原值"
    db.close()


def test_list_page_marks_locked_rows(client):
    """列表页：已对账的行显示标记，未对账的行给出「改」。"""
    db = appdb.SessionLocal()
    _person(db)
    _coverage(db, date(2026, 9, 15), tag="page")
    db.add(StaffDailyReport(person_code="P1", report_date=date(2026, 9, 10),
                            area="旧", p1_cnt=1, p2_cnt=1, total_cnt=2))
    db.add(StaffDailyReport(person_code="P1", report_date=date(2026, 9, 20),
                            area="新", p1_cnt=2, p2_cnt=2, total_cnt=4))
    db.commit()
    db.close()
    _user(client, "admin", None, "管理员", role="admin")
    _login(client)
    html = client.get("/staff-reports?start=2026-09-01&end=2026-09-30").text
    assert "已对账" in html and "edit_date=2026-09-20" in html   # 9/10 锁定、9/20 可改
    assert "edit_date=2026-09-10" not in html

# ---------- 2026-10-10 两个功能：改自报（点数 + 当天进展）/ 补录只能点数 ----------
# 用户口径：
#   ① 管理员可以改自报数据 —— 改点数、改进展（同一天一个卡片、一次保存）；
#   ② 管理员可以补录员工的自报数据 —— 只能补录点数，补录不了任务进展。

def _mk_user(db, username, code, name, role="staff"):
    """连 Person 一起建好（进展/自报都要外键）。"""
    if code and db.get(Person, code) is None:
        db.add(Person(code=code, display_name=name))
        db.flush()
    u = db.query(User).filter(User.username == username).first()
    if u is None:
        u = User(username=username, display_name=name, role=role,
                 is_active=True, status="active", person_code=code,
                 password_hash=hash_password("pw123456"), must_change_password=False)
        db.add(u)
    db.flush()
    return u


def _seed_task_with_progress(*, on_date, pct=60, status="confirmed"):
    """造：员工 P1（member1）+ 队长 P9（leader1）+ 一个任务，那天的进展按 status 处理。"""
    from app.models import BdTask
    from app.services import bd_tasks
    db = appdb.SessionLocal()
    _mk_user(db, "member1", "P1", "甲", role="staff")
    _mk_user(db, "leader1", "P9", "队長", role="leader")   # ⚠️ 队长先建好再"确认"
    team = bd_teams.create_team(db, "P1队", by="admin")
    bd_teams.set_members(db, team.id, [("P9", "leader"), ("P1", "member")])
    st = bd_tasks.create_station(db, "測試駅", line="測試線")
    bd_tasks.create_tasks(db, [st.id], by="admin")
    task = db.query(BdTask).first()
    bd_tasks.set_task_team(db, [task.id], team.id, assign_date=on_date)
    bd_tasks.assign_members(db, task.id, ["P1"], by="admin")
    ms = db.query(User).filter(User.username == "member1").first()
    bd_tasks.save_progress(db, task.id, pct, "员工报的", by="member1",
                           actor_user=ms, on_date=on_date)
    if status in ("confirmed", "adjusted"):
        lg = db.query(User).filter(User.username == "leader1").first()
        bd_tasks.save_progress(db, task.id, pct, "", by="leader1", actor_user=lg,
                               confirm=(status == "confirmed"), on_date=on_date)
    db.commit()
    tid = task.id
    db.close()
    return tid


def test_admin_edit_day_updates_points_and_progress(client):
    """① 改自报：点数 + 当天进展一次保存都生效（留痕、任务当前进度跟着变）。"""
    from app.models import BdTask, BdTaskProgress, BdMessage
    on_date = date(2026, 10, 9)
    tid = _seed_task_with_progress(on_date=on_date, pct=60, status="confirmed")
    db = appdb.SessionLocal()
    db.add(StaffDailyReport(person_code="P1", report_date=on_date, area="旧",
                            p1_cnt=1, p2_cnt=1, total_cnt=2, source="web"))
    db.commit()
    msgs = db.query(BdMessage).count()
    db.close()
    _user(client, "admin", None, "管理员", role="admin")
    csrf = _login(client)
    r = _admin_save(client, csrf, person_code="P1", report_date=on_date.isoformat(),
                    area="新", p1_cnt="9", p2_cnt="3",
                    **{"pct_%d" % tid: "85", "note_%d" % tid: "管理员改的"})
    assert r.status_code == 303 and "msg=" in r.headers["location"], r.headers
    from urllib.parse import unquote
    assert "改了 1 个任务进展" in unquote(r.headers["location"])
    db = appdb.SessionLocal()
    rep = db.query(StaffDailyReport).one()
    assert (rep.p1_cnt, rep.p2_cnt, rep.total_cnt, rep.area) == (9, 3, 12, "新")
    row = db.query(BdTaskProgress).filter(BdTaskProgress.task_id == tid).one()
    assert row.pct == 85 and row.review_status == "adjusted"
    assert row.reviewed_by == "admin"
    assert row.reported_pct == 60 and row.reported_by == "member1", "员工原值要留住"
    assert "管理员改的" in (row.note or "")
    assert db.get(BdTask, tid).pct == 85, "任务当前进度要跟着变"
    assert db.query(BdMessage).count() > msgs, "改完要通知队员"
    db.close()


def test_admin_edit_day_rejects_progress_on_backfill(client):
    """② 补录（那天没有自报）→ 只能补点数；带进展被拦且一条都不写。"""
    from app.models import BdTaskProgress
    on_date = date(2026, 10, 9)
    tid = _seed_task_with_progress(on_date=on_date, pct=60, status="confirmed")
    _user(client, "admin", None, "管理员", role="admin")
    csrf = _login(client)
    # ②-a 带进展 → err，点数也不许写（整体回滚）
    r = _admin_save(client, csrf, person_code="P1", report_date=on_date.isoformat(),
                    p1_cnt="9", p2_cnt="0", **{"pct_%d" % tid: "85"})
    assert "err=" in r.headers["location"]
    from urllib.parse import unquote
    assert "只能补录点数" in unquote(r.headers["location"])
    db = appdb.SessionLocal()
    assert db.query(StaffDailyReport).count() == 0, "拦住了就一条都不许写"
    assert db.query(BdTaskProgress).one().pct == 60, "进展不许动"
    db.close()
    # ②-b 只补点数 → 成功，进展不动
    r2 = _admin_save(client, csrf, person_code="P1", report_date=on_date.isoformat(),
                     area="补", p1_cnt="9", p2_cnt="0")
    assert "msg=" in r2.headers["location"], r2.headers
    db = appdb.SessionLocal()
    rep = db.query(StaffDailyReport).one()
    assert (rep.p1_cnt, rep.p2_cnt, rep.source) == (9, 0, "admin")
    assert db.query(BdTaskProgress).one().pct == 60, "补录不碰进展"
    db.close()


def test_admin_edit_day_skips_unchanged_progress(client):
    """同值的进展不动：只改点数时不该把每条进展盖成"已调整"、也不该刷消息。"""
    from app.models import BdMessage, BdTaskProgress
    on_date = date(2026, 10, 9)
    tid = _seed_task_with_progress(on_date=on_date, pct=60, status="confirmed")
    db = appdb.SessionLocal()
    db.add(StaffDailyReport(person_code="P1", report_date=on_date, area="旧",
                            p1_cnt=1, p2_cnt=0, total_cnt=1))
    db.commit()
    before_msg = db.query(BdMessage).count()
    reviewed_at = db.query(BdTaskProgress).one().reviewed_at
    db.close()
    _user(client, "admin", None, "管理员", role="admin")
    csrf = _login(client)
    r = _admin_save(client, csrf, person_code="P1", report_date=on_date.isoformat(),
                    p1_cnt="5", p2_cnt="0", **{"pct_%d" % tid: "60"})   # 同值
    assert "msg=" in r.headers["location"]
    from urllib.parse import unquote
    assert "改了" not in unquote(r.headers["location"]), "同值不该算改动"
    db = appdb.SessionLocal()
    row = db.query(BdTaskProgress).one()
    assert row.review_status == "confirmed" and row.reviewed_by == "leader1"
    assert row.reviewed_at == reviewed_at, "同值不该重盖审核时间"
    assert db.query(BdMessage).count() == before_msg, "同值不该发消息"
    assert db.query(StaffDailyReport).one().p1_cnt == 5, "点数照改"
    db.close()


def test_admin_edit_card_shows_day_progress_only_for_existing(client):
    """界面：改已有自报 → 当天进展逐条给输入框；补录（没有自报）→ 只给点数 + 提示。"""
    on_date = date(2026, 10, 9)
    tid = _seed_task_with_progress(on_date=on_date, pct=55, status="confirmed")
    db = appdb.SessionLocal()
    db.add(StaffDailyReport(person_code="P1", report_date=on_date, area="x",
                            p1_cnt=2, p2_cnt=0, total_cnt=2))
    db.commit()
    db.close()
    _user(client, "admin", None, "管理员", role="admin")
    _login(client)
    html = client.get("/staff-reports?start=2026-10-01&end=2026-10-10"
                      "&person_code=P1&edit_date=%s" % on_date.isoformat()).text
    assert 'data-testid="day-progress"' in html
    assert 'name="pct_%d"' % tid in html and 'name="note_%d"' % tid in html
    assert 'value="55"' in html
    html2 = client.get("/staff-reports?start=2026-10-01&end=2026-10-10"
                       "&person_code=P1&edit_date=2026-10-08").text
    assert 'data-testid="backfill-hint"' in html2
    assert 'name="pct_%d"' % tid not in html2
