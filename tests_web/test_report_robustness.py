# -*- coding: utf-8 -*-
"""第一轮评审修复的行为测试：物化失效 / 状态不卡死 / 重试护栏 / 在跑复用 /
空文件不生成 / 可见月门槛 / 全角编号归一 / 导出白名单。"""
from datetime import date, datetime, timedelta

import pytest

import app.db as appdb
from app.auth import SESSION_COOKIE, hash_password, read_session_token
from app.models import (FormalRecord, ImportFile, Person, PersonDailyStat,
                        RawRecord, StaffDailyReport, StaffReportAnalysis,
                        StaffReportComparePerson, User)


# ---------- 夹具 ----------

def _staff(client, username="emp1", code="P1", name="甲", must_change=False):
    db = appdb.SessionLocal()
    if db.get(Person, code) is None:
        db.add(Person(code=code, display_name=name))
    if db.query(User).filter(User.username == username).first() is None:
        db.add(User(username=username, password_hash=hash_password("pw123456"),
                    display_name=name, role="staff", person_code=code,
                    is_active=True, status="active",
                    must_change_password=must_change))
    db.commit()
    db.close()


def _admin(client, username="admin"):
    db = appdb.SessionLocal()
    if db.query(User).filter(User.username == username).first() is None:
        db.add(User(username=username, password_hash=hash_password("pw123456"),
                    display_name="管理员", role="admin", is_active=True))
        db.commit()
    db.close()


def _login(client, username="emp1", password="pw123456"):
    client.post("/login", data={"username": username, "password": password},
                follow_redirects=False)
    return read_session_token(client.cookies.get(SESSION_COOKIE))["csrf"]


def _sys_day(db, code, d, p1, p2):
    db.add(PersonDailyStat(person_code=code, ref_date=d, records=p1 + p2,
                           p1=p1, p2=p2, points=p1 + p2 * 2))


def _mock_ai(monkeypatch):
    from app.services import ai_chat, report_ai
    monkeypatch.setattr(ai_chat, "configured", lambda: True)

    def fake_chat(prompt, timeout=None, retries=0, max_tokens=None,
                  return_usage=False):
        text = ('{"overall_comment":"ok","accuracy_notes":"n",'
                '"per_person":{}}')
        return (text, {"total_tokens": 7}) if return_usage else text
    monkeypatch.setattr(ai_chat, "chat", fake_chat)
    monkeypatch.setattr(report_ai, "_s",
                        lambda: type("S", (), {"ai_model": "mock",
                                               "report_ai_timeout": 1,
                                               "report_ai_max_tokens": 100})())


def _no_thread(monkeypatch):
    from app.services import report_ai
    monkeypatch.setattr(report_ai.threading, "Thread",
                        lambda *a, **k: type("T", (), {"start": lambda s: None})())


def _coverage(db, *dates, tag="rb"):
    """造"已导入文件的覆盖范围"（正式记录）——区间可用性以它为准。"""
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


def _done_analysis(db, start, end, person_code="P1"):
    from app.services import daily_report, report_ai
    res = daily_report.compare(db, start, end)
    a = StaffReportAnalysis(period_start=start, period_end=end, status="done",
                            summary=dict(res["summary"], fingerprint="fp"),
                            payload={"by_lang": {"zh": {}}})
    db.add(a)
    db.commit()
    report_ai.materialize(db, a.id, res)
    return a


def _compare_row(db, aid, code="P1"):
    return (db.query(StaffReportComparePerson)
            .filter(StaffReportComparePerson.analysis_id == aid,
                    StaffReportComparePerson.person_code == code).first())


# ---------- 物化失效（评审 Critical：员工端显示旧数字） ----------

def test_materialization_refreshes_after_report_change(client):
    """员工填报/修改后，覆盖该日期的报告物化行必须刷新（否则员工端是旧数字）。"""
    from app.services import daily_report
    db = appdb.SessionLocal()
    _sys_day(db, "P1", date(2026, 9, 16), 4, 2)
    db.commit()
    a = _done_analysis(db, date(2026, 9, 16), date(2026, 9, 17))
    # 先提交一条（9/16 当天由 jst_today 决定，这里直接用服务层改今天那条）
    u = db.query(User).filter(User.username == "emp1").first() or None
    if u is None:
        _staff(client)
        u = db.query(User).filter(User.username == "emp1").first()
    row = StaffDailyReport(person_code="P1", user_id=u.id,
                           report_date=daily_report.jst_today(), area="渋谷",
                           p1_cnt=1, p2_cnt=1, total_cnt=2)
    db.add(row)
    db.commit()
    # 这个日期不在 a 的区间内 → 物化行仍为 0
    assert _compare_row(db, a.id).rep_total == 0
    # 把报告区间挪到包含今天，再改填报 → 触发刷新
    a.period_end = daily_report.jst_today()
    db.commit()
    daily_report.update_today(db, u, area="渋谷", p1_cnt=5, p2_cnt=3)
    db.expire_all()
    assert _compare_row(db, a.id).rep_total == 8      # 刷新到最新数字
    daily_report.update_today(db, u, area="渋谷", p1_cnt=2, p2_cnt=2)
    db.expire_all()
    assert _compare_row(db, a.id).rep_total == 4      # 再改也跟得上
    db.close()


# ---------- 状态机：绝不卡在 running ----------

def test_run_analysis_marks_failed_on_unexpected_error(client, monkeypatch):
    """生成过程中任何意外异常 → 落 failed（绝不卡在 running/pending）。"""
    from app.services import daily_report, report_ai
    db = appdb.SessionLocal()
    _sys_day(db, "P1", date(2026, 9, 16), 4, 2)
    db.commit()
    a = StaffReportAnalysis(period_start=date(2026, 9, 16),
                            period_end=date(2026, 9, 17), status="pending",
                            summary={}, payload={})
    db.add(a)
    db.commit()
    monkeypatch.setattr(daily_report, "compare",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("炸了")))
    report_ai.run_analysis(a.id)
    db.expire_all()
    got = db.get(StaffReportAnalysis, a.id)
    assert got.status == "failed" and "炸了" in (got.ai_error or "")
    db.close()


def test_retry_refused_while_running(client):
    """在跑中的报告不允许重复触发；卡死的才允许重试。"""
    from app.services import report_ai
    db = appdb.SessionLocal()
    a = StaffReportAnalysis(period_start=date(2026, 9, 16),
                            period_end=date(2026, 9, 17), status="running",
                            summary={}, payload={}, created_at=datetime.utcnow())
    db.add(a)
    db.commit()
    _, msg = report_ai.retry(db, a.id)
    assert "正在生成" in msg and db.get(StaffReportAnalysis, a.id).status == "running"
    # 卡死（超时）→ 允许
    a.created_at = datetime.utcnow() - timedelta(minutes=report_ai.STALE_MINUTES + 1)
    db.commit()
    assert report_ai.is_stale(a) and report_ai.can_retry(a)
    db.close()


def test_start_analysis_reuses_inflight(client, monkeypatch):
    """同区间已有"正在生成"的记录 → 复用，不重复起线程/烧 token。"""
    from app.services import report_ai
    db = appdb.SessionLocal()
    _sys_day(db, "P1", date(2026, 9, 16), 4, 2)
    db.commit()
    inflight = StaffReportAnalysis(period_start=date(2026, 9, 16),
                                   period_end=date(2026, 9, 17),
                                   status="running", summary={}, payload={},
                                   created_at=datetime.utcnow())
    db.add(inflight)
    db.commit()
    monkeypatch.setattr(__import__("app.services.ai_chat", fromlist=["x"]),
                        "configured", lambda: True)
    a, msg = report_ai.start_analysis(db, None, date(2026, 9, 16),
                                      date(2026, 9, 17))
    assert a.id == inflight.id and "正在生成" in msg
    assert db.query(StaffReportAnalysis).count() == 1
    db.close()


# ---------- 空文件不生成报告 ----------

def test_auto_for_import_skips_import_without_formal_records(client, monkeypatch):
    """入表 0 条的文件 → 不生成报告（**不回退全局范围**，否则白烧 token）。"""
    from app.services import report_ai
    db = appdb.SessionLocal()
    imp = ImportFile(file_name="empty.xlsx", file_sha256="sha-empty2",
                     file_size=1, stored_path="/tmp/e.xlsx", uploaded_by=1,
                     status="parsed")
    db.add(imp)
    db.commit()
    assert report_ai.import_period(db, imp.id) == (None, None)
    assert "跳过" in report_ai.auto_for_import(db, imp.id)
    assert db.query(StaffReportAnalysis).count() == 0
    db.close()


# ---------- 可见月门槛 ----------

def test_feedback_hides_pre_launch_months(client, monkeypatch):
    """员工端核对页也遵守"员工可见起始月"（原先可绕过）。"""
    from app.services import perf
    db = appdb.SessionLocal()
    _staff(client)
    _sys_day(db, "P1", date(2026, 9, 16), 4, 2)
    _coverage(db, date(2026, 9, 16), date(2026, 9, 17))   # 有文件覆盖，区间才有意义
    db.commit()
    a = _done_analysis(db, date(2026, 9, 16), date(2026, 9, 17))
    db.close()
    monkeypatch.setattr(perf, "staff_visible_from", lambda db: "2026-10")
    _login(client)
    html = client.get("/my/report/feedback").text
    assert "我的准确率" not in html                     # 9 月被挡
    # 直链 aid 也不行
    html2 = client.get("/my/report/feedback?aid=%d" % a.id).text
    assert "我的准确率" not in html2
    # 放行后可见
    monkeypatch.setattr(perf, "staff_visible_from", lambda db: "")
    assert "我的准确率" in client.get("/my/report/feedback").text


# ---------- 全角编号归一 ----------

def test_fullwidth_submitter_code_normalized():
    """文件里的全角编号要归一成半角，否则与手工建号匹配不上、会重复建人。"""
    from store_settle.rules import parse_submitter
    name, code = parse_submitter("陈嘉溢(２１８８２４０６２６２７９０３８)")
    assert code == "2188240626279038"

    name2, code2 = parse_submitter("陈嘉溢(2188240626279038)")
    assert code2 == "2188240626279038"


# ---------- 导出白名单 ----------

def test_export_kind_whitelist(client):
    """kind 会进 Content-Disposition → 必须是白名单值（否则响应头畸形）。"""
    _admin(client)
    _login(client, "admin")
    r = client.get("/staff-reports/export?kind=evil%0D%0AX-Injected:%201")
    assert r.status_code == 400
