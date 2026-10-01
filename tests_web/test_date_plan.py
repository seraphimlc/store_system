# -*- coding: utf-8 -*-
"""日期计划（半月出勤登记）测试：规格 `docs/specs-date-plan.md`。

口径（与用户确认）：
- 自然半月 1–15 / 16–月末；**填报窗口 = 期首前 7 天 ~ 登记截止日**（3 号 / 18 号）；
- 默认每天都出勤，只把"不出勤"的日子落库；
- 三态：○ 可出勤 / × 不出勤 / □ 已出勤（已自报；**已自报覆盖计划**）；
- 锁定：窗口没开/已关不能改；窗口内已过去、已自报的日期也不可改；
- 窗口关了还没登记 → 今天及以后按"默认全部出勤"（浅色 ○）。
"""
from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

import app.db as appdb
from app.auth import SESSION_COOKIE, hash_password, read_session_token
from app.models import Person, StaffDailyReport, StaffDatePlan, User
from app.services import date_plan
from tests.helpers import form_token

TODAY = date(2026, 10, 10)      # H2 窗口内（10-09 ~ 10-18）
FROZEN = date(2026, 9, 25)      # 2026-10-H1 的窗口开放首日（其 15 天全在未来）


@pytest.fixture
def frozen(monkeypatch):
    """把"日期计划的今天"固定成 2026-09-25，让落点/弹窗/提交测试与真实运行日期无关。"""
    monkeypatch.setattr(date_plan, "jst_today", lambda: FROZEN)
    return FROZEN


# ---------------- 工具 ----------------

def _person(db, code="P1", name="甲"):
    if db.get(Person, code) is None:
        db.add(Person(code=code, display_name=name))
        db.commit()
    return code


def _mk_staff(client, username="emp1", code="P1", name="甲", password="pw123456"):
    """只建账号（不登录）——用来观察登录落点。"""
    db = appdb.SessionLocal()
    _person(db, code, name)
    if db.query(User).filter(User.username == username).first() is None:
        db.add(User(username=username, display_name=name, role="staff",
                    person_code=code, password_hash=hash_password(password),
                    is_active=True, status="active", must_change_password=False))
        db.commit()
    db.close()


def _login_raw(client, username="emp1", password="pw123456"):
    """登录并返回 (响应, csrf)——响应里能看落点 location。"""
    r = client.post("/login", data={"username": username, "password": password},
                    follow_redirects=False)
    return r, read_session_token(client.cookies.get(SESSION_COOKIE))["csrf"]


def _staff(client, username="emp1", code="P1", name="甲", password="pw123456"):
    _mk_staff(client, username, code, name, password)
    return _login_raw(client, username, password)[1]


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


def test_assumed_default_rules():
    """超过登记截止日 + 今天及以后 → 未登记按"默认全部出勤"。"""
    dl, today = date(2026, 10, 18), date(2026, 10, 20)
    assert date_plan.assumed_default(today, today, dl) is True
    assert date_plan.assumed_default(date(2026, 10, 31), today, dl) is True
    assert date_plan.assumed_default(date(2026, 10, 19), today, dl) is False  # 已过去不追认
    # 截止日当天仍算按时 → 不启用默认
    assert date_plan.assumed_default(dl, dl, dl) is False
    assert date_plan.assumed_default(date(2026, 10, 19), date(2026, 10, 17), dl) is False


def test_cell_state_assumed_priority():
    """默认出勤的优先级最低：已自报 > 已登记的计划 > 默认。"""
    assert date_plan.cell_state(None, False, assumed=True) == date_plan.STATE_ON
    assert date_plan.cell_state(None, False) == date_plan.STATE_NONE
    assert date_plan.cell_state(False, False, assumed=True) == date_plan.STATE_OFF
    assert date_plan.cell_state(None, True, assumed=True) == date_plan.STATE_DONE


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
    """窗口内已过去的日期不动：既不新增默认行，也不被后续提交重置。"""
    db = appdb.SessionLocal()
    _person(db)
    # 10-02 在 H1 窗口（09-25 ~ 10-03）内 → 只有 10-01 已过去
    date_plan.save_plan(db, _U(), "2026-10-H1", ["2026-10-02", "2026-10-12"],
                        today=date(2026, 10, 2))
    rows = {x.plan_date: x.available for x in db.query(StaffDatePlan).all()}
    assert min(rows) == date(2026, 10, 2)           # 10-01 没有行
    assert len(rows) == 14                          # 02–15
    assert rows[date(2026, 10, 2)] is False and rows[date(2026, 10, 12)] is False
    # 第二天（10-03，窗口最后一天）再提交：10-02 已过去 → 不进表单、不被重置
    r = date_plan.save_plan(db, _U(), "2026-10-H1", ["2026-10-03"],
                            today=date(2026, 10, 3))
    assert r["skipped"] == 2 and r["written"] == 13
    rows2 = {x.plan_date: x.available for x in db.query(StaffDatePlan).all()}
    assert rows2[date(2026, 10, 2)] is False        # 锁定的过去日期保持原值
    assert rows2[date(2026, 10, 3)] is False        # 今天：本次勾选生效
    assert rows2[date(2026, 10, 12)] is True        # 未来：取消勾选 → 恢复可出勤
    assert len(rows2) == 14                         # 10-01 仍然没有行


def _report(db, code, d, mark=True):
    """模拟"员工自报"：写一条每日填报 + **写透到计划表**（真实链路见 daily_report）。"""
    db.add(StaffDailyReport(person_code=code, report_date=d,
                            p1_cnt=1, p2_cnt=0, total_cnt=1))
    db.commit()
    if mark:
        date_plan.mark_reported(db, code, d, True)


def test_mark_reported_writes_through(client):
    """自报写透：有计划行就改 reported（保留 available），没行就插一行 source=report。"""
    db = appdb.SessionLocal()
    _person(db)
    # 先登记计划（10-12 标为不出勤）
    date_plan.save_plan(db, _U(), "2026-10-H1", ["2026-10-12"],
                        today=date(2026, 10, 2))
    assert date_plan.mark_reported(db, "P1", date(2026, 10, 12)) is True
    row = db.query(StaffDatePlan).filter(
        StaffDatePlan.plan_date == date(2026, 10, 12)).one()
    assert row.reported is True and row.available is False   # 计划值保留
    assert row.source == date_plan.SOURCE_WEB                # 仍是登记行
    # 没登记的日子（10-01 在计划里被跳过）→ 自报插一行，且**不算已登记**
    assert date_plan.mark_reported(db, "P1", date(2026, 10, 1)) is True
    r1 = db.query(StaffDatePlan).filter(
        StaffDatePlan.plan_date == date(2026, 10, 1)).one()
    assert r1.source == date_plan.SOURCE_REPORT and r1.reported is True
    # 删除自报 → reported 撤回
    assert date_plan.mark_reported(db, "P1", date(2026, 10, 12), False) is True
    assert db.query(StaffDatePlan).filter(
        StaffDatePlan.plan_date == date(2026, 10, 12)).one().reported is False
    db.close()


def test_resync_repairs_reports_missing_plan_rows(client):
    """**线上真事故回归**（2026-10-01）：写透上线前提交的自报没有计划行 → 矩阵看不见，
    跑一次对齐（脚本 `scripts/resync_plan_reported.py` 的逻辑）后必须显示 □。"""
    db = appdb.SessionLocal()
    _seed_matrix(db)
    # 老数据：只有自报表，没有计划行（写透之前的形态）
    db.add(StaffDailyReport(person_code="P1", report_date=date(2026, 10, 16),
                            p1_cnt=2, p2_cnt=1, total_cnt=3))
    db.commit()
    m = date_plan.admin_matrix(db, "2026-10-H2", today=date(2026, 10, 10))
    rows = {r["person_code"]: r for r in m["rows"]}
    assert rows["P1"]["states"][date(2026, 10, 16)] == date_plan.STATE_NONE  # 看不见
    # 对齐：按自报表把计划表补/改回来
    for code, d in db.query(StaffDailyReport.person_code,
                            StaffDailyReport.report_date).all():
        date_plan.mark_reported(db, code, d, True)
    m2 = date_plan.admin_matrix(db, "2026-10-H2", today=date(2026, 10, 10))
    rows2 = {r["person_code"]: r for r in m2["rows"]}
    assert rows2["P1"]["states"][date(2026, 10, 16)] == date_plan.STATE_DONE
    assert rows2["P1"]["submitted"] is False        # 只是补了自报，不算登记过计划
    db.close()


def test_rebuild_reported_clears_stale_flags(client):
    """反向清理：计划行标了"已自报"但自报表里没有 → `rebuild_reported` 撤掉。"""
    db = appdb.SessionLocal()
    _person(db)
    date_plan.mark_reported(db, "P1", date(2026, 10, 20), True)
    db.add(StaffDailyReport(person_code="P1", report_date=date(2026, 10, 20),
                            p1_cnt=1, p2_cnt=0, total_cnt=1))
    db.commit()
    db.delete(db.query(StaffDailyReport).one())     # 自报被删（如清理脚本）
    db.commit()
    assert date_plan.rebuild_reported(db, person_code="P1") == 1
    assert db.query(StaffDatePlan).one().reported is False
    db.close()


def test_is_submitted_ignores_report_only_rows(client):
    """只自报没登记计划的人 → **不算已登记**（`source='report'` 的行不算）。"""
    db = appdb.SessionLocal()
    _person(db)
    date_plan.mark_reported(db, "P1", date(2026, 10, 20))      # 只自报
    assert date_plan.is_submitted(db, "P1", "2026-10-H2") is False
    assert date_plan.needs_plan(db, "P1", today=date(2026, 10, 10))["key"] == \
        "2026-10-H2"
    db.close()


def test_admin_matrix_reads_only_plan_table(client):
    """**设计守门**：矩阵只读 `staff_date_plans`；没写透的自报**不会**显示方框。

    这是用户 2026-10-01 明确的口径（"避免关联查询"、"自报了就直接改计划表里的状态"）：
    写路径（`daily_report` 的 4 个入口）负责把自报写透，渲染路径不做 join。
    """
    db = appdb.SessionLocal()
    _seed_matrix(db)
    db.add(StaffDailyReport(person_code="P1", report_date=date(2026, 10, 16),
                            p1_cnt=1, p2_cnt=0, total_cnt=1))
    db.commit()                                   # 只写自报表、不写透
    m = date_plan.admin_matrix(db, "2026-10-H2", today=date(2026, 10, 10))
    rows = {r["person_code"]: r for r in m["rows"]}
    assert rows["P1"]["states"][date(2026, 10, 16)] == date_plan.STATE_NONE


def test_save_plan_keeps_reported_days_untouched(client):
    """已有自报的日子：计划值不被覆盖（自报是事实，计划只是预报）。"""
    db = appdb.SessionLocal()
    _person(db)
    _report(db, "P1", date(2026, 10, 12))
    date_plan.save_plan(db, _U(), "2026-10-H1", ["2026-10-12"],
                        today=date(2026, 10, 2))
    row = db.query(StaffDatePlan).filter(
        StaffDatePlan.plan_date == date(2026, 10, 12)).one()
    assert row.source == date_plan.SOURCE_REPORT     # 只自报的行没被登记覆盖
    assert row.reported is True
    view = date_plan.plan_days(db, "P1", "2026-10-H1", today=date(2026, 10, 2))
    day = [d for d in view["days"] if d["date"] == date(2026, 10, 12)][0]
    assert day["state"] == date_plan.STATE_DONE and day["mark"] == "□"
    assert day["editable"] is False and day["lock"] == date_plan.LOCK_REPORTED


def test_save_plan_all_locked_raises(client):
    """窗口开着但整期都锁定（已过去/已自报）→ 报错，且不留下半条数据。"""
    db = appdb.SessionLocal()
    _person(db)
    for day in date_plan.period_days("2026-10-H1"):
        _report(db, "P1", day)
    with pytest.raises(date_plan.AllLocked):
        date_plan.save_plan(db, _U(), "2026-10-H1", [], today=date(2026, 10, 2))
    assert db.query(StaffDatePlan).filter(
        StaffDatePlan.source == date_plan.SOURCE_WEB).count() == 0


def test_save_plan_rejects_outside_window(client):
    """窗口没开或已关 → WindowClosed（用户口径：提前 7 天开放，到 3/18 号为止）。"""
    db = appdb.SessionLocal()
    _person(db)
    # H1 窗口 = 期首前 7 天 ~ 截止日 = 09-24 ~ 10-03
    assert date_plan.period_window("2026-10-H1") == (
        date(2026, 9, 24), date(2026, 10, 3))
    assert date_plan.period_window("2026-10-H2") == (
        date(2026, 10, 9), date(2026, 10, 18))
    for day in (date(2026, 9, 24), date(2026, 10, 3)):      # 首尾当天都算开放
        assert date_plan.is_window_open("2026-10-H1", day) is True
    for day, state in ((date(2026, 10, 4), "closed"),
                       (date(2026, 9, 23), "before")):
        with pytest.raises(date_plan.WindowClosed) as e:
            date_plan.save_plan(db, _U(), "2026-10-H1", [], today=day)
        assert e.value.state == state
    with pytest.raises(date_plan.WindowClosed):        # H2 窗口 10-09 才开
        date_plan.save_plan(db, _U(), "2026-10-H2", [], today=date(2026, 10, 8))
    assert db.query(StaffDatePlan).count() == 0


def test_window_open_period_and_next_window():
    """同一时刻最多一期可填；4–8 号 / 19–24 号是"没有可填报期"的间隙。"""
    assert date_plan.open_period(date(2026, 10, 1)) == "2026-10-H1"      # 本期窗口
    assert date_plan.default_period(date(2026, 10, 1)) == "2026-10-H1"
    # 10-05：H1 已关、H2 未开 → 没有可填的期，默认展示本期；提示下个窗口
    assert date_plan.open_period(date(2026, 10, 5)) == ""
    assert date_plan.default_period(date(2026, 10, 5)) == "2026-10-H1"
    assert date_plan.next_window(date(2026, 10, 5))["open_at"] == date(2026, 10, 9)
    # 10-10：H2 窗口开着 → 管理员默认也看这一期（"未来两周"）
    assert date_plan.open_period(date(2026, 10, 10)) == "2026-10-H2"
    assert date_plan.default_period(date(2026, 10, 10)) == "2026-10-H2"
    assert date_plan.next_window(date(2026, 10, 10)) is None
    # 10-25：H2 已关、下月 H1 开着（月中就把下个月排好）
    assert date_plan.open_period(date(2026, 10, 25)) == "2026-11-H1"
    assert date_plan.default_period(date(2026, 10, 25)) == "2026-11-H1"
    # 10-20：间隙（H2 关了、11-H1 还没开）
    assert date_plan.open_period(date(2026, 10, 20)) == ""
    assert date_plan.next_window(date(2026, 10, 20))["key"] == "2026-11-H1"


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
    """未登记的可改日期按"默认每天都出勤"显示 ○；**过去的日期看事实**（没自报 = ×）。"""
    db = appdb.SessionLocal()
    _person(db)
    today = date(2026, 10, 2)                  # H1 窗口内（09-24~10-03）
    view = date_plan.plan_days(db, "P1", "2026-10-H1", today=today)
    assert view["submitted"] is False and view["overdue"] is False
    past, future = view["days"][0], view["days"][1]
    assert past["date"] == date(2026, 10, 1) and past["editable"] is False
    # 10-01 已过去且没自报 → ×（用户口径："没自报显示叉"）
    assert past["state"] == date_plan.STATE_OFF and past["mark"] == "×"
    assert past["past"] is True
    assert future["date"] == date(2026, 10, 2) and future["editable"] is True
    assert future["state"] == date_plan.STATE_NONE and future["mark"] == "○"
    assert view["editable_cnt"] == 14          # 2–15
    assert view["free_cnt"] == 15
    assert view["overdue"] is False and view["window_state"] == "open"
    # 窗口内最后一天（10-03）仍可改：只剩 03–15 可编辑
    last = date_plan.plan_days(db, "P1", "2026-10-H1", today=date(2026, 10, 3))
    assert last["window_state"] == "open" and last["editable_cnt"] == 13
    # 窗口一关（10-04）→ 一律不可改
    closed = date_plan.plan_days(db, "P1", "2026-10-H1", today=date(2026, 10, 4))
    assert closed["window_state"] == "closed" and closed["editable_cnt"] == 0


def test_plan_days_past_shows_fact(client):
    """员工端同口径：过去有自报 → □，没自报 → ×（哪怕计划里写的是可出勤）。"""
    db = appdb.SessionLocal()
    _person(db)
    date_plan.save_plan(db, _U(), "2026-10-H1", ["2026-10-03"],
                        today=date(2026, 10, 2))
    _report(db, "P1", date(2026, 10, 2))            # 10-02 自报了
    view = date_plan.plan_days(db, "P1", "2026-10-H1", today=date(2026, 10, 4))
    by = {d["date"]: d for d in view["days"]}
    assert by[date(2026, 10, 1)]["state"] == date_plan.STATE_OFF    # 计划可出勤，但没自报
    assert by[date(2026, 10, 2)]["state"] == date_plan.STATE_DONE   # 自报了 → □
    assert by[date(2026, 10, 3)]["state"] == date_plan.STATE_OFF    # 计划不出勤
    assert by[date(2026, 10, 4)]["state"] == date_plan.STATE_ON     # 今天之后照计划


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


def test_short_code_helper():
    """编号短展示 = 后 5 位（用户 2026-10-01："员工编号取后5位就行"）。"""
    assert date_plan.short_code("2188240627782155") == "82155"
    assert date_plan.short_code(" 2188240627782155 ") == "82155"
    assert date_plan.short_code("1234") == "1234"      # 不到 5 位 → 原样，不补零
    assert date_plan.short_code("") == "" and date_plan.short_code(None) == ""


def test_admin_matrix_page_shows_short_code(client, monkeypatch):
    """矩阵里显示后 5 位，完整编号只留在 title（鼠标悬停可查）。"""
    _admin(client)
    monkeypatch.setattr(date_plan, "jst_today", lambda: date(2026, 10, 1))
    db = appdb.SessionLocal()
    _person(db, "2188240627782155", "小川逸")
    db.add(User(username="u82155", display_name="小川逸", role="staff",
                person_code="2188240627782155", password_hash="x",
                is_active=True, status="active"))
    db.commit()
    db.close()
    r = client.get("/staff-plans?period=2026-10-H1")
    assert "（82155）" in r.text
    assert 'title="2188240627782155"' in r.text           # 完整编号在 tooltip 里
    assert "2188240627782155）" not in r.text              # 正文不再出现整串


def test_admin_matrix_states_counts_and_unsubmitted(client):
    db = appdb.SessionLocal()
    _seed_matrix(db)
    # 甲登记了（20 号不出勤），乙没登记
    date_plan.save_plan(db, _U("P1"), "2026-10-H2", ["2026-10-20"], today=TODAY)
    # 甲 16 号已自报 → 方框（覆盖计划）；自报写透进计划表（真实链路见 daily_report）
    _report(db, "P1", date(2026, 10, 16))
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


def test_admin_matrix_default_all_after_deadline(client):
    """超过 18 号仍未登记 → **今天及以后**按"默认全部出勤"显示（过去的日子不追认）。"""
    db = appdb.SessionLocal()
    _seed_matrix(db)                       # P1/P2 都没登记
    before = date_plan.admin_matrix(db, "2026-10-H2", today=date(2026, 10, 16))
    assert before["default_all"] is False
    assert {r["states"][date(2026, 10, 16)] for r in before["rows"]} == {
        date_plan.STATE_NONE}               # 没到截止日 → 未登记还是"–"
    m = date_plan.admin_matrix(db, "2026-10-H2", today=date(2026, 10, 20))
    rows = {r["person_code"]: r for r in m["rows"]}
    d_past, d_today, d_future = (date(2026, 10, 19), date(2026, 10, 20),
                                 date(2026, 10, 25))
    assert m["default_all"] is True
    assert rows["P1"]["states"][d_past] == date_plan.STATE_OFF    # 过去看事实：没自报＝×
    assert rows["P1"]["states"][d_today] == date_plan.STATE_ON    # 今天起默认可出勤
    assert rows["P1"]["states"][d_future] == date_plan.STATE_ON
    assert rows["P1"]["assumed"] is True and rows["P1"]["submitted"] is False
    assert m["free_cnt"][d_future] == 2 and m["default_cnt"][d_future] == 2
    assert m["none_cnt"][d_past] == 0          # 过去的格子已经没有"未登记"这一态
    assert sorted(m["assumed_people"]) == ["P1", "P2"]


def test_admin_matrix_default_does_not_override_submitted_plan(client):
    """登记过的人不受"默认出勤"影响：他标的不出勤仍是不出勤。"""
    db = appdb.SessionLocal()
    _seed_matrix(db)
    date_plan.save_plan(db, _U("P1"), "2026-10-H2", ["2026-10-20"],
                        today=date(2026, 10, 17))
    m = date_plan.admin_matrix(db, "2026-10-H2", today=date(2026, 10, 20))
    rows = {r["person_code"]: r for r in m["rows"]}
    assert rows["P1"]["states"][date(2026, 10, 20)] == date_plan.STATE_OFF
    assert rows["P1"]["states"][date(2026, 10, 25)] == date_plan.STATE_ON
    assert rows["P1"]["assumed"] is False and rows["P1"]["assumed_cnt"] == 0
    assert rows["P2"]["assumed"] is True                 # 没登记的按默认
    assert m["free_cnt"][date(2026, 10, 20)] == 1        # 只有 P2（默认）


def test_admin_matrix_excludes_past_days_from_free_count(client):
    """过去的日子不构成可用人力（小计置 0），且**看事实**：没自报＝×。"""
    db = appdb.SessionLocal()
    _seed_matrix(db)
    date_plan.save_plan(db, _U("P1"), "2026-10-H2", [], today=date(2026, 10, 18))
    m = date_plan.admin_matrix(db, "2026-10-H2", today=date(2026, 10, 22))
    rows = {r["person_code"]: r for r in m["rows"]}
    past = date(2026, 10, 20)
    assert m["past"][past] is True
    assert m["free_cnt"][past] == 0
    assert rows["P1"]["states"][past] == date_plan.STATE_OFF   # 计划可出勤但那天没自报 → ×
    # 未来：P1 登记过（○）+ P2 已过截止日未登记（按默认出勤 ○）= 2
    future = date(2026, 10, 25)
    assert m["past"][future] is False and m["free_cnt"][future] == 2
    assert m["default_cnt"][future] == 1
    assert rows["P2"]["assumed"] is True


def test_admin_matrix_past_day_with_report_is_done(client):
    """用户例子（今天 10-04）：10-03 计划出勤但没自报 → ×；有自报 → □。"""
    db = appdb.SessionLocal()
    _seed_matrix(db)
    # 甲整期登记（默认全可出勤）；乙没登记计划，只在 10-02 自报过
    date_plan.save_plan(db, _U("P1"), "2026-10-H1", [], today=date(2026, 9, 30))
    _report(db, "P1", date(2026, 10, 2))
    _report(db, "P2", date(2026, 10, 2))
    m = date_plan.admin_matrix(db, "2026-10-H1", today=date(2026, 10, 4))
    rows = {r["person_code"]: r for r in m["rows"]}
    d1, d2, d3 = date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3)
    assert rows["P1"]["states"][d1] == date_plan.STATE_OFF      # 计划出勤 + 没自报 → ×
    assert rows["P1"]["states"][d2] == date_plan.STATE_DONE     # 自报了 → □
    assert rows["P1"]["states"][d3] == date_plan.STATE_OFF      # 计划出勤 + 没自报 → ×
    assert rows["P2"]["states"][d1] == date_plan.STATE_OFF      # 没登记 + 没自报 → ×
    assert rows["P2"]["states"][d2] == date_plan.STATE_DONE     # 只自报也显示 □
    assert rows["P2"]["submitted"] is False                     # 但不算"登记过计划"
    assert all(m["past"][d] for d in (d1, d2, d3))
    assert m["free_cnt"][d2] == 0                               # 过去的日期不计入可用人力
    # 两个统计行（2026-10-01 用户要求）：计划出勤按计划值算（甲登记过 → 10-01/10-02 都是 1）
    assert m["plan_cnt"][d1] == 1 and m["plan_cnt"][d3] == 1
    # 实际出勤：10-02 两人都自报过 → 2；今天(10-04) 0；未来(10-05) 还没到 → None
    assert m["actual_cnt"][d2] == 2
    assert m["actual_cnt"][date(2026, 10, 4)] == 0
    assert m["actual_cnt"][date(2026, 10, 5)] is None


def test_today_keeps_plan_until_next_day(client):
    """口径（2026-10-01 用户确认）：**今天**显示计划值（当天还没过完，自报多半傍晚才交），
    次日 0 点后才按事实（有自报 □ / 没自报 ×）。"""
    db = appdb.SessionLocal()
    _seed_matrix(db)
    date_plan.save_plan(db, _U("P1"), "2026-10-H1", [], today=date(2026, 10, 1))
    # 站在 10-01 当天看：10-01 还是计划 ○（哪怕还没自报）
    today_view = date_plan.admin_matrix(db, "2026-10-H1", today=date(2026, 10, 1))
    r = {x["person_code"]: x for x in today_view["rows"]}["P1"]
    assert r["states"][date(2026, 10, 1)] == date_plan.STATE_ON
    # 站在 10-02 看：10-01 变成事实 —— 没自报 → ×；10-02 仍看计划 ○
    next_view = date_plan.admin_matrix(db, "2026-10-H1", today=date(2026, 10, 2))
    r2 = {x["person_code"]: x for x in next_view["rows"]}["P1"]
    assert r2["states"][date(2026, 10, 1)] == date_plan.STATE_OFF
    assert r2["states"][date(2026, 10, 2)] == date_plan.STATE_ON
    assert next_view["past"][date(2026, 10, 1)] is True
    db.close()


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
    date_plan.mark_reported(db, "P1", date.today())      # 自报写透（真实链路）
    db.close()
    r = client.get("/my/plan")
    assert r.status_code == 200
    assert "出勤计划" in r.text
    assert "默认每天都出勤，点一下那天就变成不出勤。" in r.text   # 一行摘要里的提示
    assert "已出勤（已自报）" in r.text
    # 不再在"今天"那行打标记（2026-10-01 用户要求）
    assert '<span class="pill run">' not in r.text
    assert 'data-testid="plan-form"' in r.text
    assert 'name="unavailable"' in r.text               # 可改日期有勾选框
    assert r.text.count("pm done") >= 1                 # 今天已自报 → 方框


def test_my_plan_submit_and_resubmit(client, frozen):
    """窗口内提交 → 覆盖式再提交（frozen = 2026-09-25，正是 10 月上半月的窗口期）。"""
    csrf = _staff(client)
    key = date_plan.open_period(frozen)
    assert key == "2026-10-H1"
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


# ---------------- 待填报判定 / 登录落点 / 弹窗提示（2026-10-01 用户要求） ----------------

def _submit_current(client, csrf):
    """登记**当前正在填报的那一期**（空勾选 = 全部可出勤）。"""
    key = date_plan.open_period(date_plan.jst_today())
    assert key, "当前没有开放中的填报窗口"
    r = client.post("/my/plan", data={
        "period": key, "csrf_token": csrf, "_ft": form_token(client, "/my/plan")},
        follow_redirects=False)
    assert r.status_code == 303 and "saved=1" in r.headers["location"]
    return key


def test_needs_plan_and_staff_home(client):
    """窗口开着且未登记 = 需要填报；落点随之切换。"""
    db = appdb.SessionLocal()
    _person(db)
    u = _U()
    today = date(2026, 10, 2)
    info = date_plan.needs_plan(db, "P1", today=today)
    assert info["key"] == "2026-10-H1" and info["overdue"] is False
    assert info["close_at"] == date(2026, 10, 3)            # 窗口到截止日为止
    assert date_plan.staff_home(db, u) == "/my/plan"        # 未登记 → 先填计划
    date_plan.save_plan(db, u, "2026-10-H1", [], today=today)
    assert date_plan.needs_plan(db, "P1", today=today) == {}
    assert date_plan.staff_home(db, u) == "/my/report"      # 已登记 → 每日自报


def test_needs_plan_edge_cases(client):
    db = appdb.SessionLocal()
    _person(db)
    # 没绑定编号：既不提示也回不到这两个页面 → 保持历史落点
    assert date_plan.needs_plan(db, None) == {}
    assert date_plan.needs_plan(db, "") == {}
    assert date_plan.staff_home(db, _U(code=None)) == "/my/perf"
    # 窗口开着、没登记 → 需要填报
    assert date_plan.needs_plan(db, "P1", today=date(2026, 10, 10))["key"] == "2026-10-H2"
    # 窗口间隙（10-05 / 10-20 既没有开着的期）→ 不需要填报，也就不会弹窗催办
    assert date_plan.needs_plan(db, "P1", today=date(2026, 10, 5)) == {}
    assert date_plan.needs_plan(db, "P1", today=date(2026, 10, 20)) == {}
    db.close()


def test_staff_login_landing_plan_then_report(client, frozen):
    """登录后第一个页面：待填报 → /my/plan；登记完 → /my/report。"""
    _mk_staff(client)
    r, csrf = _login_raw(client)
    assert r.headers["location"] == "/my/plan"
    _submit_current(client, csrf)
    client.post("/logout", data={"csrf_token": csrf}, follow_redirects=False)
    r2, _ = _login_raw(client)
    assert r2.headers["location"] == "/my/report"


def test_staff_login_lands_on_report_when_no_window(client, frozen, monkeypatch):
    """窗口间隙（没有可填报的期）→ 登录直接进每日自报页。"""
    monkeypatch.setattr(date_plan, "jst_today", lambda: date(2026, 10, 20))
    _mk_staff(client)
    r, _ = _login_raw(client)
    assert r.headers["location"] == "/my/report"


def test_staff_root_redirect_uses_staff_home(client, frozen):
    """访问 / 也走同一落点规则（管理员仍是数据看板）。"""
    _mk_staff(client)
    _login_raw(client)
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/my/plan"


def test_plan_prompt_popup_shown_until_submitted(client):
    """待填报 → 员工端弹窗 + tab 角标；正在填的计划页不弹；登记后消失。"""
    csrf = _staff(client)
    r = client.get("/my/report")
    assert 'data-testid="plan-prompt"' in r.text
    assert 'data-testid="go-plan"' in r.text and 'data-testid="plan-later"' in r.text
    assert 'class="dot"' in r.text                      # tab 角标
    assert 'data-testid="plan-prompt"' not in client.get("/my/plan").text
    _submit_current(client, csrf)
    after = client.get("/my/report")
    assert 'data-testid="plan-prompt"' not in after.text
    assert 'class="dot"' not in after.text


def test_plan_prompt_not_shown_to_admin(client):
    _admin(client)
    r = client.get("/staff-plans")
    assert r.status_code == 200
    assert 'data-testid="plan-prompt"' not in r.text


def test_my_plan_rows_toggle_without_js(client, frozen, monkeypatch):
    """员工端：15 天顺序表，**整行点一下就切 ○↔×**，纯 CSS（不依赖 JS）。"""
    _staff(client)
    html = client.get("/my/plan").text
    # 顺序表：一期就是一条一条往下排（不写死 15，H2 可能 16 条）
    assert html.count('class="pickrow"') == 15          # 2026-10-H1 = 15 天
    assert html.count('name="unavailable"') == 15
    # 表单区不依赖 Alpine（顶栏汉堡菜单的 x-data 在 base.html 里，与本表无关）
    form = html.split('data-testid="plan-form"')[1].split("</form>")[0]
    assert "x-model" not in form and "x-data" not in form
    # 开关组件（switch）：开=可出勤（滑块靠右）、关=不出勤（滑块靠左），纯 CSS 兄弟选择器
    assert 'class="sw"' in html and 'role="switch"' in html
    css = open("app/static/app.css", encoding="utf-8").read()
    assert ".pickrow input:checked ~ .sw-wrap .sw i { left: 3px; }" in css
    assert ".pickrow input:checked ~ .sw-wrap .sw-txt.off { display: inline; }" in css
    # 窗口内、且本期已经开始（10-02 看 10 月上半月）→ 过去的日子不再可点
    monkeypatch.setattr(date_plan, "jst_today", lambda: date(2026, 10, 2))
    html2 = client.get("/my/plan").text
    assert html2.count('name="unavailable"') == 14
    assert "未自报（按不出勤）" in html2                  # 10-01 过去且没自报 → ×
    # 自报写透后 → □（同样不可点）
    db = appdb.SessionLocal()
    date_plan.mark_reported(db, "P1", date(2026, 10, 1))
    db.close()
    html3 = client.get("/my/plan").text
    assert html3.count('name="unavailable"') == 14
    assert "已出勤（已自报）" in html3
    assert 'class="pm done"' in html3
    assert 'class="pickrow static"' in html3              # 只读行用静态样式


def test_plan_prompt_shows_window_deadline(client, frozen):
    """弹窗里给出填报窗口（到截止日为止），不再有"已逾期仍可补登记"的说法。"""
    _staff(client)
    html = client.get("/my/report").text
    assert "填报期到" in html and "2026-10-03" in html    # 10 月上半月窗口 = 09-24 ~ 10-03
    assert "已过登记截止日" not in html


def test_my_plan_past_period_is_readonly(client, frozen):
    """窗口已关的半月：只读展示，不给保存按钮（避免点了才报错）。"""
    _staff(client)
    prev = date_plan.shift_period(date_plan.current_period(frozen), halves=-1)
    r = client.get("/my/plan?period=" + prev)
    assert r.status_code == 200
    assert 'data-testid="window-closed"' in r.text        # 页面自带状态条
    assert 'data-testid="plan-locked"' in r.text
    assert 'data-testid="save-plan"' not in r.text
    assert 'name="unavailable"' not in r.text             # 没有任何可勾选的日期


def test_my_plan_post_outside_window_reports_error(client, frozen):
    """窗口已关 → 提交被拒（err），不写任何数据。"""
    csrf = _staff(client)
    prev = date_plan.shift_period(date_plan.current_period(frozen), halves=-1)
    r = client.post("/my/plan", data={
        "period": prev, "csrf_token": csrf, "_ft": form_token(client, "/my/plan")},
        follow_redirects=False)
    assert r.status_code == 303 and "err=" in r.headers["location"]
    db = appdb.SessionLocal()
    assert db.query(StaffDatePlan).count() == 0
    db.close()


def test_my_plan_shows_window_banners(client, monkeypatch):
    """三种窗口状态各有状态条：开放中 / 还没开始 / 已结束。"""
    _staff(client)
    # 1) 开放中：2026-10-01（H1 窗口 09-24 ~ 10-03 内）
    monkeypatch.setattr(date_plan, "jst_today", lambda: date(2026, 10, 1))
    r = client.get("/my/plan")
    assert 'data-testid="window-open"' in r.text and "2026-09-24" in r.text
    assert 'name="unavailable"' in r.text
    # 2) 还没开始 / 3) 已结束：10-05 是窗口间隙（H1 已关、H2 10-09 才开）
    monkeypatch.setattr(date_plan, "jst_today", lambda: date(2026, 10, 5))
    r2 = client.get("/my/plan?period=2026-10-H2")
    assert 'data-testid="window-before"' in r2.text and "2026-10-09" in r2.text
    assert 'name="unavailable"' not in r2.text
    r3 = client.get("/my/plan?period=2026-10-H1")
    assert 'data-testid="window-closed"' in r3.text and "2026-10-03" in r3.text
    assert 'name="unavailable"' not in r3.text
    # 间隙里页面提示下一个填报期
    assert 'data-testid="next-window"' in r3.text and "2026-10-09" in r3.text


def test_my_plan_form_token_is_single_use(client, frozen):
    """同一令牌重复提交被拒（全站防重复提交机制）。"""
    csrf = _staff(client)
    key = date_plan.open_period(frozen)
    ft = form_token(client, "/my/plan")
    data = {"period": key, "csrf_token": csrf, "_ft": ft}
    assert client.post("/my/plan", data=data,
                       follow_redirects=False).status_code == 303
    r = client.post("/my/plan", data=data, follow_redirects=False)
    assert r.status_code == 400


def test_my_plan_rejects_other_period_dates(client, frozen):
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
    """员工访问管理端矩阵 → 拦回**员工首页**（待填报时 = /my/plan，否则 /my/report）。"""
    _staff(client)
    r = client.get("/staff-plans", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith(("/my/plan", "/my/report",
                                             "/my/perf", "/login"))


def test_admin_plan_other_period_does_not_show_zero_for_today(client, monkeypatch):
    """看非本期时**不显示"今天可出勤人数"**（今天是别的半月，显示 0 会误导）。"""
    _admin(client)
    monkeypatch.setattr(date_plan, "jst_today", lambda: date(2026, 10, 1))
    this_view = client.get("/staff-plans").text
    assert "计划出勤" in this_view and "实际出勤" in this_view   # 本期（今天在里面）→ 显示
    other = date_plan.shift_period(
        date_plan.current_period(date(2026, 10, 1)), halves=2)
    r = client.get("/staff-plans?period=" + other)
    assert r.status_code == 200
    assert "今天 计划出勤" not in r.text                  # 非本期 → "今天"那段不显示
    # 顶部只有一条信息条（不再有统计卡片墙，也不再有那几段说明废话）
    assert 'data-testid="plan-bar"' in r.text
    assert "stat-card" not in r.text
    for gone in ("window-hint", "past-rule", "default-hint"):
        assert 'data-testid="%s"' % gone not in r.text


def test_admin_plan_matrix_page(client):
    """矩阵：行=员工、列=日期；**没有"登记"列**，也没用人名清单（2026-10-01 用户要求）。"""
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
    # 底部两行统计：计划出勤 / 实际出勤（2026-10-01 用户要求）
    assert "计划出勤" in r.text and "实际出勤" in r.text
    assert 'data-testid="cell-P1-2026-10-16"' in r.text
    # 登记状态靠表格里的标记体现：不再有登记列，也不再罗列未提交的人名
    assert 'data-testid="unsubmitted"' not in r.text
    assert "<th>登记</th>" not in r.text
    assert "未提交计划：" not in r.text
    assert "未提交计划" in r.text                     # 顶部信息条上的计数（催办用）
    # 甲登记过（虚线可出勤 + 20 号不出勤），乙没登记 → 乙整行 –
    assert 'data-testid="cell-P1-2026-10-20"' in r.text
    assert r.text.count("未登记") >= 2
    # 导出链接带 download 属性（防连点重复下载的钩子，见 base.html 的守卫）
    assert "staff-plans/export" in r.text and "download" in r.text


def test_admin_plan_page_defaults_to_open_period(client, frozen):
    """管理员默认看**正在填报的那一期**（= 未来两周），没有开放期时退回本期。"""
    _admin(client)
    r = client.get("/staff-plans")
    assert r.status_code == 200
    assert "2026-10-01" in r.text                     # frozen = 09-25 → 默认 2026-10-H1


def test_admin_plan_export_xlsx(client):
    """导出照"排班计划"参考表排版：说明行 + 标题行 + 表头 + 七曜行 + 数据；
    **没有区域标识行**、**没有登记列**。"""
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
    grid = [["" if c.value is None else c.value for c in row]
            for row in ws.iter_rows()]
    # 第 1 行：说明图例；第 2 行：期间/填报期标题；第 3 行：表头；第 4 行：七曜
    assert str(grid[0][0]).startswith("说明：○=可出勤")
    assert "区域" not in str(grid[1][0]) and "名古屋" not in str(grid[1][0])
    assert str(grid[1][0]).startswith("出勤计划 2026-10-16")
    assert grid[2][:3] == ["姓名", "员工编号", "可出动天数"]
    assert grid[2][3:] == ["10/16", "10/17", "10/18", "10/19", "10/20", "10/21",
                           "10/22", "10/23", "10/24", "10/25", "10/26", "10/27",
                           "10/28", "10/29", "10/30", "10/31"]
    # 七曜行（10-16 是周五 → 金）
    assert grid[3][3:] == ["金", "土", "日", "月", "火", "水", "木", "金", "土",
                           "日", "月", "火", "水", "木", "金", "土"]
    by_name = {row[0]: row for row in grid if row[0] in ("甲", "乙")}
    assert set(by_name) == {"甲", "乙"}
    assert by_name["甲"][1] == "P1"             # 编号短展示（P1 不到 5 位 → 原样）
    assert by_name["甲"][3 + 4] == "×"          # 10-20：自己标的不出勤
    assert by_name["甲"][3] == "○"              # 10-16：可出勤
    assert by_name["乙"][3] == ""               # 未登记 → 留空
    assert by_name["甲"][2] == 15               # 可出动天数：整期 16 天 − 1 天不出勤
    # 底部两行统计（2026-10-01 用户要求）：计划出勤 / 实际出勤
    totals = {str(row[0]): row for row in grid}
    assert "计划出勤" in totals and "实际出勤" in totals
    assert "可出勤人数（今天起）" not in totals
    # 计划出勤：甲登记了（10-16~10-19 可出勤 + 10-20 不出勤）→ 该日 1 人；乙未登记 → 不计
    assert totals["计划出勤"][3] == 1              # 10-16
    # 实际出勤：这一期全在未来（今天 10-10）→ 整行留空（那天还没到）
    assert totals["实际出勤"][3] == ""
    assert totals["实际出勤"][-1] == ""
