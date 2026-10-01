# -*- coding: utf-8 -*-
"""日期计划（半月出勤登记）测试：规格 `docs/specs-date-plan.md`。

口径（与用户确认）：
- 自然半月 1–15 / 16–月末，登记截止 3 号 / 18 号；
- 默认每天都出勤，只把"不出勤"的日子落库；
- 三态：○ 可出勤 / × 不出勤 / □ 已出勤（已自报；**已自报覆盖计划**）；
- 锁定：已过去的日期、已有自报的日期不可再改（逾期只提示不拦）。
"""
from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

import app.db as appdb
from app.auth import SESSION_COOKIE, hash_password, read_session_token
from app.models import Person, StaffDailyReport, StaffDatePlan, User
from app.services import date_plan
from tests.helpers import form_token

TODAY = date(2026, 10, 10)      # 服务层用的固定"今天"（H2 尚未到）


# ---------------- 工具 ----------------

def _person(db, code="P1", name="甲"):
    if db.get(Person, code) is None:
        db.add(Person(code=code, display_name=name))
        db.commit()
    return code


def _staff(client, username="emp1", code="P1", name="甲", password="pw123456"):
    db = appdb.SessionLocal()
    _person(db, code, name)
    if db.query(User).filter(User.username == username).first() is None:
        db.add(User(username=username, display_name=name, role="staff",
                    person_code=code, password_hash=hash_password(password),
                    is_active=True, status="active", must_change_password=False))
        db.commit()
    db.close()
    from fastapi.testclient import TestClient  # noqa: F401
    client.post("/login", data={"username": username, "password": password},
                follow_redirects=False)
    return read_session_token(client.cookies.get(SESSION_COOKIE))["csrf"]


def _admin(client, username="admin", password="pw123456"):
    db = appdb.SessionLocal()
    if db.query(User).filter(User.username == username).first() is None:
        db.add(User(username=username, password_hash=hash_password(password),
                    display_name="管理员", role="admin", is_active=True))
        db.commit()
    db.close()
    client.post("/login", data={"username": username, "password": password},
                follow_redirects=False)
    return read_session_token(client.cookies.get(SESSION_COOKIE))["csrf"]


class _U:
    """假的登录用户（服务层测试不需要真账号）。"""

    def __init__(self, code="P1", uid=1):
        self.person_code = code
        self.id = uid


# ---------------- 半月划分 ----------------

def test_period_of_splits_month_in_half():
    assert date_plan.period_of(date(2026, 10, 1)) == "2026-10-H1"
    assert date_plan.period_of(date(2026, 10, 15)) == "2026-10-H1"
    assert date_plan.period_of(date(2026, 10, 16)) == "2026-10-H2"
    assert date_plan.period_of(date(2026, 10, 31)) == "2026-10-H2"


def test_period_bounds_and_deadline():
    """H1=1–15，H2=16–月末；截止日 3 号 / 18 号。"""
    assert date_plan.period_bounds("2026-10-H1") == (
        date(2026, 10, 1), date(2026, 10, 15), date(2026, 10, 3))
    assert date_plan.period_bounds("2026-10-H2") == (
        date(2026, 10, 16), date(2026, 10, 31), date(2026, 10, 18))


def test_period_days_length_varies_by_month():
    """H2 天数随月份变化（28/29/30/31 天），不能写死 15。"""
    assert len(date_plan.period_days("2026-10-H1")) == 15
    assert len(date_plan.period_days("2026-10-H2")) == 16
    assert len(date_plan.period_days("2026-04-H2")) == 15
    assert len(date_plan.period_days("2026-02-H2")) == 13
    assert date_plan.period_bounds("2028-02-H2")[1] == date(2028, 2, 29)  # 闰年


def test_shift_period_across_year_boundary():
    assert date_plan.shift_period("2026-12-H2", halves=1) == "2027-01-H1"
    assert date_plan.shift_period("2027-01-H1", halves=-1) == "2026-12-H2"
    assert date_plan.shift_period("2026-10-H1", halves=1) == "2026-10-H2"
    assert date_plan.shift_period("2026-10-H1", months=1) == "2026-11-H1"


def test_parse_period_rejects_garbage():
    for bad in ("", "2026-10", "2026-10-H3", "abc", "2026-13-H1"):
        with pytest.raises(ValueError):
            date_plan.period_bounds(bad)


def test_period_options_marks_current_and_overdue():
    opts = date_plan.period_options(date(2026, 10, 10), back=2, fwd=1)
    keys = [o["key"] for o in opts]
    assert keys == ["2026-09-H1", "2026-09-H2", "2026-10-H1", "2026-10-H2"]
    assert [o["current"] for o in opts] == [False, False, True, False]
    # H1 截止 3 号 → 10 号已逾期；H2 截止 18 号 → 未逾期
    assert [o["overdue"] for o in opts] == [True, True, True, False]


def test_current_period_contains_today():
    for d in (date(2026, 10, 1), date(2026, 10, 15), date(2026, 10, 16),
              date(2026, 2, 28)):
        key = date_plan.period_of(d)
        start, end, _ = date_plan.period_bounds(key)
        assert start <= d <= end


# ---------------- 三态与锁定（纯函数） ----------------

def test_cell_state_priority():
    """已自报覆盖计划值；没有计划行 = 未登记。"""
    assert date_plan.cell_state(True, False) == date_plan.STATE_ON
    assert date_plan.cell_state(False, False) == date_plan.STATE_OFF
    assert date_plan.cell_state(None, False) == date_plan.STATE_NONE
    assert date_plan.cell_state(False, True) == date_plan.STATE_DONE   # 自报优先
    assert date_plan.cell_state(None, True) == date_plan.STATE_DONE
    assert date_plan.MARKS[date_plan.STATE_ON] == "○"
    assert date_plan.MARKS[date_plan.STATE_OFF] == "×"
    assert date_plan.MARKS[date_plan.STATE_DONE] == "□"
    assert date_plan.MARKS[date_plan.STATE_NONE] == "–"


def test_lock_of_rules():
    today = date(2026, 10, 10)
    assert date_plan.lock_of(date(2026, 10, 9), today, False) == date_plan.LOCK_PAST
    assert date_plan.lock_of(date(2026, 10, 10), today, False) == date_plan.LOCK_NONE
    assert date_plan.lock_of(date(2026, 10, 20), today, False) == date_plan.LOCK_NONE
    # 已自报优先于"已过去"（提示语更有信息量）
    assert date_plan.lock_of(date(2026, 10, 9), today, True) == date_plan.LOCK_REPORTED


# ---------------- 数据层 ----------------

def test_plan_row_roundtrip_and_unique(client):
    db = appdb.SessionLocal()
    code = _person(db)
    db.add(StaffDatePlan(person_code=code, plan_date=date(2026, 10, 20),
                         available=False))
    db.commit()
    r = db.query(StaffDatePlan).one()
    assert r.available is False and r.source == "web"
    assert r.created_at is not None and r.updated_at is not None
    db.add(StaffDatePlan(person_code=code, plan_date=date(2026, 10, 20),
                         available=True))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    assert db.query(StaffDatePlan).count() == 1


# ---------------- 提交（save_plan） ----------------

def test_save_plan_defaults_every_day_available(client):
    """只提交"不出勤"集合 → 其余日期全部按默认可出勤落库。"""
    db = appdb.SessionLocal()
    _person(db)
    r = date_plan.save_plan(db, _U(), "2026-10-H2",
                            ["2026-10-20", "2026-10-21"], today=TODAY)
    assert r["written"] == 16 and r["off_cnt"] == 2
    rows = {x.plan_date: x.available for x in db.query(StaffDatePlan).all()}
    assert len(rows) == 16
    assert rows[date(2026, 10, 20)] is False
    assert rows[date(2026, 10, 21)] is False
    assert rows[date(2026, 10, 16)] is True
    assert date_plan.is_submitted(db, "P1", "2026-10-H2") is True
    assert date_plan.is_submitted(db, "P1", "2026-10-H1") is False


def test_save_plan_is_upsert_not_append(client):
    """重复提交 = 覆盖，不产生重复行、不留下旧值。"""
    db = appdb.SessionLocal()
    _person(db)
    date_plan.save_plan(db, _U(), "2026-10-H2", ["2026-10-20"], today=TODAY)
    date_plan.save_plan(db, _U(), "2026-10-H2", ["2026-10-22"], today=TODAY)
    rows = db.query(StaffDatePlan).filter(
        StaffDatePlan.plan_date >= date(2026, 10, 16)).all()
    assert len(rows) == 16
    by_date = {x.plan_date: x.available for x in rows}
    assert by_date[date(2026, 10, 20)] is True     # 取消勾选 → 恢复可出勤
    assert by_date[date(2026, 10, 22)] is False


def test_save_plan_skips_past_days(client):
    """已过去的日期不动：既不新增默认行，也不被后续提交重置。"""
    db = appdb.SessionLocal()
    _person(db)
    date_plan.save_plan(db, _U(), "2026-10-H1", ["2026-10-10", "2026-10-12"],
                        today=date(2026, 10, 10))
    rows = {x.plan_date: x.available for x in db.query(StaffDatePlan).all()}
    assert min(rows) == date(2026, 10, 10)          # 10 号之前没有行
    assert len(rows) == 6                           # 10–15
    assert rows[date(2026, 10, 10)] is False and rows[date(2026, 10, 12)] is False
    # 第二天再提交：10 号已过去 → 不进表单、不被重置（仍是不出勤）
    r = date_plan.save_plan(db, _U(), "2026-10-H1", ["2026-10-11"],
                            today=date(2026, 10, 11))
    assert r["skipped"] == 10 and r["written"] == 5
    rows2 = {x.plan_date: x.available for x in db.query(StaffDatePlan).all()}
    assert rows2[date(2026, 10, 10)] is False       # 锁定的过去日期保持原值
    assert rows2[date(2026, 10, 11)] is False       # 今天：本次勾选生效
    assert rows2[date(2026, 10, 12)] is True        # 未来：取消勾选 → 恢复可出勤
    assert len(rows2) == 6                          # 10 号之前仍然没有行


def test_save_plan_keeps_reported_days_untouched(client):
    """已有自报的日子：计划值不被覆盖（自报是事实，计划只是预报）。"""
    db = appdb.SessionLocal()
    _person(db)
    db.add(StaffDailyReport(person_code="P1", report_date=date(2026, 10, 12),
                            p1_cnt=1, p2_cnt=0, total_cnt=1))
    db.commit()
    date_plan.save_plan(db, _U(), "2026-10-H1", ["2026-10-12"],
                        today=date(2026, 10, 5))
    assert (db.query(StaffDatePlan)
            .filter(StaffDatePlan.plan_date == date(2026, 10, 12)).first()) is None
    view = date_plan.plan_days(db, "P1", "2026-10-H1", today=date(2026, 10, 5))
    day = [d for d in view["days"] if d["date"] == date(2026, 10, 12)][0]
    assert day["state"] == date_plan.STATE_DONE and day["mark"] == "□"
    assert day["editable"] is False and day["lock"] == date_plan.LOCK_REPORTED


def test_save_plan_all_locked_raises(client):
    """整个半月都已过去 → 报错，且不留下半条数据。"""
    db = appdb.SessionLocal()
    _person(db)
    with pytest.raises(date_plan.AllLocked):
        date_plan.save_plan(db, _U(), "2026-09-H1", [], today=date(2026, 10, 10))
    assert db.query(StaffDatePlan).count() == 0


def test_save_plan_requires_person_code(client):
    db = appdb.SessionLocal()
    with pytest.raises(ValueError):
        date_plan.save_plan(db, _U(code=None), "2026-10-H2", [], today=TODAY)


def test_save_plan_rejects_out_of_range_and_bad_dates(client):
    """越界/非法日期一律报错（不静默丢，否则成"以为改了其实没改"）。"""
    db = appdb.SessionLocal()
    _person(db)
    with pytest.raises(ValueError):
        date_plan.save_plan(db, _U(), "2026-10-H2", ["2026-10-15"], today=TODAY)
    with pytest.raises(ValueError):
        date_plan.save_plan(db, _U(), "2026-10-H2", ["2026/10/20"], today=TODAY)
    assert db.query(StaffDatePlan).count() == 0


# ---------------- 员工端视图 ----------------

def test_plan_days_defaults_and_marks(client):
    """未登记的可改日期按"默认每天都出勤"显示 ○；已过去且无登记显示 –。"""
    db = appdb.SessionLocal()
    _person(db)
    today = date(2026, 10, 2)                  # H1 截止 10/3 → 尚未逾期
    view = date_plan.plan_days(db, "P1", "2026-10-H1", today=today)
    assert view["submitted"] is False and view["overdue"] is False
    past, future = view["days"][0], view["days"][1]
    assert past["date"] == date(2026, 10, 1) and past["editable"] is False
    assert past["state"] == date_plan.STATE_NONE and past["mark"] == "–"
    assert future["date"] == date(2026, 10, 2) and future["editable"] is True
    assert future["state"] == date_plan.STATE_NONE and future["mark"] == "○"
    assert view["editable_cnt"] == 14          # 2–15
    assert view["free_cnt"] == 15
    assert view["overdue"] is False
    # 逾期只提示不拦：10/5 再看，仍然可改
    late = date_plan.plan_days(db, "P1", "2026-10-H1", today=date(2026, 10, 5))
    assert late["overdue"] is True and late["editable_cnt"] == 11


def test_plan_days_counts_after_save(client):
    db = appdb.SessionLocal()
    _person(db)
    date_plan.save_plan(db, _U(), "2026-10-H2", ["2026-10-20", "2026-10-21"],
                        today=TODAY)
    view = date_plan.plan_days(db, "P1", "2026-10-H2", today=TODAY)
    assert view["submitted"] is True and view["last_at"] is not None
    assert view["off_cnt"] == 2 and view["free_cnt"] == 14
    off = [d for d in view["days"] if d["off"]]
    assert [d["mark"] for d in off] == ["×", "×"]


# ---------------- 管理端矩阵 ----------------

def _seed_matrix(db):
    _person(db, "P1", "甲")
    _person(db, "P2", "乙")
    for code, name in (("P1", "甲"), ("P2", "乙")):
        db.add(User(username="u" + code, display_name=name, role="staff",
                    person_code=code, password_hash="x", is_active=True,
                    status="active"))
    db.commit()


def test_admin_matrix_states_counts_and_unsubmitted(client):
    db = appdb.SessionLocal()
    _seed_matrix(db)
    # 甲登记了（20 号不出勤），乙没登记
    date_plan.save_plan(db, _U("P1"), "2026-10-H2", ["2026-10-20"], today=TODAY)
    # 甲 16 号已自报 → 方框（覆盖计划）
    db.add(StaffDailyReport(person_code="P1", report_date=date(2026, 10, 16),
                            p1_cnt=2, p2_cnt=1, total_cnt=3))
    db.commit()
    m = date_plan.admin_matrix(db, "2026-10-H2", today=TODAY)
    rows = {r["person_code"]: r for r in m["rows"]}
    assert set(rows) == {"P1", "P2"}
    assert rows["P1"]["submitted"] is True and rows["P2"]["submitted"] is False
    assert m["unsubmitted"] == ["P2"]
    d16, d20 = date(2026, 10, 16), date(2026, 10, 20)
    assert rows["P1"]["states"][d16] == date_plan.STATE_DONE
    assert rows["P1"]["states"][d20] == date_plan.STATE_OFF
    assert rows["P1"]["states"][date(2026, 10, 17)] == date_plan.STATE_ON
    assert rows["P2"]["states"][d16] == date_plan.STATE_NONE    # 未登记 ≠ 可出勤
    # 按日小计：16 号可出勤 = 甲（方框），乙未登记不算
    assert m["free_cnt"][d16] == 1
    assert m["none_cnt"][d16] == 1
    assert m["past"][d16] is False


def test_admin_matrix_excludes_past_days_from_free_count(client):
    """已经过去的日子不构成可用人力 → 小计置 0，但格子照旧显示三态。"""
    db = appdb.SessionLocal()
    _seed_matrix(db)
    date_plan.save_plan(db, _U("P1"), "2026-10-H2", [], today=date(2026, 10, 19))
    m = date_plan.admin_matrix(db, "2026-10-H2", today=date(2026, 10, 22))
    rows = {r["person_code"]: r for r in m["rows"]}
    past = date(2026, 10, 20)
    assert m["past"][past] is True
    assert m["free_cnt"][past] == 0
    assert rows["P1"]["states"][past] == date_plan.STATE_ON    # 格子照旧显示三态
    future = date(2026, 10, 25)
    assert m["past"][future] is False and m["free_cnt"][future] == 1


def test_admin_matrix_hides_inactive_staff_without_plan(client):
    """停用账号且本期没登记的人不进矩阵；登记过的人必须显示（否则数据被静默吞掉）。"""
    db = appdb.SessionLocal()
    _seed_matrix(db)
    u = db.query(User).filter(User.person_code == "P2").one()
    u.is_active, u.status = False, "resigned"
    db.commit()
    m = date_plan.admin_matrix(db, "2026-10-H2", today=TODAY)
    assert [r["person_code"] for r in m["rows"]] == ["P1"]
    date_plan.save_plan(db, _U("P2"), "2026-10-H2", [], today=TODAY)
    m2 = date_plan.admin_matrix(db, "2026-10-H2", today=TODAY)
    assert set(r["person_code"] for r in m2["rows"]) == {"P1", "P2"}


# ---------------- 路由：员工端 ----------------

def test_my_plan_requires_login(client):
    r = client.get("/my/plan", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("/login")


def test_my_plan_page_renders_three_states(client):
    _staff(client)
    db = appdb.SessionLocal()
    db.add(StaffDailyReport(person_code="P1", report_date=date.today(),
                            p1_cnt=1, p2_cnt=0, total_cnt=1))
    db.commit()
    db.close()
    r = client.get("/my/plan")
    assert r.status_code == 200
    assert "出勤计划" in r.text
    assert "默认每天都出勤" in r.text
    assert "已出勤（已自报）" in r.text
    assert 'data-testid="plan-form"' in r.text
    assert 'name="unavailable"' in r.text               # 可改日期有勾选框
    assert r.text.count("pm done") >= 1                 # 今天已自报 → 方框


def test_my_plan_submit_and_resubmit(client):
    csrf = _staff(client)
    today = date_plan.jst_today()
    key = date_plan.shift_period(date_plan.current_period(today), halves=1)
    days = date_plan.period_days(key)
    off = [str(days[0]), str(days[1])]
    r = client.post("/my/plan", data={
        "period": key, "csrf_token": csrf, "_ft": form_token(client, "/my/plan"),
        "unavailable": off}, follow_redirects=False)
    assert r.status_code == 303 and "saved=1" in r.headers["location"]
    db = appdb.SessionLocal()
    assert (db.query(StaffDatePlan)
            .filter(StaffDatePlan.available.is_(False)).count()) == 2
    assert (db.query(StaffDatePlan).count()) == len(days)
    db.close()
    # 再提交：只勾一天 → 另一天恢复可出勤
    r2 = client.post("/my/plan", data={
        "period": key, "csrf_token": csrf, "_ft": form_token(client, "/my/plan"),
        "unavailable": [str(days[1])]}, follow_redirects=False)
    assert r2.status_code == 303
    db = appdb.SessionLocal()
    rows = {x.plan_date: x.available for x in db.query(StaffDatePlan).all()}
    db.close()
    assert rows[days[0]] is True and rows[days[1]] is False
    assert len(rows) == len(days)


def test_my_plan_past_period_is_readonly(client):
    """已完全过去的半月：只读展示，不给保存按钮（避免点了才报错）。"""
    _staff(client)
    prev = date_plan.shift_period(
        date_plan.current_period(date_plan.jst_today()), halves=-1)
    r = client.get("/my/plan?period=" + prev)
    assert r.status_code == 200
    assert 'data-testid="plan-locked"' in r.text
    assert 'data-testid="save-plan"' not in r.text
    assert 'name="unavailable"' not in r.text        # 没有任何可勾选的日期


def test_my_plan_post_past_period_reports_all_locked(client):
    """整期已锁 → 报错提示，不写任何数据。"""
    csrf = _staff(client)
    prev = date_plan.shift_period(
        date_plan.current_period(date_plan.jst_today()), halves=-1)
    r = client.post("/my/plan", data={
        "period": prev, "csrf_token": csrf, "_ft": form_token(client, "/my/plan")},
        follow_redirects=False)
    assert r.status_code == 303 and "err=" in r.headers["location"]
    db = appdb.SessionLocal()
    assert db.query(StaffDatePlan).count() == 0
    db.close()


def test_my_plan_form_token_is_single_use(client):
    """同一令牌重复提交被拒（全站防重复提交机制）。"""
    csrf = _staff(client)
    today = date_plan.jst_today()
    key = date_plan.shift_period(date_plan.current_period(today), halves=1)
    ft = form_token(client, "/my/plan")
    data = {"period": key, "csrf_token": csrf, "_ft": ft}
    assert client.post("/my/plan", data=data,
                       follow_redirects=False).status_code == 303
    r = client.post("/my/plan", data=data, follow_redirects=False)
    assert r.status_code == 400


def test_my_plan_rejects_other_period_dates(client):
    """表单里塞别半月/别月份的日期 → 报错而不是静默写坏。"""
    csrf = _staff(client)
    today = date_plan.jst_today()
    key = date_plan.current_period(today)
    r = client.post("/my/plan", data={
        "period": key, "csrf_token": csrf, "_ft": form_token(client, "/my/plan"),
        "unavailable": ["2099-01-01"]}, follow_redirects=False)
    assert r.status_code == 303 and "err=" in r.headers["location"]
    db = appdb.SessionLocal()
    assert db.query(StaffDatePlan).count() == 0
    db.close()


# ---------------- 路由：管理端 ----------------

def test_staff_blocked_from_admin_plan_page(client):
    _staff(client)
    r = client.get("/staff-plans", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith(("/my/perf", "/login"))


def test_admin_plan_other_period_does_not_show_zero_for_today(client):
    """看非本期时，"今天可出勤人数"显示 0 会误导（今天是别的半月）→ 显示 —。"""
    _admin(client)
    nxt = date_plan.shift_period(
        date_plan.current_period(date_plan.jst_today()), halves=1)
    r = client.get("/staff-plans?period=" + nxt)
    assert r.status_code == 200
    assert "今天不在本期" in r.text


def test_admin_plan_matrix_page(client):
    _admin(client)
    db = appdb.SessionLocal()
    _seed_matrix(db)
    key = "2026-10-H2"
    date_plan.save_plan(db, _U("P1"), key, ["2026-10-20"], today=TODAY)
    db.close()
    r = client.get("/staff-plans?period=" + key)
    assert r.status_code == 200
    assert "甲" in r.text and "乙" in r.text
    assert 'data-testid="plan-matrix"' in r.text
    assert "未提交计划：" in r.text                    # 乙没登记
    assert "可出勤人数（今天及以后）" in r.text
    # 乙整行都是"未登记"
    assert r.text.count("未登记") >= 3


def test_admin_plan_page_defaults_to_current_period(client):
    _admin(client)
    r = client.get("/staff-plans")
    assert r.status_code == 200
    assert date_plan.current_period(date_plan.jst_today())[:7] in r.text


def test_admin_plan_export_xlsx(client):
    _admin(client)
    import io

    from openpyxl import load_workbook

    db = appdb.SessionLocal()
    _seed_matrix(db)
    date_plan.save_plan(db, _U("P1"), "2026-10-H2", ["2026-10-20"], today=TODAY)
    db.close()
    r = client.get("/staff-plans/export?period=2026-10-H2")
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]
    wb = load_workbook(io.BytesIO(r.content))
    ws = wb.active
    head = [c.value for c in ws[1]]
    assert head[:3] == ["员工编号", "姓名", "登记"]
    assert head[3:] == ["10-16", "10-17", "10-18", "10-19", "10-20", "10-21",
                        "10-22", "10-23", "10-24", "10-25", "10-26", "10-27",
                        "10-28", "10-29", "10-30", "10-31"]
    grid = [[c.value for c in row] for row in ws.iter_rows(min_row=2)]
    by_code = {row[0]: row for row in grid if row[0] in ("P1", "P2")}
    assert by_code["P1"][2] == "已登记" and by_code["P2"][2] == "未登记"
    assert by_code["P1"][head.index("10-20")] == "×"
    assert by_code["P1"][head.index("10-16")] == "○"
    assert by_code["P2"][head.index("10-16")] == "–"
    assert any(str(row[0]).startswith("可出勤人数") for row in grid)
