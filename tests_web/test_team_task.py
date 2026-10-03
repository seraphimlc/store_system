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

def test_leader_sees_and_reports_own_cross_team_task(client, seeded):
    """队长在**别人的队**里当队员、被派了活：看得见（跨队），而且**作为担当能自己上报**
    （用户 2026-10-03 口径：员工自己先上报、队长做调整 —— 他在这里就是员工）。"""
    db = appdb.SessionLocal()
    b = bd_teams.create_team(db, "汤静队", by="admin")
    bd_teams.set_members(db, b.id, [("P3", "leader"), ("P1", "member")])
    st = bd_tasks.create_station(db, "池ノ上", line="井の頭線")
    bd_tasks.create_tasks(db, [st.id], by="admin", team_id=b.id,
                          assign_date=date(2026, 10, 3))
    t = db.query(BdTask).filter(BdTask.station_id == st.id).one()
    bd_tasks.assign_members(db, t.id, ["P1"], by="admin")   # 小川（P1）自己的活
    db.commit()
    db.close()
    _login(client, "ogawa")
    p = client.get("/my/tasks?tab=mine")
    assert p.status_code == 200
    assert 'data-testid="tab-mine"' in p.text          # 「我的」tab 在
    assert "池ノ上" in p.text, "队长在别队的活必须看得见（跨队）"
    assert 'data-testid="slider-%d"' % t.id in p.text, "担当本人应能上报"
    # 但不能分派别人的队（分派=任务管理）
    assert 'data-testid="assign-%d"' % t.id not in p.text
    r = _post(client, "/my/tasks/progress", {"task_id": str(t.id), "pct": "30"},
              from_path="/my/tasks?tab=mine")
    assert r.status_code == 303
    db = appdb.SessionLocal()
    assert db.get(BdTask, t.id).pct == 30
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
    bd_teams.set_members(db, b.id, [("P3", "leader"), ("P2", "member")])
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
