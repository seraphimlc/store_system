# -*- coding: utf-8 -*-
"""团队 + 车站任务测试（规格 `docs/specs-team-management.md` /
`docs/specs-station-tasks.md`）。

覆盖：团队定义（圈人/指定队长）、队长权限（落点/无重定向环/导航 gate）、
车站→任务、分派 ≤2 人、状态机（未分配/进行中/已完成）、每日进展（当天覆盖）、
管理端任务总表（分配日期区间）、队员只读、硬边界（不碰结算域）。
"""
import re
from datetime import date

import pytest

import app.db as appdb
from app.auth import hash_password
from app.db import Base
from app.models import (BdStation, BdTask, BdTaskAssign, BdTaskProgress,
                        BdTeam, BdTeamMember, Person, User)
from app.services import bd_teams, bd_tasks
from tests.helpers import form_token

ADMIN_PW = "pw123456"


# ---------------- 辅助 ----------------

def _user(db, username, role, code=None, name=None, status="active"):
    u = db.query(User).filter(User.username == username).first()
    if u is None:
        u = User(username=username, display_name=name or username, role=role,
                 is_active=True, status=status, person_code=code,
                 password_hash=hash_password(ADMIN_PW),
                 must_change_password=False)
        db.add(u)
    if code and db.get(Person, code) is None:
        db.add(Person(code=code, display_name=name or username))
    db.flush()
    return u


def _login(client, username, password=ADMIN_PW):
    return client.post("/login", data={"username": username, "password": password},
                       follow_redirects=False)


def _csrf(client, path):
    m = re.search(r'name="csrf_token" value="([^"]+)"',
                  client.get(path, follow_redirects=True).text)
    return m.group(1) if m else ""


def _post(client, path, data, from_path=None):
    """带一次性令牌 + csrf 的 POST。"""
    d = dict(data)
    d["_ft"] = form_token(client, from_path or path)
    d["csrf_token"] = _csrf(client, from_path or path)
    return client.post(path, data=d, follow_redirects=False)


@pytest.fixture
def seeded(client):
    """管理员 + 队长(小川逸) + 队员(汤静) + 一个队 + 一个车站 + 一个任务。"""
    db = appdb.SessionLocal()
    _user(db, "admin", "admin", name="管理员")
    _user(db, "ogawa", "leader", "P1", "小川逸")
    _user(db, "tangjing", "staff", "P2", "汤静")
    _user(db, "ganzijie", "staff", "P3", "甘子杰")
    team = bd_teams.create_team(db, "小川队", "TW01", by="admin")
    bd_teams.set_members(db, team.id, [("P1", "leader"), ("P2", "member")])
    st = bd_tasks.create_station(db, "駒場東大前", line="井の頭線")
    bd_tasks.create_tasks(db, [st.id], by="admin")
    task = db.query(BdTask).first()
    # 派给团队 + 分配日期（分派队员的前提；也是管理端"已分配"视图的入口）
    bd_tasks.set_task_team(db, [task.id], team.id, assign_date=date(2026, 10, 3))
    db.commit()
    ids = {"team": team.id, "station": st.id, "task": task.id}
    db.close()
    return ids


# ---------------- 团队定义 ----------------

def test_team_create_and_dup_name_rejected(seeded):
    db = appdb.SessionLocal()
    with pytest.raises(bd_teams.NameExists):
        bd_teams.create_team(db, "小川队", by="admin")
    # 编号归一 + 重号拒绝
    t2 = bd_teams.create_team(db, "汤静队", "ｔｊ 01", by="admin")
    assert t2.code == "TJ01"
    with pytest.raises(bd_teams.CodeExists):
        bd_teams.create_team(db, "别的队", "TJ01", by="admin")
    db.close()


def test_member_remove_keeps_history(seeded):
    """移出 = 写 end_date，不删行；再拉回来是新行。"""
    db = appdb.SessionLocal()
    tid = seeded["team"]
    bd_teams.set_members(db, tid, [("P1", "leader")], today=date(2026, 10, 10))
    db.commit()
    rows = db.query(BdTeamMember).filter(BdTeamMember.team_id == tid).all()
    assert len(rows) == 2, "历史行不许删"
    gone = [r for r in rows if r.person_code == "P2"][0]
    assert gone.end_date == date(2026, 10, 10)
    assert bd_teams.is_leader_of(db, "P2", tid) is False
    # 拉回来 → 新行
    bd_teams.set_members(db, tid, [("P1", "leader"), ("P2", "member")],
                         today=date(2026, 10, 20))
    db.commit()
    act = [r for r in db.query(BdTeamMember)
           .filter(BdTeamMember.team_id == tid,
                   BdTeamMember.end_date.is_(None)).all()]
    assert {r.person_code for r in act} == {"P1", "P2"}
    assert len(db.query(BdTeamMember).filter(
        BdTeamMember.team_id == tid).all()) == 3
    db.close()


def test_leader_switch_and_summary(seeded):
    db = appdb.SessionLocal()
    tid = seeded["team"]
    assert bd_teams.is_leader_of(db, "P1", tid) is True
    assert bd_teams.is_leader_of(db, "P2", tid) is False
    # 队长换人
    bd_teams.set_members(db, tid, [("P2", "leader"), ("P1", "member")])
    db.commit()
    assert bd_teams.is_leader_of(db, "P2", tid) is True
    assert bd_teams.is_leader_of(db, "P1", tid) is False
    assert [t.name for t in bd_teams.leader_teams(db, "P2")] == ["小川队"]
    s = bd_teams.summary(db)
    assert s["n_team"] == 1 and s["no_leader"] == []
    # 没有任何队长的队会被点名
    t3 = bd_teams.create_team(db, "空队", by="admin")
    db.commit()
    assert [x[0] for x in bd_teams.summary(db)["no_leader"]] == [t3.id]
    db.close()


def test_teams_page_for_admin_and_blocked_for_staff(client, seeded):
    _login(client, "admin")
    p = client.get("/teams")
    assert p.status_code == 200 and "小川队" in p.text
    assert "队长" in p.text
    # 员工被拦到员工首页
    _login(client, "tangjing")
    r = client.get("/teams", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith(("/my/plan", "/my/report",
                                            "/my/perf", "/login"))


def test_team_create_via_page(client, seeded):
    _login(client, "admin")
    r = _post(client, "/teams/create", {"name": "汤静队", "code": "TJ01"},
              from_path="/teams")
    assert r.status_code == 303
    db = appdb.SessionLocal()
    assert db.query(BdTeam).filter(BdTeam.name == "汤静队").first() is not None
    db.close()


def test_members_save_via_page(client, seeded):
    _login(client, "admin")
    tid = seeded["team"]
    r = _post(client, "/teams/%d/members" % tid,
              {"person": ["P1", "P2", "P3"], "leader": "P3"},
              from_path="/teams/%d" % tid)
    assert r.status_code == 303
    db = appdb.SessionLocal()
    assert bd_teams.is_leader_of(db, "P3", tid) is True
    assert bd_teams.is_leader_of(db, "P1", tid) is False
    db.close()


# ---------------- 队长权限（第 2 项） ----------------

def test_leader_landing_has_no_redirect_loop(client, seeded):
    """队长从 `/` 出发必须落到 /my/tasks，且不反复重定向。"""
    _login(client, "ogawa")
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/my/tasks"
    seen = []
    url = "/"
    for _ in range(6):
        r = client.get(url, follow_redirects=False)
        if r.status_code != 302:
            break
        loc = r.headers["location"]
        assert loc not in seen, "重定向成环：%s → %s" % (seen, loc)
        seen.append(loc)
        url = loc
    assert seen and seen[0] == "/my/tasks" and len(seen) <= 3


def test_leader_blocked_from_admin_pages_lands_on_tasks(client, seeded):
    _login(client, "ogawa")
    for path in ("/dashboard", "/teams", "/stations", "/tasks",
                 "/staff-admin", "/staff-plans", "/staff-reports"):
        r = client.get(path, follow_redirects=True)
        assert r.status_code == 200, path
        assert "/my/tasks" in str(r.url), "%s → %s" % (path, r.url)


def test_leader_must_change_password_first(client, seeded):
    db = appdb.SessionLocal()
    u = db.query(User).filter(User.username == "ogawa").first()
    u.must_change_password = True
    db.commit()
    db.close()
    _login(client, "ogawa")
    r = client.get("/my/tasks", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith("/my/password")


def test_leader_nav_gate_and_tabbar(client, seeded):
    """队长页面不许出现管理端链接，但要有底部导航 + 任务 tab。"""
    _login(client, "ogawa")
    html = client.get("/my/tasks").text
    for bad in ('href="/dashboard"', 'href="/files"', 'href="/perf"',
                'href="/config"', 'href="/staff-admin"'):
        assert bad not in html, "队长不该看到 %s" % bad
    assert "has-tabbar" in html
    assert 'data-testid="tab-tasks"' in html
    assert "任务分派" in html


def test_staff_login_landing_unchanged(client, seeded):
    """员工落点仍是员工首页（走了 landing_home，但结果不变）。"""
    _login(client, "tangjing")
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith(("/my/plan", "/my/report",
                                            "/my/perf"))


# ---------------- 车站 / 任务（第 5 项） ----------------

def test_station_norm_and_dup(seeded):
    db = appdb.SessionLocal()
    with pytest.raises(bd_tasks.NameExists):
        bd_tasks.create_station(db, "駒場東大前")          # 同名
    with pytest.raises(bd_tasks.NameExists):
        bd_tasks.create_station(db, " 駒場東大前 ")        # 空格归一后同名
    db.close()


def test_task_create_is_idempotent(seeded):
    db = appdb.SessionLocal()
    sid = seeded["station"]
    r = bd_tasks.create_tasks(db, [sid], by="admin")
    assert r == {"created": 0, "skipped": 1}
    assert db.query(BdTask).count() == 1
    db.close()


def test_assign_limit_two_and_must_be_member(seeded):
    db = appdb.SessionLocal()
    tid = seeded["task"]
    with pytest.raises(bd_tasks.TaskError):
        bd_tasks.assign_members(db, tid, ["P1", "P2", "P3"], by="admin")
    with pytest.raises(bd_tasks.TaskError):
        bd_tasks.assign_members(db, tid, ["P3"], by="admin")   # 非本队成员
    bd_tasks.assign_members(db, tid, ["P2"], by="admin")
    db.commit()
    assert db.query(BdTaskAssign).filter(BdTaskAssign.task_id == tid).count() == 1
    db.close()


def test_state_machine_unassigned_doing_done(seeded):
    db = appdb.SessionLocal()
    tid = seeded["task"]
    task = db.get(BdTask, tid)
    assert task.state == "unassigned" and task.pct == 0
    bd_tasks.assign_members(db, tid, ["P2"], by="admin")
    db.commit()
    assert db.get(BdTask, tid).state == "doing"              # 有人但 0%
    bd_tasks.save_progress(db, tid, 40, "开始", by="ogawa")
    db.commit()
    assert db.get(BdTask, tid).pct == 40
    assert db.get(BdTask, tid).state == "doing"
    bd_tasks.save_progress(db, tid, 100, "完成", by="ogawa")
    db.commit()
    assert db.get(BdTask, tid).state == "done"
    # 撤人 → 回到未分配
    bd_tasks.assign_members(db, tid, [], by="admin")
    db.commit()
    assert db.get(BdTask, tid).state == "unassigned"
    db.close()


def test_daily_progress_upserts_same_day(seeded):
    db = appdb.SessionLocal()
    tid = seeded["task"]
    bd_tasks.assign_members(db, tid, ["P2"], by="admin")
    bd_tasks.save_progress(db, tid, 30, "上午", by="ogawa")
    db.commit()
    row = db.query(BdTaskProgress).filter(BdTaskProgress.task_id == tid).one()
    first_updated = row.updated_at
    bd_tasks.save_progress(db, tid, 70, "下午", by="ogawa")
    db.commit()
    rows = db.query(BdTaskProgress).filter(BdTaskProgress.task_id == tid).all()
    assert len(rows) == 1, "同一天只留一条"
    assert rows[0].pct == 70 and rows[0].note == "下午"
    assert rows[0].updated_at >= first_updated
    assert db.get(BdTask, tid).pct == 70
    db.close()


def test_pct_clamped_0_100(seeded):
    db = appdb.SessionLocal()
    tid = seeded["task"]
    assert bd_tasks.save_progress(db, tid, 130, by="admin")["pct"] == 100
    assert bd_tasks.save_progress(db, tid, -5, by="admin")["pct"] == 0
    db.close()


def test_change_team_clears_assignees(seeded):
    """换队 → 原担当被清空（人不属于新队）。"""
    db = appdb.SessionLocal()
    tid = seeded["task"]
    bd_tasks.assign_members(db, tid, ["P2"], by="admin")
    db.commit()
    t2 = bd_teams.create_team(db, "汤静队", by="admin")
    db.commit()
    res = bd_tasks.set_task_team(db, [tid], t2.id, assign_date=date(2026, 10, 3))
    db.commit()
    assert res["cleared"] == 1
    assert db.query(BdTaskAssign).filter(BdTaskAssign.task_id == tid).count() == 0
    assert db.get(BdTask, tid).assign_date == date(2026, 10, 3)
    db.close()


def test_stations_page_admin(client, seeded):
    _login(client, "admin")
    p = client.get("/stations")
    assert p.status_code == 200 and "駒場東大前" in p.text
    assert 'data-testid="bulk-team-form"' in p.text


def test_bulk_make_tasks_via_page(client, seeded):
    _login(client, "admin")
    db = appdb.SessionLocal()
    s2 = bd_tasks.create_station(db, "池ノ上", line="井の頭線")
    db.commit()
    db.close()
    r = _post(client, "/stations/tasks", {"all_without": "1"},
              from_path="/stations")
    assert r.status_code == 303
    db = appdb.SessionLocal()
    assert db.query(BdTask).count() == 2
    db.close()


# ---------------- 员工端任务页（第 3 项） ----------------

def test_leader_task_page_tabs_and_assign(client, seeded):
    _login(client, "ogawa")
    p = client.get("/my/tasks")
    assert p.status_code == 200
    for tid in ('data-testid="tab-unassigned"', 'data-testid="tab-doing"',
                'data-testid="tab-done"'):
        assert tid in p.text
    assert "未分配（1）" in p.text
    # 未分配 tab 里能分派
    assert 'data-testid="assign-%d"' % seeded["task"] in p.text
    r = _post(client, "/my/tasks/assign",
              {"task_id": str(seeded["task"]), "person": "P2"},
              from_path="/my/tasks?tab=unassigned")
    assert r.status_code == 303
    db = appdb.SessionLocal()
    assert db.query(BdTaskAssign).count() == 1
    assert db.get(BdTask, seeded["task"]).state == "doing"
    db.close()




def test_leader_submit_progress_via_page(client, seeded):
    _login(client, "ogawa")
    r = _post(client, "/my/tasks/progress",
              {"task_id": str(seeded["task"]), "pct": "60", "note": "顺路"},
              from_path="/my/tasks")
    assert r.status_code == 303
    db = appdb.SessionLocal()
    assert db.get(BdTask, seeded["task"]).pct == 60
    db.close()


def test_member_is_readonly(client, seeded):
    """队员：看得到分给自己的，但不能提交进展 / 不能分派。"""
    db = appdb.SessionLocal()
    tid = seeded["task"]
    bd_tasks.assign_members(db, tid, ["P2"], by="admin")
    bd_tasks.save_progress(db, tid, 25, by="ogawa")
    db.commit()
    db.close()
    _login(client, "tangjing")
    p = client.get("/my/tasks")
    assert p.status_code == 200
    assert "駒場東大前" in p.text
    assert 'data-testid="slider-%d"' % tid not in p.text, "队员不该有滑动条"
    # 从带表单的页面取 csrf（队员的任务页是只读的、页面上没有表单）
    r = _post(client, "/my/tasks/progress", {"task_id": str(tid), "pct": "99"},
              from_path="/my/report")
    assert r.status_code == 303
    db = appdb.SessionLocal()
    assert db.get(BdTask, tid).pct == 25, "队员不许改进度"
    db.close()


def test_member_only_sees_own_tasks(client, seeded):
    db = appdb.SessionLocal()
    st2 = bd_tasks.create_station(db, "池ノ上")
    bd_tasks.create_tasks(db, [st2.id], by="admin")
    other = db.query(BdTask).filter(BdTask.station_id == st2.id).one()
    bd_tasks.set_task_team(db, [other.id], seeded["team"],
                           assign_date=date(2026, 10, 3))
    bd_tasks.assign_members(db, other.id, ["P1"], by="admin")
    db.commit()
    db.close()
    _login(client, "tangjing")
    p = client.get("/my/tasks")
    assert "池ノ上" not in p.text
    assert "当前没有分给你的车站" in p.text


def test_admin_cannot_use_staff_task_page_as_leader(client, seeded):
    """管理员进 /my/tasks 会被挡回 /dashboard（该页只服务员工与队长）。"""
    _login(client, "admin")
    r = client.get("/my/tasks", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/dashboard"


# ---------------- 管理端任务总表（第 4 项） ----------------

def test_admin_board_default_only_assigned_and_date_range(client, seeded):
    db = appdb.SessionLocal()
    st2 = bd_tasks.create_station(db, "池ノ上")
    bd_tasks.create_tasks(db, [st2.id], by="admin")            # 未派队
    db.commit()
    db.close()
    _login(client, "admin")
    p = client.get("/tasks")
    assert p.status_code == 200
    # 默认只看已分配：已派队的出现，没派队的不出现
    assert "駒場東大前" in p.text
    assert "池ノ上" not in p.text, "还没派队的任务不该出现在已分配视图"
    assert "2026-10-03" in p.text, "分配日期要显示出来"
    # 分配日期区间之外 → 查不到；区间之内 → 查得到
    assert "駒場東大前" not in client.get("/tasks?date_from=2026-10-04").text
    assert "駒場東大前" not in client.get("/tasks?date_to=2026-10-02").text
    assert "駒場東大前" in client.get(
        "/tasks?date_from=2026-10-01&date_to=2026-10-05").text
    # 按队 / 按状态筛
    assert "駒場東大前" in client.get("/tasks?team=%d" % seeded["team"]).text
    assert "駒場東大前" in client.get("/tasks?state=unassigned").text
    assert "駒場東大前" not in client.get("/tasks?state=done").text


def test_admin_fix_progress_via_page(client, seeded):
    _login(client, "admin")
    r = _post(client, "/tasks/%d/progress" % seeded["task"],
              {"pct": "80", "note": "管理员修正"}, from_path="/tasks")
    assert r.status_code == 303
    db = appdb.SessionLocal()
    row = db.query(BdTaskProgress).filter(
        BdTaskProgress.task_id == seeded["task"]).one()
    assert row.pct == 80 and row.submitted_by == "admin"
    db.close()


def test_tasks_export_xlsx(client, seeded):
    _login(client, "admin")
    r = client.get("/tasks/export")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(
        "application/vnd.openxmlformats")
    assert len(r.content) > 1000


def test_leader_cannot_post_admin_bulk_endpoints(client, seeded):
    _login(client, "ogawa")
    for path, data in (("/stations/team", {"station_id": str(seeded["station"]),
                                          "team_id": str(seeded["team"])}),
                       ("/stations/tasks", {"all_without": "1"}),
                       ("/stations/create", {"name": "X"}),
                       ("/tasks/%d/progress" % seeded["task"], {"pct": "50"}),
                       ("/teams/create", {"name": "Y"})):
        r = client.post(path, data=data, follow_redirects=False)
        assert r.status_code == 302, path
        assert r.headers["location"] in ("/my/tasks", "/login"), \
            "%s → %s" % (path, r.headers["location"])


# ---------------- 硬边界 ----------------

def test_station_tasks_does_not_touch_settlement_tables():
    """硬边界回归：只新增 bd_* 表，结算域四张表不得出现 bd 字段。"""
    settlement = ("formal_records", "person_daily_stats",
                  "month_perf_records", "payroll_period_rows")
    for t in settlement:
        cols = {c.name for c in Base.metadata.tables[t].columns}
        assert not any(c.startswith("bd_") for c in cols), t
    for t in ("bd_team", "bd_team_member", "bd_station", "bd_task",
              "bd_task_assign", "bd_task_progress"):
        assert t in Base.metadata.tables, t
