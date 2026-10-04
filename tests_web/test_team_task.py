# -*- coding: utf-8 -*-
"""团队 + 车站任务测试（规格 `docs/specs-team-management.md` /
`docs/specs-station-tasks.md`）。

覆盖：团队定义（圈人/指定队长）、队长权限（落点/无重定向环/导航 gate）、
车站→任务、分派 ≤2 人、状态机（未分配/进行中/已完成）、每日进展（当天覆盖）、
管理端任务总表（分配日期区间）、队员只读、硬边界（不碰结算域）。
"""
import re
from datetime import date, timedelta

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
    # 撤人：完成就是完成（pct=100 优先），不会退回未分配
    bd_tasks.assign_members(db, tid, [], by="admin")
    db.commit()
    assert db.get(BdTask, tid).state == "done"
    # "没担当 + 有进度" = 进行中（2026-10-03 修：旧口径会显示成"未分配"）
    bd_tasks.save_progress(db, tid, 20, by="admin")
    db.commit()
    assert db.get(BdTask, tid).state == "doing"
    # 真正的未分配：没担当 + pct=0
    bd_tasks.save_progress(db, tid, 0, by="admin")
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
    for tid in ('data-testid="tab-mine"', 'data-testid="tab-unassigned"',
                'data-testid="tab-doing"', 'data-testid="tab-done"'):
        assert tid in p.text
    assert "未分配（1）" in p.text
    # 默认 tab = 我的（队长页面与员工一样）；本队待分派去「未分配」tab 看
    assert p.text.count('data-testid="my-task-row"') == 0
    p2 = client.get("/my/tasks?tab=unassigned")
    assert 'data-testid="assign-%d"' % seeded["task"] in p2.text
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


def test_member_can_report_own_but_not_others(client, seeded):
    """口径（2026-10-03 变更）：**员工自己先上报**，队长做调整。

    - 队员对自己担当的任务 → **有滑动条、能上报**
    - 队员不能**分派**（分派=任务管理，队长专属）
    - 队员不能上报**别人担当**的任务（数据隔离）
    """
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
    assert 'data-testid="slider-%d"' % tid in p.text, "队员对自己的任务应能上报"
    assert 'data-testid="assign-%d"' % tid not in p.text, "队员不能分派"
    r = _post(client, "/my/tasks/progress", {"task_id": str(tid), "pct": "60"},
              from_path="/my/tasks")
    assert r.status_code == 303
    db = appdb.SessionLocal()
    assert db.get(BdTask, tid).pct == 60
    # 换成别人担当 → 他不能上报
    bd_tasks.assign_members(db, tid, ["P1"], by="admin")
    db.commit()
    db.close()
    r = _post(client, "/my/tasks/progress", {"task_id": str(tid), "pct": "99"},
              from_path="/my/report")
    assert r.status_code == 303
    db = appdb.SessionLocal()
    assert db.get(BdTask, tid).pct == 60, "不能改别人担当的任务"
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


def test_designate_leader_syncs_account_role(seeded):
    """指定队长 → 账号角色自动变 leader；不再当任何队队长 → 退回 staff；admin 不动。"""
    db = appdb.SessionLocal()
    tid = seeded["team"]
    u2 = db.query(User).filter(User.username == "tangjing").first()
    assert u2.role == "staff"
    # 把 P2（汤静）设成队长
    bd_teams.set_members(db, tid, [("P1", "member"), ("P2", "leader")])
    db.commit()
    assert db.query(User).filter(User.username == "tangjing").first().role == "leader"
    assert db.query(User).filter(User.username == "ogawa").first().role == "staff", \
        "不再当队长要退回 staff"
    # 队长被移出所有队 → 退回 staff
    bd_teams.set_members(db, tid, [("P1", "member")])
    db.commit()
    assert db.query(User).filter(User.username == "tangjing").first().role == "staff"
    # 管理员账号绝不被改写
    assert db.query(User).filter(User.username == "admin").first().role == "admin"
    db.close()


def test_leader_view_after_role_sync(client, seeded):
    """端到端：把某人设成队长后，他登录看到的是**队长视图**（三 tab + 分派控件）。"""
    db = appdb.SessionLocal()
    tid = seeded["team"]
    bd_teams.set_members(db, tid, [("P2", "leader")])
    db.commit()
    db.close()
    _login(client, "tangjing", password="pw123456")
    r = client.get("/", follow_redirects=False)
    assert r.headers["location"] == "/my/tasks"          # 队长落点
    p = client.get("/my/tasks?tab=unassigned").text
    assert 'data-testid="tab-unassigned"' in p
    assert 'data-testid="assign-%d"' % seeded["task"] in p


# ---------------- 队长自己也要巡店（2026-10-03 用户口径） ----------------
def test_leader_cannot_be_in_two_teams_and_cannot_see_other_team_tasks(
        client, seeded):
    """**一个队员只能在一个队**（用户 2026-10-03 口径）。

    本轮改口径：以前允许「队长去别的队当队员、被派活」（跨队兼任），
    与"一个队员只能在一个队"互斥 → 现在写入端直接拒绝，
    并且他在别队看不见任务（数据隔离）。
    """
    from app.models import BdTask
    from app.services import bd_tasks as _bt
    db = appdb.SessionLocal()
    b = bd_teams.create_team(db, "汤静队", by="admin")
    bd_teams.set_members(db, b.id, [("P3", "leader")])
    db.commit()          # 先落地：下面失败那次 rollback 不能把建队也回滚
    # P1 已在 A 队 → 不能再进 B 队（以前允许，现在明确拒绝）
    with pytest.raises(bd_teams.TeamError) as e:
        bd_teams.set_members(db, b.id, [("P3", "leader"), ("P1", "member")])
    assert "别的队" in str(e.value)
    db.rollback()
    st = _bt.create_station(db, "池ノ上", line="井の頭線")
    _bt.create_tasks(db, [st.id], by="admin", team_id=b.id,
                     assign_date=date(2026, 10, 3))
    b_task = db.query(BdTask).filter(BdTask.station_id == st.id).one()
    b_task_id = b_task.id
    db.commit()
    db.close()
    _login(client, "ogawa")                    # A 队队长，不在 B 队
    p = client.get("/my/tasks?tab=mine")
    assert p.status_code == 200
    assert "池ノ上" not in p.text, "别队的任务不该出现在他的「我的」里"
    db = appdb.SessionLocal()
    t = db.get(BdTask, b_task_id)
    u_a = db.query(User).filter(User.username == "ogawa").one()
    assert _bt.can_report(db, u_a, t) is False
    assert _bt.can_assign(db, u_a, t) is False
    assert _bt.can_adjust(db, u_a, t) is False
    own = db.get(BdTask, seeded["task"])       # 本队那条 → 都有权限
    assert _bt.can_assign(db, u_a, own) is True
    db.close()




def test_leader_can_submit_his_own_task_in_his_team(client, seeded):
    """队长在自己带的队里被派了活 → 有滑动条、能提交。"""
    db = appdb.SessionLocal()
    bd_tasks.assign_members(db, seeded["task"], ["P1"], by="admin")
    db.commit()
    t = db.get(BdTask, seeded["task"])
    db.close()
    _login(client, "ogawa")
    p = client.get("/my/tasks?tab=mine")
    assert "駒場東大前" in p.text
    assert 'data-testid="slider-%d"' % t.id in p.text
    r = _post(client, "/my/tasks/progress",
              {"task_id": str(t.id), "pct": "40"}, from_path="/my/tasks")
    assert r.status_code == 303
    db = appdb.SessionLocal()
    assert db.get(BdTask, t.id).pct == 40
    db.close()


# ---------------- 开始日 / 完成日（自动写） ----------------

def test_start_and_done_date_auto_written(seeded):
    db = appdb.SessionLocal()
    tid = seeded["task"]
    t = db.get(BdTask, tid)
    assert t.start_date is None and t.done_date is None
    bd_tasks.save_progress(db, tid, 30, by="ogawa")
    db.commit()
    t = db.get(BdTask, tid)
    assert t.start_date == date.today(), "首次提交 = 开始日"
    assert t.done_date is None
    bd_tasks.save_progress(db, tid, 100, by="ogawa")
    db.commit()
    t = db.get(BdTask, tid)
    assert t.done_date == date.today(), "到 100% = 完成日"
    # 进度回退 → 完成日清掉（与 state 自洽）
    bd_tasks.save_progress(db, tid, 80, by="ogawa")
    db.commit()
    t = db.get(BdTask, tid)
    assert t.done_date is None and t.start_date == date.today()
    assert t.state == "doing"
    db.close()


def test_board_shows_dates_and_stale_filter(client, seeded):
    db = appdb.SessionLocal()
    bd_tasks.save_progress(db, seeded["task"], 30, by="ogawa")
    st2 = bd_tasks.create_station(db, "池ノ上")
    bd_tasks.create_tasks(db, [st2.id], by="admin", team_id=seeded["team"],
                          assign_date=date(2026, 10, 3))     # 从没提交过 → 停滞
    db.commit()
    db.close()
    _login(client, "admin")
    p = client.get("/tasks")
    assert p.status_code == 200
    assert date.today().strftime("%Y-%m-%d") in p.text, "开始日要显示"
    assert 'data-testid="by-team"' in p.text, "按队汇总要在"
    assert 'data-testid="stale-only"' in p.text
    # 停滞筛选：只应留下"从没提交过"的那条
    p2 = client.get("/tasks?stale=1")
    assert "池ノ上" in p2.text
    assert "駒場東大前" not in p2.text, "今天提交过的不该算停滞"


def test_remove_member_warns_about_open_tasks(client, seeded):
    """移出一个名下还有未完成任务的人 → 明确提示（不自动改派）。"""
    db = appdb.SessionLocal()
    bd_tasks.assign_members(db, seeded["task"], ["P2"], by="admin")
    db.commit()
    db.close()
    _login(client, "admin")
    r = _post(client, "/teams/%d/members" % seeded["team"],
              {"person": ["P1"], "leader": "P1"},
              from_path="/teams/%d" % seeded["team"])
    assert r.status_code == 303
    loc = r.headers["location"]
    assert "%E6%9C%AA%E5%AE%8C%E6%88%90" in loc or "未完成" in loc, loc


# ---------------- 变更日志（用户 2026-10-03 第 3 条） ----------------

def test_logs_written_for_task_and_team_changes(seeded):
    """任务：创建/派队/分派/上报/状态变化 都留日志；团队：建队/成员/队长任免 也留。"""
    from app.models import BdLog
    db = appdb.SessionLocal()
    tid = seeded["task"]
    before = db.query(BdLog).count()
    bd_tasks.assign_members(db, tid, ["P2"], by="admin")
    bd_tasks.save_progress(db, tid, 40, "开始", by="ogawa")
    bd_tasks.save_progress(db, tid, 70, "队长调整", by="ogawa")
    bd_tasks.set_task_team(db, [tid], seeded["team"],
                           assign_date=date(2026, 10, 9))
    bd_teams.set_members(db, seeded["team"], [("P2", "leader")])
    db.commit()
    logs = db.query(BdLog).order_by(BdLog.id.asc()).all()
    assert len(logs) > before
    actions = [g.action for g in logs]
    assert "assign" in actions and "progress" in actions
    assert "role" in actions, "队长任免要留痕（旧实现就地改，看不到历史）"
    # 进度日志能看到"改了两次、谁改的"
    prog = [g for g in logs if g.action == "progress"]
    assert len(prog) >= 2
    assert prog[0].old_value == "0%" and prog[0].new_value == "40%"
    assert prog[1].old_value == "40%" and prog[1].new_value == "70%"
    # 换队日志带 队名 旧→新
    disp = [g for g in logs if g.action == "dispatch"]
    assert disp and disp[0].field == "team" and "小川队" in disp[0].new_value
    db.close()


def test_task_detail_page_shows_timeline_and_isolates(client, seeded):
    """任务详情页：有日志时间线；别的队的人看不到（数据隔离）。"""
    db = appdb.SessionLocal()
    tid = seeded["task"]
    bd_tasks.assign_members(db, tid, ["P2"], by="admin")
    bd_tasks.save_progress(db, tid, 55, "一线报的", by="ogawa")
    db.commit()
    db.close()
    _login(client, "admin")
    p = client.get("/tasks/%d" % tid)
    assert p.status_code == 200
    assert 'data-testid="task-timeline"' in p.text
    assert 'data-testid="progress-history"' in p.text
    assert "一线报的" in p.text
    # 汤静（P2）是担当 → 能看；甘子杰（P3，外人）→ 看不到
    _login(client, "tangjing")
    assert client.get("/tasks/%d" % tid).status_code == 200
    _login(client, "ganzijie")
    r = client.get("/tasks/%d" % tid, follow_redirects=False)
    assert r.status_code == 302, "不是本队队长也不是担当 → 不能看"


def test_logs_page_admin_only_and_leader_scoped(client, seeded):
    _login(client, "admin")
    p = client.get("/logs")
    assert p.status_code == 200 and 'data-testid="logs"' in p.text
    # 队员没有 log.view → 回员工首页
    _login(client, "tangjing")
    r = client.get("/logs", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith(("/my/plan", "/my/report", "/my/perf"))


# ---------------- 数据隔离（用户 2026-10-03 第 9 条） ----------------

def test_team_leader_cannot_touch_other_team(seeded):
    """a 队队长不能处理 b 队的任务（读写都拦）。"""
    db = appdb.SessionLocal()
    b = bd_teams.create_team(db, "汤静队", by="admin")
    bd_teams.set_members(db, b.id, [("P3", "leader")])   # P2 已在 A 队（一人只能一队）
    st = bd_tasks.create_station(db, "池ノ上")
    bd_tasks.create_tasks(db, [st.id], by="admin", team_id=b.id,
                          assign_date=date(2026, 10, 3))
    t = db.query(BdTask).filter(BdTask.station_id == st.id).one()
    u_a = db.query(User).filter(User.username == "ogawa").one()   # a 队队长
    assert bd_tasks.can_report(db, u_a, t) is False
    assert bd_tasks.can_assign(db, u_a, t) is False
    assert bd_tasks.can_adjust(db, u_a, t) is False
    # 本队那条 → 都有权限
    own = db.get(BdTask, seeded["task"])
    assert bd_tasks.can_assign(db, u_a, own) is True
    assert bd_tasks.can_report(db, u_a, own) is True
    db.close()


def test_cannot_assign_resigned_member(seeded):
    """离职/停用的人不能派工（他执行不了）；成员记录仍保留。"""
    db = appdb.SessionLocal()
    u = db.query(User).filter(User.username == "tangjing").one()
    u.status = "resigned"
    db.commit()
    with pytest.raises(bd_tasks.TaskError):
        bd_tasks.assign_members(db, seeded["task"], ["P2"], by="admin")
    # 成员记录还在（只是状态变了）
    assert any(m["person_code"] == "P2"
               for m in bd_teams.team_members(db, seeded["team"]))
    st = [m for m in bd_teams.team_members(db, seeded["team"])
          if m["person_code"] == "P2"][0]
    assert st["status"] == "resigned" and st["can_work"] is False
    db.close()


def test_team_detail_shows_employee_status(client, seeded):
    _login(client, "admin")
    p = client.get("/teams/%d" % seeded["team"])
    assert p.status_code == 200
    assert "员工状态" in p.text
    assert 'data-testid="status-P1"' in p.text and "在岗" in p.text


# ---------------- 角色能力表（权限表 a 方案） ----------------

def test_role_capability_table_drives_judgement(client, seeded):
    """权限口径来自 bd_role_cap（表空则回退缺省），三类角色能力不同。"""
    from app.services import bd_perm
    db = appdb.SessionLocal()
    assert bd_perm.allowed(db, "leader", "task.assign") is True
    assert bd_perm.allowed(db, "staff", "task.assign") is False
    assert bd_perm.allowed(db, "staff", "task.report") is True
    assert bd_perm.allowed(db, "admin", "team.manage") is True
    # 改表即改判权（单一来源）
    from app.models import BdRoleCap
    row = (db.query(BdRoleCap)
           .filter(BdRoleCap.role == "staff",
                   BdRoleCap.capability == "task.report").first())
    if row is not None:
        row.allowed = False
        db.commit()
        bd_perm.clear_cache()
        assert bd_perm.allowed(db, "staff", "task.report") is False
    bd_perm.clear_cache()
    db.close()


def test_settlement_code_never_reads_ops_domain():
    """**口径（用户 2026-10-03）：结算域与作业域各干各的，任务完成情况不影响绩效和工资。**

    源码级守门：结算域的服务/路由**不得引用**作业域的任何模块或模型
    （列级守门见 `test_station_tasks_does_not_touch_settlement_tables`；
    行为级守门见 `test_progress_submission_only_writes_bd_tables`）。
    """
    import pathlib
    import app as app_pkg
    root = pathlib.Path(app_pkg.__file__).parent
    ops_names = ("bd_tasks", "bd_teams", "bd_log", "bd_perm", "bd_station",
                 "bd_store", "bd_area", "BdTask", "BdTaskAssign",
                 "BdTaskProgress", "BdStation", "BdTeam", "BdTeamMember",
                 "BdLog", "BdRoleCap", "bd_r")
    offenders = []
    for sub in ("services", "routers"):
        for p in sorted((root / sub).glob("*.py")):
            if p.name.startswith("bd_"):
                continue                      # 作业域自己的文件
            txt = p.read_text(encoding="utf-8")
            for kw in ops_names:
                if kw in txt:
                    offenders.append("%s → %s" % (p.name, kw))
    assert not offenders, "结算域引用了作业域（违反各干各的）：%s" % offenders


def test_progress_submission_only_writes_bd_tables(client, seeded):
    """**行为级守门**：上报进展只写 `bd_*` 表 —— 不会顺手改绩效/工资/结算任何表。"""
    from sqlalchemy import event
    db = appdb.SessionLocal()
    touched = set()

    def _before_flush(session, flush_context, instances):
        for obj in list(session.new) + list(session.dirty) + list(session.deleted):
            touched.add(type(obj).__tablename__)

    event.listen(db, "before_flush", _before_flush)
    try:
        bd_tasks.save_progress(db, seeded["task"], 100, "完成", by="ogawa")
        db.rollback()
    finally:
        event.remove(db, "before_flush", _before_flush)
    assert touched, "没抓到 flush，测试本身失效"
    bad = sorted(t for t in touched if not t.startswith("bd_"))
    assert not bad, "上报进展写了非作业域的表：%s" % bad
    db.close()


# ---------------- 假期模式 + 派工提醒（用户 2026-10-03 要求） ----------------

def test_leave_start_and_end(seeded):
    """假期模式：开启（可留空结束日=未定）→ 休假中；结束 → **从当天起不算休假**。"""
    from app.services import bd_leave
    db = appdb.SessionLocal()
    today = bd_leave.today()
    row = bd_leave.start_leave(db, "P2", today, None, "回老家", by="tangjing")
    db.commit()
    assert row.status == "active" and row.end_date is None
    assert bd_leave.is_on_leave(db, "P2", today) is True
    assert bd_leave.is_on_leave(db, "P1", today) is False
    # 结束 → 今天起不再是休假
    bd_leave.end_leave(db, "P2", today, by="tangjing")
    db.commit()
    assert bd_leave.is_on_leave(db, "P2", today) is False
    assert bd_leave.active_leave(db, "P2") is None
    # 历史留痕（不是删行）
    from app.models import BdStaffLeave
    rows = db.query(BdStaffLeave).filter(BdStaffLeave.person_code == "P2").all()
    assert len(rows) == 1 and rows[0].status == "ended"
    db.close()


def test_leave_new_one_ends_previous(seeded):
    """同一个人同时只有一条 active：新开一条 → 旧的自动结束（不出现两段重叠）。"""
    from app.services import bd_leave
    db = appdb.SessionLocal()
    t = bd_leave.today()
    bd_leave.start_leave(db, "P2", t, None, "第一次", by="x")
    db.commit()
    bd_leave.start_leave(db, "P2", t + timedelta(days=10), None, "第二次", by="x")
    db.commit()
    assert bd_leave.active_leave(db, "P2").reason == "第二次"
    first = [r for r in bd_leave.active_map.__globals__["BdStaffLeave"].__table__.c
             and [] or []]
    from app.models import BdStaffLeave
    rows = (db.query(BdStaffLeave).filter(BdStaffLeave.person_code == "P2")
            .order_by(BdStaffLeave.id.asc()).all())
    assert len(rows) == 2
    assert rows[0].status == "ended" and rows[1].status == "active"
    assert rows[0].end_date == t + timedelta(days=9)
    db.close()


def test_assign_warns_on_leave_but_does_not_block(seeded):
    """**休假中派工 → 提醒但成功**（用户明确"不强制约束"）。"""
    from app.services import bd_leave
    db = appdb.SessionLocal()
    today = bd_leave.today()
    bd_leave.start_leave(db, "P2", today, today + timedelta(days=3), "休假",
                         by="tangjing")
    db.commit()
    r = bd_tasks.assign_members(db, seeded["task"], ["P2"], by="ogawa",
                                on_date=today)
    db.commit()
    assert r["n"] == 1, "休假不阻断派工"
    assert any("休假中" in w for w in r["warnings"]), r["warnings"]
    assert db.query(BdTaskAssign).count() == 1
    db.close()


def test_assign_warns_when_plan_says_off(seeded):
    """出勤计划说该日不出勤 → 派工时提醒（仍然成功）。"""
    from app.models import StaffDatePlan
    from app.services import bd_leave
    db = appdb.SessionLocal()
    today = bd_leave.today()
    db.add(StaffDatePlan(person_code="P2", plan_date=today, available=False,
                         reported=False, source="staff"))
    db.commit()
    r = bd_tasks.assign_members(db, seeded["task"], ["P2"], by="ogawa",
                                on_date=today)
    db.commit()
    assert r["n"] == 1
    assert any("不出勤" in w for w in r["warnings"]), r["warnings"]
    db.close()


def test_assign_no_warning_when_available(seeded):
    """正常在岗 → 不提醒（避免狼来了）。"""
    from app.services import bd_leave
    db = appdb.SessionLocal()
    today = bd_leave.today()
    r = bd_tasks.assign_members(db, seeded["task"], ["P2"], by="ogawa",
                                on_date=today)
    db.commit()
    assert r["warnings"] == []
    db.close()


def test_availability_reads_never_write(seeded):
    """可用性检查（派工提醒的数据源）**只读不写** —— 别在派工前顺手改计划表。"""
    from sqlalchemy import event
    from app.services import bd_leave
    db = appdb.SessionLocal()
    touched = set()

    def _before_flush(session, ctx, instances):
        for o in list(session.new) + list(session.dirty) + list(session.deleted):
            touched.add(type(o).__tablename__)

    event.listen(db, "before_flush", _before_flush)
    try:
        bd_leave.availability_map(db, ["P1", "P2"], bd_leave.today())
        db.rollback()
    finally:
        event.remove(db, "before_flush", _before_flush)
    assert touched == set(), "可用性检查写了表：%s" % touched
    db.close()


def test_leader_can_use_plan_page_and_leave(client, seeded):
    """**队长也是员工**：出勤计划页 + 假期模式对 leader 也要开放。"""
    _login(client, "ogawa")
    p = client.get("/my/plan")
    assert p.status_code == 200, "队长必须能登记出勤计划"
    assert 'data-testid="leave-card"' in p.text
    r = _post(client, "/my/leave",
              {"start_date": date.today().isoformat(), "end_date": "",
               "reason": "休假"}, from_path="/my/plan")
    assert r.status_code == 303
    p2 = client.get("/my/plan")
    assert 'data-testid="leave-state"' in p2.text and "休假中" in p2.text
    # 任务页有横幅
    assert 'data-testid="my-leave-banner"' in client.get("/my/tasks").text
    # 结束
    r = _post(client, "/my/leave/end", {}, from_path="/my/plan")
    assert r.status_code == 303
    assert "未休假" in client.get("/my/plan").text


def test_leave_cannot_be_set_for_someone_else(client, seeded):
    """隔离：员工只能给自己开假（表单里的 person_code 被忽略）。"""
    from app.services import bd_leave
    _login(client, "tangjing")
    r = _post(client, "/my/leave",
              {"start_date": date.today().isoformat(), "end_date": "",
               "reason": "试", "person_code": "P1"}, from_path="/my/plan")
    assert r.status_code == 303
    db = appdb.SessionLocal()
    assert bd_leave.is_on_leave(db, "P2", bd_leave.today()) is True
    assert bd_leave.is_on_leave(db, "P1", bd_leave.today()) is False, "不能替别人开假"
    db.close()


def test_leader_sees_leave_tag_on_assign_picker(client, seeded):
    """队长派工时候选旁边能看到「休假」标签（提醒，不禁止勾选）。"""
    from app.services import bd_leave
    db = appdb.SessionLocal()
    bd_leave.start_leave(db, "P2", bd_leave.today(), None, "休假", by="x")
    db.commit()
    db.close()
    _login(client, "ogawa")
    p = client.get("/my/tasks?tab=unassigned")
    assert p.status_code == 200
    assert 'data-testid="avail-%d-P2"' % seeded["task"] in p.text
    assert "休假" in p.text


def test_admin_board_marks_assignee_on_leave(client, seeded):
    """管理端任务总表：担当旁边标「休」。"""
    from app.services import bd_leave
    db = appdb.SessionLocal()
    bd_tasks.assign_members(db, seeded["task"], ["P2"], by="admin")
    bd_leave.start_leave(db, "P2", bd_leave.today(), None, "休假", by="x")
    db.commit()
    db.close()
    _login(client, "admin")
    p = client.get("/tasks")
    assert p.status_code == 200
    assert 'data-testid="leave-%d-P2"' % seeded["task"] in p.text


# ---------------- 进展确认/调整 + 消息通知（用户 2026-10-03） ----------------

def test_employee_report_then_leader_confirms_and_notifies(client, seeded):
    """员工上报 → 待确认；队长**确认** → 已确认 + **给员工发消息**。"""
    from app.models import BdMessage, BdMessageRecipient, BdTaskProgress
    db = appdb.SessionLocal()
    tid = seeded["task"]
    bd_tasks.assign_members(db, tid, ["P2"], by="admin")
    db.commit()
    db.close()
    # 员工（汤静）上报 80%
    _login(client, "tangjing")
    r = _post(client, "/my/tasks/progress", {"task_id": str(tid), "pct": "80"},
              from_path="/my/tasks")
    assert r.status_code == 303
    db = appdb.SessionLocal()
    p = db.query(BdTaskProgress).filter(BdTaskProgress.task_id == tid).one()
    assert p.review_status == "pending" and p.reported_pct == 80
    assert p.reported_by == "tangjing"
    assert db.query(BdMessage).count() == 0, "上报本身不发消息"
    db.close()
    # 队长确认
    _login(client, "ogawa")
    r = _post(client, "/my/tasks/confirm", {"task_id": str(tid)},
              from_path="/my/tasks?tab=unassigned")
    assert r.status_code == 303
    db = appdb.SessionLocal()
    p = db.query(BdTaskProgress).filter(BdTaskProgress.task_id == tid).one()
    assert p.review_status == "confirmed" and p.pct == 80
    assert p.reviewed_by == "ogawa" and p.reviewed_at is not None
    m = db.query(BdMessage).one()
    # 消息的"发件人"就是做这件事的人（队长），只是 scope 标成任务进展
    assert m.scope == "task_review" and m.sender_kind == "leader"
    assert m.sender == "ogawa"
    assert "确认" in m.title and "80" in m.body
    recips = db.query(BdMessageRecipient).filter(
        BdMessageRecipient.message_id == m.id).all()
    assert [r_.person_code for r_ in recips] == ["P2"], "只发给担当"
    assert recips[0].read_at is None
    db.close()


def test_leader_adjust_notifies_old_and_new(client, seeded):
    """员工报 80 → 队长**调整为 70** → 原值保留 + 消息里写明 80→70。"""
    from app.models import BdMessage, BdTaskProgress
    db = appdb.SessionLocal()
    tid = seeded["task"]
    bd_tasks.assign_members(db, tid, ["P2"], by="admin")
    db.commit()
    db.close()
    _login(client, "tangjing")
    _post(client, "/my/tasks/progress", {"task_id": str(tid), "pct": "80"},
          from_path="/my/tasks")
    _login(client, "ogawa")
    r = _post(client, "/my/tasks/progress",
              {"task_id": str(tid), "pct": "70", "note": "按现场情况下调"},
              from_path="/my/tasks?tab=unassigned")
    assert r.status_code == 303
    db = appdb.SessionLocal()
    p = db.query(BdTaskProgress).filter(BdTaskProgress.task_id == tid).one()
    assert p.pct == 70 and p.reported_pct == 80, "原值必须留着（用于对比展示）"
    assert p.review_status == "adjusted" and p.reviewed_by == "ogawa"
    m = db.query(BdMessage).one()
    assert "调整" in m.title
    assert "80" in m.body and "70" in m.body, m.body
    assert m.url == "/tasks/%d" % tid, "消息要能直达任务"
    db.close()


def test_leader_direct_write_does_not_notify(client, seeded):
    """没有员工上报时队长直接填 → 不发"调整"消息（没得对比）。"""
    from app.models import BdMessage
    db = appdb.SessionLocal()
    bd_tasks.assign_members(db, seeded["task"], ["P2"], by="admin")
    db.commit()
    db.close()
    _login(client, "ogawa")
    _post(client, "/my/tasks/progress",
          {"task_id": str(seeded["task"]), "pct": "50"},
          from_path="/my/tasks?tab=unassigned")
    db = appdb.SessionLocal()
    assert db.query(BdMessage).count() == 0
    db.close()


# ---------------- 消息模块 ----------------

def test_admin_sends_to_all_and_staff_reads(client, seeded):
    from app.models import BdMessage, BdMessageRecipient
    _login(client, "admin")
    r = _post(client, "/messages/send",
              {"title": "全体通知", "body": "明天开大会", "mode": "all"},
              from_path="/messages?tab=new")
    assert r.status_code == 303
    db = appdb.SessionLocal()
    m = db.query(BdMessage).one()
    recips = {x.person_code for x in db.query(BdMessageRecipient)
              .filter(BdMessageRecipient.message_id == m.id).all()}
    assert {"P1", "P2", "P3"} <= recips, recips
    db.close()
    # 员工看到未读 + 能标已读
    _login(client, "tangjing")
    p = client.get("/messages")
    assert p.status_code == 200
    assert "全体通知" in p.text and 'data-testid="msg-unread"' in p.text
    assert 'data-testid="tab-messages"' in p.text
    mid = m.id
    r = _post(client, "/messages/%d/read" % mid, {}, from_path="/messages")
    assert r.status_code == 303
    db = appdb.SessionLocal()
    row = db.query(BdMessageRecipient).filter(
        BdMessageRecipient.message_id == mid,
        BdMessageRecipient.person_code == "P2").one()
    assert row.read_at is not None
    db.close()
    assert "没有未读消息" in client.get("/messages").text


def test_leader_can_only_send_to_own_team(client, seeded):
    """**数据隔离**：队长发给本队队员 OK；发给队外的人 → 拒绝且不发消息。"""
    from app.models import BdMessage
    _login(client, "ogawa")
    r = _post(client, "/messages/send",
              {"title": "给队员", "mode": "pick", "person": "P2"},
              from_path="/messages?tab=new")
    assert r.status_code == 303
    db = appdb.SessionLocal()
    assert db.query(BdMessage).count() == 1
    db.close()
    r = _post(client, "/messages/send",
              {"title": "越界", "mode": "pick", "person": "P3"},
              from_path="/messages?tab=new")
    assert r.status_code == 303
    assert "err=" in r.headers["location"]
    db = appdb.SessionLocal()
    assert db.query(BdMessage).count() == 1, "越界不许发出去"
    db.close()


def test_staff_cannot_send_messages(client, seeded):
    from app.models import BdMessage
    from app.services import bd_msg
    _login(client, "tangjing")
    r = _post(client, "/messages/send", {"title": "我要发", "mode": "all"},
              from_path="/messages")
    assert r.status_code == 303
    db = appdb.SessionLocal()
    assert db.query(BdMessage).count() == 0
    with pytest.raises(bd_msg.MsgError):
        bd_msg.recipients_for(db, db.query(User).filter(
            User.username == "tangjing").one(), mode="all")
    db.close()


def test_message_go_marks_read_and_redirects(client, seeded):
    from app.services import bd_msg
    db = appdb.SessionLocal()
    admin = db.query(User).filter(User.username == "admin").one()
    m = bd_msg.send(db, admin, ["P2"], "看这条", "点开跳任务",
                    url="/tasks/%d" % seeded["task"])
    db.commit()
    mid = m.id
    db.close()
    _login(client, "tangjing")
    r = client.get("/messages/%d/go" % mid, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/tasks/%d" % seeded["task"]
    db = appdb.SessionLocal()
    from app.models import BdMessageRecipient
    assert db.query(BdMessageRecipient).filter(
        BdMessageRecipient.message_id == mid).one().read_at is not None
    db.close()


def test_message_isolation_other_person_cannot_read_it(client, seeded):
    """别人不能读我的消息（越权 → 回列表，不改状态）。"""
    from app.services import bd_msg
    db = appdb.SessionLocal()
    admin = db.query(User).filter(User.username == "admin").one()
    m = bd_msg.send(db, admin, ["P2"], "只给汤静", "", url="/tasks/1")
    db.commit()
    mid = m.id
    db.close()
    _login(client, "ganzijie")          # P3，不是收件人
    r = client.get("/messages/%d/go" % mid, follow_redirects=False)
    assert r.headers["location"] == "/messages"
    db = appdb.SessionLocal()
    from app.models import BdMessageRecipient
    assert db.query(BdMessageRecipient).filter(
        BdMessageRecipient.message_id == mid).one().read_at is None
    db.close()


def test_message_send_only_writes_bd_tables(client, seeded):
    """行为级守门：发消息/确认进展**只写 bd_ 表**（结算域照旧不碰）。"""
    from sqlalchemy import event
    from app.services import bd_msg
    db = appdb.SessionLocal()
    touched = set()

    def _before_flush(session, ctx, instances):
        for o in list(session.new) + list(session.dirty) + list(session.deleted):
            touched.add(type(o).__tablename__)

    event.listen(db, "before_flush", _before_flush)
    try:
        admin = db.query(User).filter(User.username == "admin").one()
        bd_msg.send(db, admin, ["P2"], "标题", "正文")
        db.rollback()
    finally:
        event.remove(db, "before_flush", _before_flush)
    bad = sorted(t for t in touched if not t.startswith("bd_"))
    assert not bad, "发消息写了非作业域的表：%s" % bad
    db.close()


def test_leader_pending_tab_lists_reports_to_confirm(client, seeded):
    """队长有专用「待确认」tab —— 队员报过、还没处理的都在这（别藏在"进行中"里）。"""
    db = appdb.SessionLocal()
    tid = seeded["task"]
    bd_tasks.assign_members(db, tid, ["P2"], by="admin")
    db.commit()
    db.close()
    _login(client, "tangjing")
    _post(client, "/my/tasks/progress", {"task_id": str(tid), "pct": "80"},
          from_path="/my/tasks")
    _login(client, "ogawa")
    p = client.get("/my/tasks")
    assert 'data-testid="tab-pending"' in p.text
    assert "待确认（1）" in p.text
    assert 'data-testid="pending-hint"' in p.text
    p2 = client.get("/my/tasks?tab=pending")
    assert "駒場東大前" in p2.text, "待确认 tab 里要能看到这条"
    assert 'data-testid="confirm-%d"' % tid in p2.text
    assert "队员报 80%" in p2.text or "80%" in p2.text


# ---------------- 假期模式 → 出勤计划自动标 ×（用户 2026-10-03「可以标」） ----------------

def test_leave_marks_plan_off_and_clears_on_end(seeded):
    """开假 → 休假期内的出勤计划**自动变成不出勤（×）**；结束休假 → **精确撤销**。"""
    from app.models import StaffDatePlan
    from app.services import bd_leave, plan_leave
    db = appdb.SessionLocal()
    today = bd_leave.today()
    row = bd_leave.start_leave(db, "P2", today, today + timedelta(days=3),
                               "休假", by="tangjing")
    db.commit()
    days = [today + timedelta(days=i) for i in range(4)]
    rows = {r.plan_date: r for r in db.query(StaffDatePlan).filter(
        StaffDatePlan.person_code == "P2",
        StaffDatePlan.plan_date.in_(days)).all()}
    assert len(rows) == 4, "休假期内每天都该有行"
    assert all(not r.available for r in rows.values()), "全部标成不出勤"
    assert all(r.leave_id == row.id for r in rows.values()), "要标记来源（便于撤销）"
    assert all(r.source == "leave" for r in rows.values())
    # 结束休假 → 自动标的行撤销（回到默认，不再是不出勤）
    bd_leave.end_leave(db, "P2", today, by="tangjing")
    db.commit()
    left = db.query(StaffDatePlan).filter(
        StaffDatePlan.person_code == "P2",
        StaffDatePlan.plan_date.in_(days)).count()
    assert left == 0, "撤销 = 删掉自动标的行（恢复默认规则）"
    db.close()


def test_leave_does_not_override_manual_off_or_reported(seeded):
    """不休假不许抢：**员工自己点的 ×** 与**已自报的日期**都不被休假覆盖。"""
    from app.models import StaffDatePlan
    from app.services import bd_leave
    db = appdb.SessionLocal()
    today = bd_leave.today()
    d_manual = today + timedelta(days=1)
    d_reported = today + timedelta(days=2)
    db.add(StaffDatePlan(person_code="P2", plan_date=d_manual,
                         available=False, reported=False, source="web"))
    db.add(StaffDatePlan(person_code="P2", plan_date=d_reported,
                         available=True, reported=True, source="report"))
    db.commit()
    bd_leave.start_leave(db, "P2", today, today + timedelta(days=5), "", by="x")
    db.commit()
    rows = {r.plan_date: r for r in db.query(StaffDatePlan).filter(
        StaffDatePlan.person_code == "P2",
        StaffDatePlan.plan_date.in_([d_manual, d_reported])).all()}
    assert rows[d_manual].leave_id is None, "员工自己标的 × 不能被休假认领"
    assert rows[d_reported].available is True, "已自报（实际出勤）不许改成不出勤"
    db.close()


def test_leave_marks_show_in_admin_plan_matrix(client, seeded):
    """管理端出勤计划矩阵：休假期那几天是 ×，且 title 标明「假期模式」。"""
    from app.services import bd_leave
    db = appdb.SessionLocal()
    today = bd_leave.today()
    bd_leave.start_leave(db, "P2", today, today + timedelta(days=2), "", by="x")
    db.commit()
    db.close()
    _login(client, "admin")
    from app.services import date_plan
    key = date_plan.current_period(today)
    p = client.get("/staff-plans?period=%s" % key)
    assert p.status_code == 200
    assert 'data-testid="cell-P2-%s"' % today.isoformat() in p.text
    assert "（假期模式）" in p.text


def test_leader_set_even_if_not_ticked_as_member(client, seeded):
    """只点「队长」不勾「进队」也要生效（后端兜底）。

    2026-10-03 用户问"如何指定队长？"—— 前端 `person` 里没有他时旧实现**静默丢弃**，
    界面上"指定了却没生效"。语义：被指定当队长 = 他当然在队里。
    """
    from app.models import BdTeamMember
    db = appdb.SessionLocal()
    tid = seeded["team"]
    db.close()
    _login(client, "admin")
    r = _post(client, "/teams/%d/members" % tid,
              {"leader": "P1"}, from_path="/teams/%d" % tid)
    assert r.status_code == 303
    db = appdb.SessionLocal()
    m = [x for x in bd_teams.team_members(db, tid)
         if x["person_code"] == "P1"]
    assert m and m[0]["role"] == "leader", "只传 leader 也要进队并当队长"
    db.close()


def test_team_detail_leader_howto_and_autocheck(client, seeded):
    """页面要写清怎么指定队长，且点「队长」自动勾「进队」。"""
    _login(client, "admin")
    p = client.get("/teams/%d" % seeded["team"])
    assert p.status_code == 200
    assert 'data-testid="leader-howto"' in p.text
    assert "指定队长" in p.text
    assert 'onchange="bdLeaderPicked(this)"' in p.text
    assert "function bdLeaderPicked" in p.text


# ---------------- 「一个队员只能在一个队」（用户 2026-10-03 口径） ----------------

def test_person_options_hides_people_in_other_teams(seeded):
    """候选列表 = **本队现役成员 + 自由人**；已在别队的人**不显示**。"""
    from app.models import BdTeam
    db = appdb.SessionLocal()
    t1 = seeded["team"]
    t2 = bd_teams.create_team(db, "汤静队", by="admin")
    bd_teams.set_members(db, t1, [("P1", "leader"), ("P2", "member")])
    db.commit()
    # 编 t2 时：P1/P2 已在 t1 → 不出现；P3 自由 → 出现
    codes = [p["code"] for p in bd_teams.person_options(db, team_id=t2.id)]
    assert "P1" not in codes and "P2" not in codes, "已进别队的人不能出现在候选里"
    assert "P3" in codes
    # 编 t1 时：本队的人要在（才能取消勾选/改角色）
    codes1 = [p["code"] for p in bd_teams.person_options(db, team_id=t1)]
    assert {"P1", "P2"} <= set(codes1) and "P3" in codes1
    # 不传 team_id（别处复用）→ 不过滤，行为保持
    assert {"P1", "P2", "P3"} <= {p["code"] for p in
                                  bd_teams.person_options(db)}
    db.close()


def test_set_members_rejects_person_from_another_team(seeded):
    """写入端也挡：已在别队的人不能被圈进第二个队（界面隐藏只是第一层）。"""
    db = appdb.SessionLocal()
    t1 = seeded["team"]
    t2 = bd_teams.create_team(db, "汤静队", by="admin")
    bd_teams.set_members(db, t1, [("P1", "leader")])
    db.commit()
    with pytest.raises(bd_teams.TeamError) as e:
        bd_teams.set_members(db, t2.id, [("P1", "member")])
    assert "别的队" in str(e.value) and "移出" in str(e.value)
    db.rollback()
    # 原队移出后就能进新队（正常调人流程）
    bd_teams.set_members(db, t1, [])
    db.commit()
    bd_teams.set_members(db, t2.id, [("P1", "member")])
    db.commit()
    assert [m["person_code"] for m in bd_teams.team_members(db, t2.id)] == ["P1"]
    db.close()


def test_team_detail_candidate_scope_hint(client, seeded):
    """页面写清"别队的人不在此列表"并把被隐藏的人数显示出来。"""
    db = appdb.SessionLocal()
    bd_teams.set_members(db, seeded["team"], [("P1", "leader")])
    t2 = bd_teams.create_team(db, "汤静队", by="admin")
    db.commit()
    t2_id = t2.id
    db.close()
    _login(client, "admin")
    p = client.get("/teams/%d" % t2_id)
    assert p.status_code == 200
    assert 'data-testid="pick-scope"' in p.text
    assert 'data-testid="n-elsewhere"' in p.text
    assert "一个队员只能在一个队" in p.text
    # t1 的队长 P1 在编 t2 时不该出现（他已在 t1）
    assert 'data-testid="pick-P1"' not in p.text
    assert 'data-testid="pick-P3"' in p.text


def test_db_level_single_team_constraint(seeded):
    """**死规定做到数据库层**：同一个人的**现役**行唯一（部分唯一索引）。

    用户 2026-10-03："不存在跨队借调。只有转出再转入。这是死规定。"
    → 服务层判重只是第一层；绕过服务层的任何写（脚本/手工 SQL/并发）都被索引挡住。
    转队 = 原队 end_date 收口 + 新队开一行 → 不冲突（历史可追溯）。
    """
    from sqlalchemy.exc import IntegrityError
    from app.models import BdTeamMember
    db = appdb.SessionLocal()
    t2 = bd_teams.create_team(db, "二队", by="admin")
    db.add(BdTeamMember(team_id=seeded["team"], person_code="P3",
                        role="member", start_date=date(2026, 10, 4)))
    db.commit()
    # 绕过服务层直接插第二个队的现役行 → 数据库拒绝
    db.add(BdTeamMember(team_id=t2.id, person_code="P3", role="member",
                        start_date=date(2026, 10, 4)))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    # 正常转队（转出 → 转入）必须仍然可行
    row = (db.query(BdTeamMember)
           .filter(BdTeamMember.team_id == seeded["team"],
                   BdTeamMember.person_code == "P3").one())
    row.end_date = date(2026, 10, 4)                 # 转出
    db.commit()
    db.add(BdTeamMember(team_id=t2.id, person_code="P3", role="member",
                        start_date=date(2026, 10, 5)))   # 转入
    db.commit()
    assert [m["person_code"] for m in bd_teams.team_members(db, t2.id)] == ["P3"]
    assert bd_teams.team_members(db, seeded["team"]) == [] or \
        "P3" not in [m["person_code"] for m in
                     bd_teams.team_members(db, seeded["team"])]
    db.close()


# ---------------- 分页（用户 2026-10-03："你就不能做个分页吗？"） ----------------

def test_stations_page_is_paginated(client, seeded):
    """车站页必须分页（以前 515 行全铺一屏）。

    断言：默认每页 50 条、有分页条、第 2 页内容不同、筛选条件在翻页链接里保留。
    """
    from app.services import paging
    db = appdb.SessionLocal()
    for i in range(60):
        bd_tasks.create_station(db, "测试站%02d" % i, line="井の頭線")
    db.commit()
    db.close()
    _login(client, "admin")
    p1 = client.get("/stations")
    assert p1.status_code == 200
    assert 'data-testid="pager"' in p1.text
    assert "共 61 条" in p1.text and "第 1 / 2 页" in p1.text
    assert 'data-testid="page-next"' in p1.text
    assert 'data-testid="page-prev"' not in p1.text, "第 1 页不该有上一页"
    p2 = client.get("/stations?page=2")
    assert "第 2 / 2 页" in p2.text
    assert 'data-testid="page-prev"' in p2.text
    # 两页不重复
    import re
    def names(html):
        return set(re.findall(r'data-testid="st-name-(\d+)"', html))
    assert not (names(p1.text) & names(p2.text)), "两页内容不该重叠"
    # 筛选条件要带进翻页链接
    f = client.get("/stations?kw=测试站&page=1")
    assert "kw=%E6%B5%8B%E8%AF%95%E7%AB%99" in f.text or "kw=测试站" in f.text
    assert paging.PER_DEFAULT == 50


def test_bulk_make_tasks_uses_all_stations_not_just_page(client, seeded):
    """「全部建任务」作用于**全集**，不受分页影响（否则只建当前页 → 静默漏建）。"""
    db = appdb.SessionLocal()
    for i in range(60):
        bd_tasks.create_station(db, "批量站%02d" % i)
    db.commit()
    before = db.query(BdTask).count()
    db.close()
    _login(client, "admin")
    r = _post(client, "/stations/tasks", {"all_without": "1"},
              from_path="/stations")
    assert r.status_code == 303
    db = appdb.SessionLocal()
    after = db.query(BdTask).count()
    db.close()
    assert after - before == 60, "全部建任务要把 60 个站都建了（不是只建 1 页）"


def test_db_pagination_helper_bounds_and_total(seeded):
    """`paging.paginate()`：夹取越界页、算对 total/pages/区间。"""
    from app.services import paging
    db = appdb.SessionLocal()
    for i in range(7):
        bd_tasks.create_station(db, "helper站%02d" % i)
    db.commit()
    from app.models import BdStation
    q = db.query(BdStation).order_by(BdStation.id.asc())
    pg = paging.paginate(q, page=1, per=3)
    assert pg["total"] == 8 and pg["pages"] == 3
    assert (pg["start"], pg["end"]) == (1, 3) and pg["has_next"] and not pg["has_prev"]
    pg3 = paging.paginate(q, page=3, per=3)
    assert (pg3["start"], pg3["end"]) == (7, 8) and pg3["has_prev"] and not pg3["has_next"]
    # 越界夹到最后一页（不能返回空页）
    over = paging.paginate(q, page=99, per=3)
    assert over["page"] == 3 and len(over["rows"]) == 2
    under = paging.paginate(q, page=0, per=3)
    assert under["page"] == 1
    db.close()


def test_staff_admin_and_store_entities_are_paginated(client, seeded):
    """员工管理（55 行）与店铺实体（42k 行，以前 limit(500) 硬砍）都要分页。"""
    from app.models import StoreEntity
    from datetime import date as _d
    db = appdb.SessionLocal()
    from sqlalchemy import func as _f
    base = (db.query(_f.max(StoreEntity.id)).scalar() or 0)
    for i in range(55):
        db.add(StoreEntity(store_id_raw="S%04d" % i, name_local="测试店%02d" % i,
                           name_norm="测试店%02d" % i,
                           master_id=base + i + 1))   # 自指=主档
    db.commit()
    db.close()
    _login(client, "admin")
    p = client.get("/stores/entities")
    assert p.status_code == 200
    assert 'data-testid="pager"' in p.text and "第 1 / 2 页" in p.text
    assert 'data-testid="page-next"' in p.text
    db2 = appdb.SessionLocal()
    for i in range(55):   # 员工管理要 >50 人才会翻页
        _user(db2, "u%02d" % i, "staff", "Z%03d" % i, "测试员工%02d" % i)
    db2.commit()
    db2.close()
    a = client.get("/staff-admin")
    assert 'data-testid="pager"' in a.text
