# -*- coding: utf-8 -*-
"""评审补齐项的行为测试：删除（自报/报告）、单日准确率、漏填汇总、逐日分页、指纹含逐日。"""
from datetime import date

import app.db as appdb
from app.auth import SESSION_COOKIE, hash_password, read_session_token
from app.models import (FormalRecord, ImportFile, Person, PersonDailyStat,
                        RawRecord, StaffDailyReport, StaffReportAnalysis,
                        StaffReportCompareDay, StaffReportComparePerson, User)
from app.services import daily_report, report_compare, report_store
from tests.helpers import form_token


def _person(db, code="P1", name="甲"):
    if not code:                      # 管理员没有员工编号
        return
    if db.get(Person, code) is None:
        db.add(Person(code=code, display_name=name))
        db.commit()


def _user(client, username="admin", code=None, name="管理员", role="admin"):
    db = appdb.SessionLocal()
    _person(db, code, name)
    if db.query(User).filter(User.username == username).first() is None:
        db.add(User(username=username, password_hash=hash_password("pw123456"),
                    display_name=name, role=role, person_code=code,
                    is_active=True, status="active", must_change_password=False))
        db.commit()
    db.close()


def _login(client, username="admin", password="pw123456"):
    client.post("/login", data={"username": username, "password": password},
                follow_redirects=False)
    return read_session_token(client.cookies.get(SESSION_COOKIE))["csrf"]


def _coverage(db, *dates, tag="cov"):
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


# ---------- 删除：自报 ----------

def test_admin_deletes_unlocked_report(client):
    db = appdb.SessionLocal()
    _person(db)
    _coverage(db, date(2026, 9, 15), tag="del1")
    db.add(StaffDailyReport(person_code="P1", report_date=date(2026, 9, 20),
                            area="x", p1_cnt=1, p2_cnt=1, total_cnt=2))
    db.commit()
    db.close()
    _user(client)
    csrf = _login(client)
    r = client.post("/staff-reports/report/delete",
                    data={"_ft": form_token(client), "csrf_token": csrf,
                          "person_code": "P1", "report_date": "2026-09-20"},
                    follow_redirects=False)
    assert r.status_code == 303 and "msg=" in r.headers["location"]
    db = appdb.SessionLocal()
    assert db.query(StaffDailyReport).count() == 0
    db.close()


def test_admin_cannot_delete_locked_report(client):
    db = appdb.SessionLocal()
    _person(db)
    _coverage(db, date(2026, 9, 15), tag="del2")
    db.add(StaffDailyReport(person_code="P1", report_date=date(2026, 9, 10),
                            area="x", p1_cnt=1, p2_cnt=1, total_cnt=2))
    db.commit()
    db.close()
    _user(client)
    csrf = _login(client)
    r = client.post("/staff-reports/report/delete",
                    data={"_ft": form_token(client), "csrf_token": csrf,
                          "person_code": "P1", "report_date": "2026-09-10"},
                    follow_redirects=False)
    assert "err=" in r.headers["location"]
    db = appdb.SessionLocal()
    assert db.query(StaffDailyReport).count() == 1        # 还在
    db.close()


def test_delete_by_admin_force_removes_locked(client):
    """脚本用的 force 路径：连已对账的也能删（演示数据清理）。"""
    db = appdb.SessionLocal()
    _person(db)
    _coverage(db, date(2026, 9, 15), tag="del3")
    db.add(StaffDailyReport(person_code="P1", report_date=date(2026, 9, 10),
                            area="x", p1_cnt=1, p2_cnt=1, total_cnt=2))
    db.commit()
    assert daily_report.delete_by_admin(db, person_code="P1",
                                        report_date=date(2026, 9, 10),
                                        force=True) is True
    assert db.query(StaffDailyReport).count() == 0
    db.close()


# ---------- 删除：报告 ----------

def test_delete_analysis_removes_materialized_rows(client):
    db = appdb.SessionLocal()
    _person(db)
    _coverage(db, date(2026, 9, 16), tag="del4")
    db.add(PersonDailyStat(person_code="P1", ref_date=date(2026, 9, 16),
                           records=6, p1=4, p2=2, points=8))
    db.add(StaffDailyReport(person_code="P1", report_date=date(2026, 9, 16),
                            area="x", p1_cnt=4, p2_cnt=2, total_cnt=6))
    db.commit()
    a = StaffReportAnalysis(period_start=date(2026, 9, 16),
                            period_end=date(2026, 9, 16), status="done",
                            summary={}, payload={"by_lang": {"zh": {}}})
    db.add(a)
    db.commit()
    report_store.materialize(db, a.id, report_compare.compare(
        db, date(2026, 9, 16), date(2026, 9, 16)))
    assert db.query(StaffReportComparePerson).count() == 1
    db.close()
    _user(client)
    csrf = _login(client)
    r = client.post("/staff-reports/analysis/%d/delete" % a.id,
                    data={"_ft": form_token(client), "csrf_token": csrf},
                    follow_redirects=False)
    assert r.status_code == 303 and "msg=" in r.headers["location"]
    db = appdb.SessionLocal()
    assert db.query(StaffReportAnalysis).count() == 0
    assert db.query(StaffReportComparePerson).count() == 0
    assert db.query(StaffReportCompareDay).count() == 0
    assert db.query(StaffDailyReport).count() == 1        # 自报不受影响
    db.close()


# ---------- 单日准确率 ----------

def test_daily_acc_day_matches_spec(client):
    """acc_day = max(0, 1 − |Δ| ÷ max(系统,1))，只算 both 日。"""
    db = appdb.SessionLocal()
    _person(db)
    db.add(PersonDailyStat(person_code="P1", ref_date=date(2026, 9, 16),
                           records=10, p1=7, p2=3, points=13))
    db.add(StaffDailyReport(person_code="P1", report_date=date(2026, 9, 16),
                            area="x", p1_cnt=7, p2_cnt=1, total_cnt=8))
    db.add(PersonDailyStat(person_code="P1", ref_date=date(2026, 9, 17),
                           records=4, p1=4, p2=0, points=4))
    # 17 号员工没报 → missing_report，不给 acc
    db.commit()
    res = report_compare.compare(db, date(2026, 9, 16), date(2026, 9, 17))
    by_date = {r["date"]: r for r in res["daily"]}
    d16 = by_date[date(2026, 9, 16)]
    assert d16["kind"] == "both" and d16["sys_total"] == 10 and d16["dt"] == 2
    assert abs(d16["acc"] - 0.8) < 1e-9                   # 1 − 2/10
    assert by_date[date(2026, 9, 17)]["acc"] is None      # 漏填日不算准确率
    db.close()


def test_daily_acc_floors_at_zero(client):
    db = appdb.SessionLocal()
    _person(db)
    db.add(PersonDailyStat(person_code="P1", ref_date=date(2026, 9, 16),
                           records=2, p1=2, p2=0, points=2))
    db.add(StaffDailyReport(person_code="P1", report_date=date(2026, 9, 16),
                            area="x", p1_cnt=9, p2_cnt=9, total_cnt=18))
    db.commit()
    res = report_compare.compare(db, date(2026, 9, 16), date(2026, 9, 16))
    assert res["daily"][0]["acc"] == 0.0                  # 不可能为负
    db.close()


def test_employee_page_shows_daily_acc(client):
    db = appdb.SessionLocal()
    _person(db)
    _user(client, "emp1", "P1", "甲", role="staff")
    _coverage(db, date(2026, 9, 16), tag="acc-ui")
    db.add(PersonDailyStat(person_code="P1", ref_date=date(2026, 9, 16),
                           records=10, p1=7, p2=3, points=13))
    db.add(StaffDailyReport(person_code="P1", report_date=date(2026, 9, 16),
                            area="x", p1_cnt=7, p2_cnt=1, total_cnt=8))
    db.commit()
    from app.services import perf
    a = StaffReportAnalysis(period_start=date(2026, 9, 16),
                            period_end=date(2026, 9, 16), status="done",
                            summary={}, payload={"by_lang": {"zh": {}}})
    db.add(a)
    db.commit()
    report_store.materialize(db, a.id, report_compare.compare(
        db, date(2026, 9, 16), date(2026, 9, 16)))
    db.close()
    _login(client, "emp1")
    import pytest
    # 可见月门槛放行（默认 2026-10 会挡掉 9 月）
    from unittest import mock
    with mock.patch.object(perf, "staff_visible_from", lambda db: ""):
        html = client.get("/my/report/feedback").text
    assert "准确率" in html and "80.0%" in html            # 单日准确率显示出来了
    db.close()


# ---------- 漏填汇总 ----------

def test_missing_summary_lists_gaps(client):
    db = appdb.SessionLocal()
    _person(db)
    for d, filled in ((date(2026, 9, 16), True), (date(2026, 9, 17), False),
                      (date(2026, 9, 18), False)):
        db.add(PersonDailyStat(person_code="P1", ref_date=d, records=5,
                               p1=5, p2=0, points=5))
        if filled:
            db.add(StaffDailyReport(person_code="P1", report_date=d, area="x",
                                    p1_cnt=5, p2_cnt=0, total_cnt=5))
    db.commit()
    ms = report_compare.missing_summary(db, date(2026, 9, 16),
                                        date(2026, 9, 18))
    assert len(ms) == 1
    m = ms[0]
    assert (m["days_system"], m["days_filled"], m["gaps"]) == (3, 1, 2)
    assert m["dates"] == [date(2026, 9, 17), date(2026, 9, 18)]
    db.close()


# ---------- 逐日分页 ----------

def test_compare_daily_no_pagination_for_small_range(client):
    """行数少时不出现分页控件（避免噪音）。"""
    db = appdb.SessionLocal()
    _person(db)
    _user(client)
    _coverage(db, date(2026, 9, 1), date(2026, 9, 5), tag="page")
    for i in range(5):
        d = date(2026, 9, i + 1)
        db.add(PersonDailyStat(person_code="P1", ref_date=d, records=3,
                               p1=3, p2=0, points=3))
        db.add(StaffDailyReport(person_code="P1", report_date=d, area="x",
                                p1_cnt=3, p2_cnt=0, total_cnt=3))
    db.commit()
    db.close()
    _login(client)
    html = client.get("/staff-reports/compare?start=2026-09-01&end=2026-09-05").text
    assert "dpage=2" not in html
    db.close()


def test_compare_daily_slices_over_100_rows(client):
    """逐日表真分页：120 行 → 第 1 页 100 行、第 2 页 20 行。"""
    from datetime import timedelta
    db = appdb.SessionLocal()
    _person(db)
    _user(client)
    _coverage(db, date(2026, 9, 1), date(2026, 12, 31), tag="page2")
    d = date(2026, 9, 1)
    for _ in range(120):
        db.add(PersonDailyStat(person_code="P1", ref_date=d, records=2,
                               p1=2, p2=0, points=2))
        db.add(StaffDailyReport(person_code="P1", report_date=d, area="x",
                                p1_cnt=2, p2_cnt=0, total_cnt=2))
        d = d + timedelta(days=1)
    db.commit()
    db.close()
    _login(client)
    url = "/staff-reports/compare?start=2026-09-01&end=2026-12-31"
    h1 = client.get(url).text
    h2 = client.get(url + "&dpage=2").text
    assert "dpage=2" in h1 and "dpage=1" in h2
    # 数据行数（类型列的 pill 数）第 1 页 100、第 2 页 20
    assert h1.count('class="pill ok"') == 100
    assert h2.count('class="pill ok"') == 20
    db.close()


# ---------- 指纹含逐日分布 ----------

def test_fingerprint_detects_moved_days(client):
    """合计相同但日子挪了 → 指纹必须不同（否则会复用旧报告，评语里的日期对不上）。"""
    from app.services import report_ai
    base = {"start": date(2026, 9, 1), "end": date(2026, 9, 2),
            "counts": {}, "persons": [{"person_code": "P1", "sys_p1": 5,
                                       "sys_p2": 0, "rep_p1": 5, "rep_p2": 0}]}
    a = dict(base, daily=[{"person_code": "P1", "date": date(2026, 9, 1),
                           "kind": "both", "sys_total": 5, "rep_total": 5, "dt": 0},
                          {"person_code": "P1", "date": date(2026, 9, 2),
                           "kind": "both", "sys_total": 5, "rep_total": 4, "dt": 1}])
    b = dict(base, daily=[{"person_code": "P1", "date": date(2026, 9, 1),
                           "kind": "both", "sys_total": 5, "rep_total": 4, "dt": 1},
                          {"person_code": "P1", "date": date(2026, 9, 2),
                           "kind": "both", "sys_total": 5, "rep_total": 5, "dt": 0}])
    assert report_ai.data_fingerprint(a) != report_ai.data_fingerprint(b)


# ---------- 我的绩效：默认月份 = 最近有数据的月份 ----------

def test_my_perf_defaults_to_latest_month(client):
    """默认月份取**最近**有数据的月份（原先取了 months[-1]，即最老的月份）。"""
    db = appdb.SessionLocal()
    _person(db)
    _user(client, "emp1", "P1", "甲", role="staff")
    for d, mo in ((date(2026, 7, 10), "2026-07"), (date(2026, 8, 10), "2026-08"),
                  (date(2026, 9, 10), "2026-09")):
        db.add(PersonDailyStat(person_code="P1", ref_date=d, records=3, p1=3,
                               p2=0, points=3))
    db.commit()
    from app.models import MonthPerfRecord
    for mo in ("2026-07", "2026-08", "2026-09"):      # 月绩效物化行（逐日明细在同一分支渲染）
        db.add(MonthPerfRecord(month=mo, person_code="P1", records=3, p1=3,
                               p2=0, points=3, salary=750))
    db.commit()
    db.close()
    _login(client, "emp1")
    from unittest import mock
    from app.services import perf
    with mock.patch.object(perf, "staff_visible_from", lambda db: ""):
        html = client.get("/my/perf").text
    assert "每日明细" in html
    assert "2026-09-10" in html                  # 默认 = 最近月份（9 月）的逐日明细
    assert "2026-08-10" not in html              # 不是 8 月
    # 显式指定月份仍然可用
    with mock.patch.object(perf, "staff_visible_from", lambda db: ""):
        html8 = client.get("/my/perf?month=2026-08").text
    assert "2026-08-10" in html8
    db.close()
