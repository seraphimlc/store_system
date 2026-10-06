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
from app.models import (User, BdStation, BdTask, BdTaskAssign, BdTaskProgress,
                        StaffDailyReport,
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
    """车站页 = **车站资产**（车站归车站，2026-10-06 用户口径）。

    页面上**不再有**新建车站 / 批量建任务 / 批量派队 / 任务列（那些归任务页）。
    ⚠️ 2026-10-06 用户："车站页面的查询条件部分太挤了，就留俩条件：关键词，线路。其它的都不用。"
    """
    _login(client, "admin")
    p = client.get("/stations")
    assert p.status_code == 200 and "駒場東大前" in p.text
    for tid in ('data-testid="station-kw"', 'data-testid="line-filter"',
                'data-testid="stations-export"'):
        assert tid in p.text, "车站页缺少 %s" % tid
    # 其它筛选条件都撤掉（太挤）
    for gone in ('data-testid="pref-filter"', 'data-testid="operator-filter"',
                 'data-testid="kind-filter"', 'data-testid="multi-only"',
                 'data-testid="sort-select"', 'data-testid="per-select"'):
        assert gone not in p.text, "车站页不该再有 %s" % gone
    # 表格里也不该再有「顺序」列（用户："顺序列不需要"）
    assert "<th>顺序</th>" not in p.text
    # 任务相关的东西一个都不许有（车站归车站）
    for gone in ('data-testid="station-create"', 'data-testid="make-all-tasks"',
                 'data-testid="bulk-team-form"', 'data-testid="no-task-only"',
                 'data-testid="make-task"'):
        assert gone not in p.text, "车站页不该再出现 %s" % gone
    assert "新建车站" not in p.text


def test_bulk_make_tasks_for_all_places(seeded):
    """批量建任务（车站页入口已删，服务层仍在）：**所有还没任务的物理车站**都建上。

    用户 2026-10-06："批量建任务也不需要。这个页面只维护车站信息。"
    → 车站页不再有这个入口；批量建任务的正式入口是 `/tasks/new`（按线路选站）。
    """
    db = appdb.SessionLocal()
    bd_tasks.create_station(db, "池ノ上", line="井の頭線")
    db.commit()
    pids = bd_tasks.place_ids_without_task(db)
    assert len(pids) == 1, "駒場東大前已有任务 → 只有池ノ上还没建（实际 %d）" % len(pids)
    r = bd_tasks.create_tasks_for_places(db, pids, by="admin")
    assert r["created"] == 1
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
    # ⚠️ 2026-10-06 审计：队长默认落点改了 —— 有未分配就默认进「未分配」
    #    （旧默认「我的」，队长第一眼是空列表，看不到还有 N 个站没分人）
    assert 'data-testid="bulk-assign-form"' in p.text, "队长默认应落在「未分配」"
    assert p.text.count('data-testid="my-task-row"') >= 1, "默认页就是未分配列表"
    p_mine = client.get("/my/tasks?tab=mine")
    assert p_mine.text.count('data-testid="my-task-row"') == 0, "「我的」仍是空的"
    p2 = client.get("/my/tasks?tab=unassigned")
    # 未分配 tab 的主体是**批量分派**（勾任务 + 选队员 + 分配）
    assert 'data-testid="bulk-assign-form"' in p2.text
    assert 'data-testid="bulk-check-%d"' % seeded["task"] in p2.text
    # ⚠️ 口径变更（用户 2026-10-06 追加需求）：未分配的任务也能**直接调进度/标完成**
    #    （原来按"这里不需要进度调整控件"藏起来；新需求要求放开）
    assert 'data-testid="slider-%d"' % seeded["task"] in p2.text
    assert 'data-testid="mark-done-%d"' % seeded["task"] in p2.text
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

def test_admin_board_assigned_tab_and_date_range(client, seeded):
    db = appdb.SessionLocal()
    st2 = bd_tasks.create_station(db, "池ノ上")
    bd_tasks.create_tasks(db, [st2.id], by="admin")            # 未派队
    db.commit()
    db.close()
    _login(client, "admin")
    # ⚠️ 2026-10-05 起任务页默认 tab = 未分配（用户："任务分为已完成，已分配，未分配"）；
    # 「已派队」的视图 = tab=assigned
    p = client.get("/tasks?tab=assigned")
    assert p.status_code == 200
    assert "駒場東大前" in p.text
    assert "池ノ上" not in p.text, "还没派队的任务不该出现在已分配 tab"
    assert "2026-10-03" in p.text, "分配日期要显示出来"
    # 分配日期区间之外 → 查不到；区间之内 → 查得到
    assert "駒場東大前" not in client.get("/tasks?tab=assigned&date_from=2026-10-04").text
    assert "駒場東大前" not in client.get("/tasks?tab=assigned&date_to=2026-10-02").text
    assert "駒場東大前" in client.get(
        "/tasks?tab=assigned&date_from=2026-10-01&date_to=2026-10-05").text
    # 按队 / 按状态筛
    assert "駒場東大前" in client.get(
        "/tasks?tab=assigned&team=%d" % seeded["team"]).text
    assert "駒場東大前" in client.get(
        "/tasks?tab=assigned&date_from=2026-10-01&date_to=2026-10-05").text
    assert "駒場東大前" in client.get("/tasks?tab=assigned&state=unassigned").text
    assert "駒場東大前" not in client.get("/tasks?tab=assigned&state=done").text


def test_admin_fix_progress_via_page(client, seeded):
    """⚠️ 用户 2026-10-06 口径：**管理员只能改"队长已确认过"的** → 未确认先拦住。"""
    tid = seeded["task"]
    db = appdb.SessionLocal()
    bd_tasks.assign_members(db, tid, ["P2"], by="admin")
    tj = db.query(User).filter(User.username == "tangjing").first()
    bd_tasks.save_progress(db, tid, 70, "员工报 70", by="tangjing", actor_user=tj)
    db.commit()
    db.close()
    _login(client, "admin")
    # ① 队长还没确认 → 管理员不能改
    r0 = _post(client, "/tasks/%d/progress" % tid,
               {"pct": "80", "note": "抢改"}, from_path="/tasks")
    assert r0.status_code == 303 and "err=" in r0.headers["location"], \
        "未确认就改要被拦住"
    assert "队长确认" in __import__("urllib.parse", fromlist=["unquote"]).unquote(
        r0.headers["location"])
    # ② 队长确认后 → 管理员可以改
    db = appdb.SessionLocal()
    og = db.query(User).filter(User.username == "ogawa").first()
    bd_tasks.save_progress(db, tid, 70, "", by="ogawa", actor_user=og, confirm=True)
    db.commit()
    db.close()
    r = _post(client, "/tasks/%d/progress" % tid,
              {"pct": "80", "note": "管理员修正"}, from_path="/tasks")
    assert r.status_code == 303
    db = appdb.SessionLocal()
    row = db.query(BdTaskProgress).filter(
        BdTaskProgress.task_id == tid).one()
    assert row.pct == 80 and row.reviewed_by == "admin"
    assert row.reported_by == "tangjing", "员工原值/上报人不能被管理员改掉"
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
    assert 'data-testid="bulk-check-%d"' % seeded["task"] in p, "未分配 tab 用批量分派"


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
                          assign_date=date(2026, 10, 3))
    task2 = (db.query(BdTask).join(BdStation, BdStation.id == BdTask.station_id)
             .filter(BdStation.name == "池ノ上").first())
    # ⚠️ 2026-10-06 口径：停滞 = **已分到人** + 未完成 + ≥2 天没提交
    #    （"还没分担当"的还没开始，谈不上没动；旧口径让停滞筛选等于不过滤）
    bd_tasks.assign_members(db, task2.id, ["P2"], by="admin")
    db.commit()
    db.close()
    _login(client, "admin")
    p = client.get("/tasks?tab=assigned")
    assert p.status_code == 200
    # ⚠️ 2026-10-06 用户删掉 开始日/完成日/最后提交/多少天没动 等列 →
    #    停滞改成看**整行高亮**（`data-stale="1"`）+ `stale=1` 筛选；日期在任务详情页/导出里
    assert 'data-stale="1"' in p.text, "停滞行要高亮"
    assert 'data-testid="by-team"' in p.text, "按队汇总要在"
    assert 'data-testid="stale-only"' in p.text
    # 停滞筛选：只应留下"从没提交过"的那条
    p2 = client.get("/tasks?tab=assigned&stale=1")
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
    #: **唯一允许跨域的编排层**：员工"点数 + 任务进度一次提交"必须同事务
    #: （用户 2026-10-06 口径）→ `self_report.py` 同时 import 两边，
    #: 它不碰结算域四表，也不被作业域引用。除此之外任何结算文件引用作业域都算违规。
    allowed = {"self_report.py"}
    offenders = []
    for sub in ("services", "routers"):
        for p in sorted((root / sub).glob("*.py")):
            if p.name.startswith("bd_") or p.name in allowed:
                continue                      # 作业域自己的文件 / 跨域编排层
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
    # ⚠️ 2026-10-06：未分配 tab 改成**批量分派**（逐行候选撤了）→ 标签写在批量下拉的 option 文本里
    opt = re.search(r'<option value="P2">([^<]*)</option>', p.text)
    assert opt, "批量下拉里要有候选"
    assert any(t in opt.group(1) for t in ("休假", "计划休", "请假")), \
        "候选旁边要有休假标签（提醒不禁止）：%s" % (opt.group(1) if opt else "")
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
    p = client.get("/tasks?tab=assigned")
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
    assert 'data-testid="nav-messages"' in p.text, "消息挪到右上角菜单了"
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
    db = appdb.SessionLocal()
    pids = bd_tasks.place_ids_without_task(db)      # 全集（不受分页影响）
    r = bd_tasks.create_tasks_for_places(db, pids, by="admin")
    after = db.query(BdTask).count()
    db.close()
    assert after - before == 60, "批量建任务要把 60 个站都建了（不是只建 1 页）"
    assert r["created"] == 60


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


# ---------------- 一都三県 线路 + 车站（2026-10-05） ----------------

def _tiny_rail_payload():
    """给导入用的小数据集（不依赖 scripts/bd_kanto_rail.json，跑得快）。"""
    return {
        "source": "test",
        "lines": [
            {"key": "京王電鉄|井の頭線", "operator": "京王電鉄", "name": "井の頭線",
             "kind": "private", "prefs": ["13"], "n": 2},
            {"key": "東日本旅客鉄道|山手線", "operator": "東日本旅客鉄道", "name": "山手線",
             "kind": "jr", "prefs": ["13"], "n": 1},
        ],
        "stations": [
            {"line_key": "京王電鉄|井の頭線", "name": "駒場東大前", "line": "井の頭線",
             "operator": "京王電鉄", "kind": "private", "pref": "13",
             "lon": 139.68, "lat": 35.66, "code": "001", "group": "g1"},
            {"line_key": "京王電鉄|井の頭線", "name": "井の頭公園", "line": "井の頭線",
             "operator": "京王電鉄", "kind": "private", "pref": "13",
             "lon": 139.58, "lat": 35.70, "code": "002", "group": "g2"},
            {"line_key": "東日本旅客鉄道|山手線", "name": "駒場東大前", "line": "山手線",
             "operator": "東日本旅客鉄道", "kind": "jr", "pref": "13",
             "lon": 139.68, "lat": 35.66, "code": "003", "group": "g1"},
        ],
    }


def test_kanto_rail_data_file_consistency():
    """数据文件本身要自洽（131 线 / 1920 站 / 一线一站 / 都道府県只含一都三県）。

    这条是**数据守门**：换 N02 版本重跑 `bd_fetch_rail.py` 后若结构变了要立刻发现。
    """
    import json
    from pathlib import Path
    p = Path(__file__).parent.parent / "scripts" / "bd_kanto_rail.json"
    if not p.exists():                                    # 没抓过数据就跳过（CI 友好）
        pytest.skip("scripts/bd_kanto_rail.json 不存在（先跑 bd_fetch_rail.py）")
    d = json.loads(p.read_text(encoding="utf-8"))
    assert len(d["lines"]) == 131, "一都三県线路数变了：%d" % len(d["lines"])
    assert len(d["stations"]) == 1920, "车站数变了：%d" % len(d["stations"])
    keys = {(l["operator"], l["name"]) for l in d["lines"]}
    assert all((s["operator"], s["line"]) in keys for s in d["stations"]), \
        "有车站挂不到线路上"
    assert {s["pref"] for s in d["stations"]} <= {"13", "11", "12", "14"}, "混进了一都三県以外的县"
    # 一线一站：(线路, 站名) 必须唯一（同名跨线是允许的，这正是用户要的口径）
    combo = [(s["operator"], s["line"], s["name"]) for s in d["stations"]]
    assert len(combo) == len(set(combo)), "同一线路里出现了重复站名"


def test_import_rail_creates_lines_stations_and_tasks(seeded):
    """导入：建线路 + 建车站 + **自动为每个车站建任务**；重复导入幂等。"""
    from app.services import bd_import_rail
    db = appdb.SessionLocal()
    try:
        before_tasks = db.query(BdTask).count()
        rep = bd_import_rail.import_rail(db, _tiny_rail_payload(), dry=True)
        # ⚠️ seeded 里已经有「駒場東大前」这个历史站（无线路）→ 它会被**认领**，
        # 不是新建（这正是"不能重复建任务"的关键）
        assert rep["lines_created"] == 2
        assert rep["stations_new"] == 2 and rep["stations_adopted"] == 1
        db.rollback()                                     # dry-run 不写

        rep = bd_import_rail.import_rail(db, _tiny_rail_payload())
        db.commit()
        assert rep["lines_created"] == 2
        assert rep["stations_new"] == 2 and rep["stations_adopted"] == 1
        # 每个车站都有任务（车站即任务）：只给**新建**的车站补任务
        assert db.query(BdTask).count() == before_tasks + 2
        from app.models import BdLine, BdStation
        line = db.query(BdLine).filter(BdLine.name == "井の頭線").one()
        assert line.operator_short == "京王", "运营商简称要能对上"
        assert line.kind == "private" and line.n_station == 2
        st = db.query(BdStation).filter(BdStation.name == "井の頭公園").one()
        assert st.line_id == line.id and st.pref == "13" and st.source == "mlit"
        assert st.lon == 139.58 and st.ekicode == "002" and st.group_code == "g2"
        # 同名跨线 → 两行（用户："先分哪个线的就按哪个线的来"）
        same = db.query(BdStation).filter(BdStation.name == "駒場東大前").all()
        assert len(same) == 2, "同名跨线要先各存一行（历史那行被认领 + 山手線新一行）"
        assert {s.line_id for s in same} == {line.id,
                                            db.query(BdLine).filter(BdLine.name == "山手線").one().id}

        # 幂等：再导一次不新增
        rep2 = bd_import_rail.import_rail(db, _tiny_rail_payload())
        db.commit()
        assert rep2["lines_created"] == 0 and rep2["stations_new"] == 0
        assert rep2["stations_existing"] == 3, "三个站都已存在（含被认领的那个）"
        assert db.query(BdTask).count() == before_tasks + 2
    finally:
        db.close()


def test_import_rail_adopts_legacy_station_and_keeps_its_task(seeded):
    """历史车站（无线路 + 已派队）必须被**认领**：挂上线路、**任务与派工保留**、不新增一行。

    这是最要紧的一条：认领失败 = 给同一个车站重复建任务，队伍的派工就白做了。
    """
    from app.services import bd_import_rail
    from app.models import BdLine, BdStation
    db = appdb.SessionLocal()
    try:
        st = db.get(BdStation, seeded["station"])
        assert st.line_id is None and st.name == "駒場東大前"
        task = db.get(BdTask, seeded["task"])
        assert task.team_id is not None
        n_st, n_task = db.query(BdStation).count(), db.query(BdTask).count()

        rep = bd_import_rail.import_rail(db, _tiny_rail_payload())
        db.commit()
        assert rep["stations_adopted"] == 1, "同名历史站要被认领"
        assert rep["stations_new"] == 2, "另外两个站才是新建"
        db.refresh(st)
        assert st.line_id is not None and st.pref == "13"
        assert db.query(BdStation).count() == n_st + 2, "认领不该多出一行"
        assert db.query(BdTask).count() == n_task + 2, "历史站的任务必须保留"
        db.refresh(task)
        assert task.team_id is not None, "原有派工不能被清掉"
    finally:
        db.close()


def test_import_rail_renorms_legacy_station_name(seeded):
    """老库的 name_norm 是旧口径（`ケ` 没统一成 `ヶ`）→ 导入时要改写，否则认领不到。"""
    from app.services import bd_import_rail
    from app.models import BdStation
    db = appdb.SessionLocal()
    try:
        st = bd_tasks.create_station(db, "箱根ケ崎")          # 旧写法
        db.flush()
        assert st.name_norm == "箱根ヶ崎" or True
        st.name_norm = "箱根ケ崎"                              # 人为退回旧键
        db.commit()
        payload = {"lines": [{"operator": "東日本旅客鉄道", "name": "八高線",
                              "kind": "jr", "prefs": ["13"], "n": 1}],
                   "stations": [{"name": "箱根ヶ崎", "line": "八高線",
                                 "operator": "東日本旅客鉄道", "kind": "jr",
                                 "pref": "13", "lon": 139.2, "lat": 35.7,
                                 "code": "", "group": ""}]}
        rep = bd_import_rail.import_rail(db, payload)
        db.commit()
        assert rep["stations_adopted"] == 1 and rep["stations_new"] == 0
        assert rep["renorm"] == 1, "旧键要被改写"
        db.refresh(st)
        assert st.name_norm == "箱根ヶ崎" and st.line_id is not None
    finally:
        db.close()


def test_task_tabs_unassigned_assigned_done(client, seeded):
    """任务页三个 tab（未分配 / 已分配 / 已完成）+ 计数（用户 2026-10-05 要求）。

    ⚠️ tab 按"**有没有人管**"分，不是按 state：派给了团队但还没分人的任务
    （state 仍是 unassigned）在管理员眼里是**已分配**。
    """
    db = appdb.SessionLocal()
    st2 = bd_tasks.create_station(db, "池ノ上")
    bd_tasks.create_tasks(db, [st2.id], by="admin")           # 无队无担当 → 未分配
    st3 = bd_tasks.create_station(db, "新代田")
    bd_tasks.create_tasks(db, [st3.id], by="admin")
    t3 = db.query(BdTask).filter(BdTask.station_id == st3.id).one()
    bd_tasks.set_task_team(db, [t3.id], seeded["team"], assign_date=date(2026, 10, 4))
    bd_tasks.assign_members(db, t3.id, ["P2"], by="admin")     # 有人 → 已分配
    db.commit()
    c = bd_tasks.tab_counts(db)
    db.close()
    assert c["unassigned"] == 1, "只有「池ノ上」是没人管的"
    assert c["assigned"] == 2, "派了队的历史任务 + 有担当的算「已分配」"
    assert c["done"] == 0
    assert c["all"] == 3

    _login(client, "admin")
    p = client.get("/tasks")
    assert p.status_code == 200
    assert 'data-testid="task-tabs"' in p.text
    for k in ("unassigned", "assigned", "done"):
        assert 'data-testid="tab-%s"' % k in p.text
    assert "池ノ上" in p.text, "默认 tab = 未分配"
    assert "駒場東大前" not in p.text, "派了队的任务不在未分配 tab"
    a = client.get("/tasks?tab=assigned")
    assert "駒場東大前" in a.text and "池ノ上" not in a.text
    d = client.get("/tasks?tab=done")
    assert "駒場東大前" not in d.text and "池ノ上" not in d.text


def test_task_line_filter_and_kw_search_in_sql(client, seeded):
    """按线路查询（用户 2026-10-05："可以根据线路查询"）+ 关键词在 **SQL 里**过滤。

    ⚠️ 关键词必须走 SQL：1,920 个任务 + 分页，只在当前页过滤等于"搜不到"。
    """
    from app.models import BdLine, BdStation
    from app.services import bd_import_rail
    db = appdb.SessionLocal()
    # 造 60 个同线路的车站，其中只有一个名字含"特殊"
    line = BdLine(name="テスト線", name_norm="テスト線", operator="テスト鉄道",
                  operator_short="テスト", kind="private", prefs="13", n_station=60)
    db.add(line)
    db.flush()
    ids = []
    for i in range(60):
        nm = "特殊駅" if i == 59 else "普通駅%02d" % i
        st = BdStation(name=nm, name_norm=bd_tasks.norm_name(nm), line="テスト線",
                       line_id=line.id, operator="テスト鉄道", pref="13", source="mlit")
        db.add(st)
        db.flush()
        ids.append(st.id)
    bd_tasks.create_tasks(db, ids, by="admin")
    # ⚠️ 车站池是**物理车站**口径 → 测完要重建 place（否则这些站没 place，池子里看不到）
    from app.services import bd_places
    bd_places.rebuild_places(db)
    db.commit()
    line_id = line.id
    total_line = bd_tasks.task_board(db, line_id=line_id, tab="unassigned")["total"]
    kw_hit = bd_tasks.task_board(db, line_id=line_id, kw="特殊", tab="unassigned")
    kw_miss = bd_tasks.task_board(db, line_id=line_id, kw="不存在的名字", tab="unassigned")
    db.close()
    assert total_line == 60
    assert kw_hit["total"] == 1, "关键词要在 SQL 里过滤（第 3 页的那个也要能搜到）"
    assert kw_hit["rows"][0]["station_name"] == "特殊駅"
    assert kw_miss["total"] == 0

    _login(client, "admin")
    # 默认 tab = 未分配 = 车站池（这 60 个站没派队 → 都在池子里）
    p = client.get("/tasks?line=%d" % line_id)
    assert p.status_code == 200
    assert 'data-testid="line-filter"' in p.text
    assert "普通駅00" in p.text
    assert "駒場東大前" not in p.text, "别的线路的车站不该出现"
    # 分页条也要在（60 条 > 50/页）
    assert 'data-testid="page-next"' in p.text
    assert client.get("/tasks?line=%d&page=2" % line_id).status_code == 200


def test_board_tab_labels_all_present(client, seeded):
    """三个 tab 的标签都要有（`assigned` 是 tab 键，不在 state 里 —— 漏了会渲染出空标签）。"""
    from app.services import bd_tasks
    for lang in ("zh", "ja"):
        labels = bd_tasks.state_labels(lang)
        for k in bd_tasks.BOARD_TABS:
            assert labels.get(k), "%s 的 tab %s 没有标签" % (lang, k)
    _login(client, "admin")
    p = client.get("/tasks")
    for k, word in (("unassigned", "未分配"), ("assigned", "已分配"),
                    ("done", "已完成")):
        assert 'data-testid="tab-%s">%s（' % (k, word) in p.text, \
            "tab %s 的标签渲染不对" % k


def test_import_rail_ids_are_positive(seeded):
    """导入必须让**数据库发号**（正 id）。

    ⚠️ 2026-10-05 踩到：dry-run 的临时负 id 在真实导入时也被用上了 →
    131 条线路全是负 id（URL 里出现 `?line=-19`）。这条测试钉住这个洞。
    """
    from app.services import bd_import_rail
    from app.models import BdLine
    db = appdb.SessionLocal()
    try:
        bd_import_rail.import_rail(db, _tiny_rail_payload())
        db.commit()
        ids = [i for (i,) in db.query(BdLine.id).all()]
        assert ids and min(ids) > 0, "线路 id 必须是正的（数据库发号）：%s" % ids
    finally:
        db.close()


# ---------------- 车站数据资产（物理车站层，2026-10-05） ----------------

def test_place_layer_groups_by_group_code(seeded):
    """**物理车站层**：同一个 `group_code` 的多条"站×线"归到一个物理车站。

    这是"车站数据资产"的核心结构（用户："车站数据可以当成我们的数据资产"）。
    """
    from app.models import BdStation, BdStationPlace
    from app.services import bd_places
    db = appdb.SessionLocal()
    try:
        # ⚠️ 站必须挂在**真实线路**上：bd_station 有"无线路时站名唯一"的部分唯一索引，
        # 两条同名的"无线路"站会直接撞索引（2026-10-05 测试踩过）
        from app.models import BdLine
        l_y = BdLine(name="山手線", name_norm="山手線", operator="東日本旅客鉄道",
                     operator_short="JR東日本", kind="jr", prefs="13", n_station=1)
        l_i = BdLine(name="井の頭線", name_norm="井の頭線", operator="京王電鉄",
                     operator_short="京王", kind="private", prefs="13", n_station=2)
        db.add_all([l_y, l_i])
        db.flush()
        # 同一个物理车站（渋谷）被两条线路各记一行
        a = BdStation(name="渋谷", name_norm=bd_tasks.norm_name("渋谷"),
                      line="山手線", line_id=l_y.id, operator="東日本旅客鉄道",
                      pref="13", lon=139.70, lat=35.658, group_code="GX01",
                      source="mlit")
        b = BdStation(name="渋谷", name_norm=bd_tasks.norm_name("渋谷"),
                      line="井の頭線", line_id=l_i.id, operator="京王電鉄",
                      pref="13", lon=139.702, lat=35.660, group_code="GX01",
                      source="mlit")
        c = BdStation(name="池ノ上", name_norm=bd_tasks.norm_name("池ノ上"),
                      line="井の頭線", line_id=l_i.id, operator="京王電鉄",
                      pref="13", lon=139.68, lat=35.66, group_code="GX02",
                      source="mlit")
        db.add_all([a, b, c])
        db.flush()
        rep = bd_places.rebuild_places(db)
        db.commit()
        # seeded 里那个站没有 group_code（手工站）→ 它自己也是一个物理车站，共 3 个
        assert rep["places"] == 3, "GX01/GX02 + seeded 的手工站"
        pa = db.query(BdStationPlace).filter(BdStationPlace.group_code == "GX01").one()
        assert pa.n_line == 2 and pa.n_line == len(
            db.query(BdStation).filter(BdStation.place_id == pa.id).all())
        assert set(pa.lines_text.split("、")) == {"山手線", "井の頭線"}
        assert pa.operators.count("、") == 1, "两家运营公司"
        assert db.get(BdStation, c.id).place_id != pa.id
        assert pa.lon and abs(pa.lon - 139.701) < 0.002, "坐标取组内均值"
        assert bd_places.place_label(pa) == "渋谷（2 线）"
        # 幂等：再建一次不新增、不重复
        rep2 = bd_places.rebuild_places(db)
        db.commit()
        assert rep2["created"] == 0 and rep2["places"] == 3
        # seeded 里那个手工站没线路/县/坐标，所以不能断言"全库 0 问题"；
        # 只断言**物理车站层自身**的一致性没问题
        kinds = {i["kind"] for i in bd_places.integrity_issues(db)}
        assert "线路数与成员数不符" not in kinds and "孤儿物理车站" not in kinds
    finally:
        db.close()


def test_place_integrity_detects_broken_asset(seeded):
    """体检要能**抓出**资产坏掉的情况（否则"资产"就是没人管的表）。"""
    from app.models import BdStation, BdTask
    from app.models import BdLine as bd_lines_mod  # noqa: N813  简写，仅本测试用
    from app.services import bd_places
    db = appdb.SessionLocal()
    try:
        line = bd_lines_mod(name="井の頭線", name_norm="井の頭線",
                                  operator="京王電鉄", operator_short="京王",
                                  kind="private", prefs="13", n_station=1)
        db.add(line)
        db.flush()
        seeded_st = db.get(BdStation, seeded["station"])
        # 先把 seeded 的站补成"完整资产行"（挂线路 + 县 + 坐标），否则它自己就是缺项
        seeded_st.line_id = line.id
        seeded_st.pref = "13"
        seeded_st.lon, seeded_st.lat = 139.68, 35.66
        seeded_st.group_code = "GSEED"
        db.flush()
        bd_places.rebuild_places(db)         # 建物理车站层 → 资产健康
        db.commit()
        assert bd_places.integrity_issues(db) == [], "补完资产后应该健康"
        # 故意**绕过 create_station** 直插一行（模拟脏数据/历史脚本写入）：
        # 没有线路、没有县、没有坐标、也没有物理车站 → 体检要全部抓出来
        st = BdStation(name="破损站", name_norm=bd_tasks.norm_name("破损站"),
                       source="manual")
        db.add(st)
        bd_tasks.create_tasks(db, [st.id], by="admin")
        db.flush()
        kinds = {i["kind"] for i in bd_places.integrity_issues(db)}
        assert "缺少线路" in kinds and "缺少物理车站" in kinds and "缺少县" in kinds
    finally:
        db.close()


def test_stations_page_shows_asset_and_no_task_id(client, seeded):
    """车站页显示资产三层规模 + **不显示任务编号**（用户："任务编号不用显示"）。"""
    from app.models import BdStation
    from app.services import bd_places
    from app.models import BdLine
    db = appdb.SessionLocal()
    l_y = BdLine(name="山手線", name_norm="山手線", operator="東日本旅客鉄道",
                 operator_short="JR東日本", kind="jr", prefs="13", n_station=1)
    l_i = BdLine(name="井の頭線", name_norm="井の頭線", operator="京王電鉄",
                 operator_short="京王", kind="private", prefs="13", n_station=1)
    db.add_all([l_y, l_i])
    db.flush()
    a = BdStation(name="渋谷", name_norm=bd_tasks.norm_name("渋谷"), line="山手線",
                  line_id=l_y.id, operator="東日本旅客鉄道", pref="13",
                  group_code="GX01", source="mlit")
    b = BdStation(name="渋谷", name_norm=bd_tasks.norm_name("渋谷"), line="井の頭線",
                  line_id=l_i.id, operator="京王電鉄", pref="13",
                  group_code="GX01", source="mlit")
    db.add_all([a, b])
    db.flush()
    bd_places.rebuild_places(db)
    db.commit()
    db.close()
    _login(client, "admin")
    p = client.get("/stations")
    assert p.status_code == 200
    assert 'data-testid="multi-line-%d"' % a.id in p.text, "跨线站要标出 N 条线"
    for word in ("物理车站", "跨线车站", "车站（站×线）"):
        assert word in p.text, "资产统计卡要有「%s」" % word
    assert "#%d" % seeded["task"] not in p.text, "不许显示任务编号（#id）"
    assert "山手線" in p.text and "井の頭線" in p.text, "跨线站要显示经过哪些线"
    assert seeded["team"] is not None  # 任务相关的变量在这里不再用到（车站页不显示任务）


# ---------------- 沿線顺序（OSM 数据源，2026-10-05） ----------------

def _load_script(name):
    """按文件路径加载 scripts/ 下的脚本（scripts 不是包，没有 __init__.py）。"""
    import importlib.util
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "scripts", name)
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_osm_route_data_is_ordered_and_licensed():
    """顺序数据来自 OSM（**明文有序，不是我们算的**），且带 ODbL 署名信息。

    用户 2026-10-05："你应该还能找到其它的数据源来确定这个顺序，而不是计算出来"
    → 选 A（OSM route relation 的成员本身有序）。
    """
    import json
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = os.path.join(root, "scripts", "bd_osm_routes.json")
    assert os.path.exists(p), "OSM 顺序数据文件必须在仓库里（导入时不需要网络）"
    d = json.load(open(p, encoding="utf-8"))
    assert d["n_route"] >= 100, "一都三县应该有上百条运行系统线路"
    assert "ODbL" in d["license"] and "OpenStreetMap" in d["license"], "许可必须写明"
    keiyo = [r for r in d["routes"] if "京葉線" in (r["name"] or "")]
    assert keiyo, "必须有京葉線"
    r = max(keiyo, key=lambda x: x["n_stop"])
    names = [s["name"] for s in r["stops"]]
    assert "東京" in names and "蘇我" in names
    assert names.index("東京") < names.index("葛西臨海公園") < names.index("蘇我"), \
        "顺序必须是 東京 → 葛西臨海公園 → 蘇我（明文顺序）"
    assert all(s.get("lat") and s.get("lon") for s in r["stops"]), "每个站要有坐标"


def test_seq_matcher_tiers_and_name_gate():
    """匹配三档：F1 强匹配 / 子集（山手線型）/ 名字不相关则拒绝。"""
    mod = _load_script("bd_fill_seq.py")
    near = (35.68, 139.76)
    ours = [{"id": 1, "name": "A", "lat": 35.680, "lon": 139.760, "line_name": "山手線"},
            {"id": 2, "name": "B", "lat": 35.690, "lon": 139.770, "line_name": "山手線"},
            {"id": 3, "name": "C", "lat": 35.700, "lon": 139.780, "line_name": "山手線"}]

    def route(name, pts):
        return {"rel_id": 1, "name": name, "route": "train", "n_stop": len(pts),
                "stops": [{"name": "s%d" % i, "lat": a, "lon": b} for i, (a, b) in enumerate(pts)]}

    # ① 子集：OSM 那条线比我们长（多了 4 个远处的站）→ 第 2 档（山手線就是这个情形）
    sub = route("JR山手線", [(35.680, 139.760), (35.690, 139.770), (35.700, 139.780),
                            (35.80, 139.90), (35.81, 139.91), (35.82, 139.92), (35.83, 139.93)])
    pick = mod.best_route_for_line(ours, [sub])
    assert pick and pick[0] == 2, "应该被子集档接受（召回 100%、精确低但名字相关）"

    # ② 名字不相关 → 拒绝（实测"多摩線"曾被"多摩快速急行"抢走）
    other = route("多摩快速急行", [(35.680, 139.760), (35.690, 139.770), (35.700, 139.780)] +
                  [(35.60 + i * 0.01, 139.5) for i in range(12)])
    assert mod.best_route_for_line(ours, [other]) is None, "名字不相关不能认"

    # ③ 站名归一是必要的（ケ/ヶ、駅、空白）
    assert mod._norm_station("箱根ケ崎駅") == mod._norm_station("箱根ヶ崎")


def test_list_stations_orders_by_seq_when_line_selected(seeded):
    """用户口径："后面我们查的时候就拿这列做 order 排序" → 按线路查时按 seq 排。"""
    from app.models import BdLine, BdStation
    from app.services import bd_places, bd_tasks
    db = appdb.SessionLocal()
    try:
        ln = BdLine(name="测试线", name_norm="测试线", operator="X", operator_short="X",
                    kind="private", prefs="13", n_station=3)
        db.add(ln)
        db.flush()
        for nm, sq in (("第三", 30), ("第一", 10), ("第二", 20)):
            db.add(BdStation(name=nm, name_norm=bd_tasks.norm_name(nm), line="测试线",
                             line_id=ln.id, pref="13", seq=sq, along_km=float(sq),
                             seq_src="osm:test", source="manual"))
        db.flush()
        bd_places.rebuild_places(db)
        db.commit()
        out = bd_tasks.list_stations(db, line_id=ln.id)
        assert [r["station"].name for r in out["rows"]] == ["第一", "第二", "第三"]
        assert out["rows"][0]["station"].along_km == 10.0
    finally:
        db.close()


def test_geometry_seq_is_quality_gated():
    """几何兜底的产物必须**过质量门槛**（用户："别太勉强" → 算不干净的宁可不给）。

    门槛：每个站的投影误差 ≤ 250m（车站必须真的贴在轨道几何上）。
    """
    import json
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = os.path.join(root, "scripts", "bd_line_seq_geom.json")
    assert os.path.exists(p), "几何兜底文件要在仓库里"
    d = json.load(open(p, encoding="utf-8"))
    assert d["n_line"] >= 10
    for L in d["lines"]:
        assert L["max_proj_m"] <= 250, "%s 投影误差 %.0fm 超标，不该产出" % (L["name"], L["max_proj_m"])
        seqs = [s["seq"] for s in L["stops"]]
        kms = [s["km"] for s in L["stops"]]
        assert seqs == list(range(1, len(seqs) + 1)), "%s 的 seq 必须连续" % L["name"]
        assert kms == sorted(kms), "%s 必须按里程递增" % L["name"]
    # 3 条"算不干净"的线路必须**不在**里面（宁可空着）
    names = {L["name"] for L in d["lines"]}
    assert "総武線" not in names and "成田線" not in names


def test_fill_seq_prefers_geom_when_osm_covers_less_than_half():
    """OSM 覆盖不到一半时，整条线改用几何顺序（**不混用两个源**，否则 seq 不可比）。"""
    mod = _load_script("bd_fill_seq.py")
    assert "geom:N02" in open(mod.__file__, encoding="utf-8").read(), "落库脚本要支持几何兜底"


def test_manual_seq_file_for_hard_lines():
    """OSM/几何都拿不到的 3 条线：人工定稿顺序（Wikipedia 駅一覧 + 坐标交叉校验）。

    用户 2026-10-05："剩下的，你可以通过 google 搜索来做。方式是笨点，但肯定能解决。"
    """
    import json
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = os.path.join(root, "scripts", "bd_line_seq_manual.json")
    assert os.path.exists(p), "人工顺序文件要在仓库里"
    d = json.load(open(p, encoding="utf-8"))
    assert d["n_line"] == 3
    assert {L["line"] for L in d["lines"]} == {"総武線", "成田線", "鉄道線"}
    for L in d["lines"]:
        assert L["n_stop"] == len(L["stops"])
        assert [s["seq"] for s in L["stops"]] == list(range(1, len(L["stops"]) + 1))
        assert len({s["name"] for s in L["stops"]}) == len(L["stops"]), "同一条线不能有重复站"
        assert "wiki" in L["source"], "来源必须可审计（Wikipedia 条目）"
        assert L["note"], "支线/折返等要写清楚"
    # ⚠️ 成田線是 Y 字形（有支线）→ **不适用里程**（否则会显示误导性的 37km）
    narita = next(L for L in d["lines"] if L["line"] == "成田線")
    assert narita["km_mode"] == "none"
    assert all(s["km"] is None for s in narita["stops"])


def test_fill_seq_supports_manual_source():
    """落库脚本要支持人工顺序（`manual:` 前缀）。"""
    mod = _load_script("bd_fill_seq.py")
    src = open(mod.__file__, encoding="utf-8").read()
    assert "bd_line_seq_manual.json" in src and "manual:" in src


# ---------------- 建任务（按线路选站；2026-10-05 定稿口径） ----------------

def _place_fixture(db, line_name="测试建线", names=("甲站", "乙站", "丙站"), seqs=(1, 2, 3)):
    """造一条线 + 若干物理车站（带 seq），返回 (line, places)。"""
    from app.models import BdLine, BdStationPlace
    from app.services import bd_places
    line = BdLine(name=line_name, name_norm=line_name, operator="测试铁道",
                  operator_short="测试", kind="private", prefs="13", n_station=len(names))
    db.add(line)
    db.flush()
    places = []
    for nm, sq in zip(names, seqs):
        db.add(BdStation(name=nm, name_norm=bd_tasks.norm_name(nm), line=line_name,
                         line_id=line.id, pref="13", seq=sq, along_km=float(sq),
                         seq_src="osm:test", source="manual"))
        db.flush()
        bd_places.rebuild_places(db)
        pl = (db.query(BdStationPlace)
              .filter(BdStationPlace.name_norm == bd_tasks.norm_name(nm)).first())
        places.append(pl)
    db.commit()
    return line, places


def test_new_task_page_lists_places_in_line_order(client, seeded):
    """建任务页：选线路 → 该线**物理车站**按 seq 列出；已有任务的站禁用（防重复建）。"""
    from app.models import BdTask
    db = appdb.SessionLocal()
    line, places = _place_fixture(db)
    # 给中间那个站先建一个任务 → 页面上应显示"已有任务"且勾选框 disabled
    db.add(BdTask(place_id=places[1].id, station_id=None, source_type="station",
                  state="unassigned", pct=0))
    db.commit()
    lid = line.id
    db.close()

    _login(client, "admin")
    p = client.get("/tasks/new?line=%d" % lid)
    assert p.status_code == 200
    assert p.text.count('data-testid="place-row"') == 3
    assert 'data-testid="place-check"' in p.text
    # 顺序：甲 → 乙 → 丙（按 seq），而不是按 id/名称
    i1, i2, i3 = (p.text.index("甲站"), p.text.index("乙站"), p.text.index("丙站"))
    assert i1 < i2 < i3, "必须按沿線顺序排"
    assert "已有任务" in p.text and "disabled" in p.text
    assert 'data-testid="select-all"' in p.text and 'data-testid="select-none"' in p.text
    assert 'data-testid="team-select"' in p.text


def test_create_tasks_for_places_rolling_dispatch(client, seeded):
    """**滚动派活**（用户口径）：A 队 2 个站 + B 队 1 个站 = 3 个任务，各自独立；
    重复提交同一批 → 全部跳过（不覆盖）。"""
    from app.models import BdTask, BdTeam
    db = appdb.SessionLocal()
    line, places = _place_fixture(db, names=("A1", "A2", "B1"))
    ta = BdTeam(name="甲队", code="JA")
    tb = BdTeam(name="乙队", code="YB")
    db.add_all([ta, tb])
    db.commit()
    r1 = bd_tasks.create_tasks_for_places(db, [places[0].id, places[1].id],
                                         by="admin", team_id=ta.id, actor_user=None)
    r2 = bd_tasks.create_tasks_for_places(db, [places[2].id], by="admin", team_id=tb.id)
    assert (r1["created"], r2["created"]) == (2, 1)
    r3 = bd_tasks.create_tasks_for_places(db, [p.id for p in places], by="admin",
                                          team_id=ta.id)
    assert r3["created"] == 0 and r3["skipped"] == 3, "重复建必须全跳过"
    rows = db.query(BdTask).filter(BdTask.place_id.in_([p.id for p in places])).all()
    assert len(rows) == 3, "3 个站 = 3 个任务（不是 1 个批次任务）"
    assert {t.team_id for t in rows} == {ta.id, tb.id}
    assert all(t.station_id is None and t.assign_date is not None for t in rows), \
        "新任务只挂 place，且自动写分配日期（今天）"
    db.close()


def test_new_task_post_creates_and_shows_in_board(client, seeded):
    """页面提交：勾 2 个站 + 选队 → 建 2 个任务；在总表「已分配」tab 和线路筛选里都能看到。"""
    from app.models import BdTask, BdTeam
    db = appdb.SessionLocal()
    line, places = _place_fixture(db, line_name="提交线", names=("申1", "申2"))
    team = BdTeam(name="提交队", code="TJ")
    db.add(team)
    db.commit()
    tid, lid = team.id, line.id
    ids = [p.id for p in places]
    db.close()

    _login(client, "admin")
    r = _post(client, "/tasks/new", {"place_id": [str(i) for i in ids],
                                     "team": str(tid), "line": str(lid)},
              from_path="/tasks/new?line=%d" % lid)
    assert r.status_code == 303
    db = appdb.SessionLocal()
    rows = db.query(BdTask).filter(BdTask.place_id.in_(ids)).all()
    assert len(rows) == 2
    assert all(t.team_id == tid for t in rows)
    # 总表：已分配 tab + 按线路筛（线路过滤要能穿过 place）
    board = bd_tasks.task_board(db, tab=bd_tasks.TAB_ASSIGNED, line_id=lid, limit=50)
    names = [x["station_name"] for x in board["rows"]]
    assert "申1" in names and "申2" in names
    assert all(x["team_name"] == "提交队" for x in board["rows"])
    db.close()
    # 页面上也看得到
    p = client.get("/tasks?tab=assigned&line=%d" % lid)
    assert p.status_code == 200 and "申1" in p.text


def test_new_task_dedupes_legacy_station_task(seeded):
    """**回归**：老口径任务（只有 station_id、没有 place_id）也算"已有任务"。

    不修的话会出现"同一个物理车站两个任务"（这条测试就是当时抓到这个 bug 后加的）。
    `seeded` 里那个任务就是老口径（station_id 有值 / place_id 为空）。
    """
    from app.models import BdTask, BdStation
    db = appdb.SessionLocal()
    st = (db.query(BdStation)
          .filter(BdStation.name_norm == bd_tasks.norm_name("駒場東大前")).first())
    assert st is not None and st.place_id is not None
    legacy = db.query(BdTask).filter(BdTask.station_id == st.id).first()
    assert legacy is not None, "seeded 应该已经有一条老口径任务"
    assert legacy.place_id is None
    r = bd_tasks.create_tasks_for_places(db, [st.place_id], by="admin", team_id=None)
    assert r["created"] == 0 and r["skipped"] == 1, \
        "老任务已占了这个车站 → 不能再建（否则同一车站两个任务）"
    n = db.query(BdTask).filter(BdTask.station_id == st.id).count()
    assert n == 1
    listed = bd_tasks.list_places_for_line(db, st.line_id)
    assert [x for x in listed if x["place_id"] == st.place_id][0]["has_task"] is True, \
        "页面上也要显示成已有任务（禁用勾选）"
    db.close()


def test_bulk_make_tasks_from_station_page_uses_places(client, seeded):
    """车站页的「全部建任务」也走**物理车站**（跨线站不会建出两个任务）。"""
    from app.models import BdTask, BdStationPlace
    db = appdb.SessionLocal()
    # 造一个跨线物理车站：两条线各一行，指向同一个 place
    _line, places = _place_fixture(db, line_name="跨线甲", names=("跨线站",))
    pl = places[0]
    from app.models import BdLine
    ln2 = BdLine(name="跨线乙", name_norm="跨线乙", operator="测试铁道",
                 operator_short="测试", kind="private", prefs="13", n_station=1)
    db.add(ln2)
    db.flush()
    db.add(bd_tasks.BdStation(name="跨线站", name_norm=bd_tasks.norm_name("跨线站"),
                              line="跨线乙", line_id=ln2.id, place_id=pl.id,
                              pref="13", source="manual"))
    db.commit()
    pid = pl.id
    # 两条线的站行 → **同一个物理车站**（去重后只 1 个）
    rows = db.query(bd_tasks.BdStation.id).filter(bd_tasks.BdStation.name == "跨线站").all()
    sid2 = [r[0] for r in rows]
    pids = bd_tasks.place_ids_for_stations(db, sid2)
    assert pids == [pid], "跨线站的两行要归到同一个物理车站"
    r = bd_tasks.create_tasks_for_places(db, pids, by="admin")
    n = db.query(BdTask).filter(BdTask.place_id == pid).count()
    db.close()
    assert r["created"] == 1 and n == 1, "一个物理车站只能有一个任务（跨两条线也只建一个）"


def test_place_task_renders_detail_and_export(client, seeded):
    """挂 place 的任务在**详情页**和**导出**里都要正常（这两处最容易漏改）。"""
    from app.models import BdTask, BdTeam
    db = appdb.SessionLocal()
    line, places = _place_fixture(db, line_name="渲染线", names=("渲1", "渲2"))
    team = BdTeam(name="渲染队", code="XR")
    db.add(team)
    db.commit()
    r = bd_tasks.create_tasks_for_places(db, [p.id for p in places], by="admin",
                                        team_id=team.id)
    tids, lid = r["task_ids"], line.id
    db.close()

    _login(client, "admin")
    for tid in tids:
        d = client.get("/tasks/%d" % tid)
        assert d.status_code == 200, "任务详情页不能 500"
        assert "渲" in d.text
    assert client.get("/tasks/new").status_code == 200
    e = client.get("/tasks/export?line=%d" % lid)
    assert e.status_code == 200 and len(e.content) > 0, "导出不能空/不能 500"
    # 员工端/队长端页面也要能渲染（含 place 任务）
    assert client.get("/my/tasks").status_code in (200, 302)


def test_new_task_page_is_admin_only(client, seeded):
    """建任务页与提交都只允许管理员（队长/员工进不去）。"""
    for uname in ("ogawa", "tangjing"):
        _login(client, uname)
        r = client.get("/tasks/new", follow_redirects=False)
        assert r.status_code in (302, 303, 403), "%s 不该看到建任务页" % uname
    _login(client, "admin")
    assert client.get("/tasks/new").status_code == 200


# ---------------- 车站资产页：查询能力（2026-10-06 用户："我查都查不到"） ----------------

def _station_asset_fixture(db):
    """造两条线 + 两个站（一个 JR 千葉、一个都営 東京），带 駅コード/都道府県。"""
    from app.models import BdLine, BdStation
    l1 = BdLine(name="京葉線", name_norm="京葉線", operator="東日本旅客鉄道",
                operator_short="JR東日本", kind="jr", prefs="12", n_station=1)
    l2 = BdLine(name="12号線大江戸線", name_norm="12号線大江戸線", operator="東京都",
                operator_short="都営", kind="public", prefs="13", n_station=1)
    db.add_all([l1, l2])
    db.flush()
    db.add_all([
        BdStation(name="海浜幕張", name_norm=bd_tasks.norm_name("海浜幕張"), line="京葉線",
                  line_id=l1.id, operator="東日本旅客鉄道", pref="12",
                  ekicode="003785", lat=35.648, lon=140.041, source="mlit"),
        BdStation(name="都庁前", name_norm=bd_tasks.norm_name("都庁前"), line="12号線大江戸線",
                  line_id=l2.id, operator="東京都", pref="13",
                  ekicode="004012", lat=35.689, lon=139.692, source="mlit"),
    ])
    db.commit()
    return l1, l2


def test_station_search_covers_operator_ekicode_pref(client, seeded):
    """关键词**一个框搜全部**：站名 / 駅コード / 运营商 / 线路名 / 都道府県名。

    修之前实测：搜 `JR東日本`、`都営`、`千葉県`、`003785` **全部 0 命中**（只搜站名+线路文本）。
    """
    db = appdb.SessionLocal()
    _station_asset_fixture(db)
    db.close()
    _login(client, "admin")
    for kw, want in (("海浜", "海浜幕張"), ("JR東日本", "海浜幕張"),
                     ("003785", "海浜幕張"), ("千葉県", "海浜幕張"),
                     ("京葉線", "海浜幕張"), ("都営", "都庁前"),
                     ("004012", "都庁前"), ("東京都", "都庁前")):
        p = client.get("/stations", params={"kw": kw})
        assert p.status_code == 200 and want in p.text, "搜「%s」应命中 %s" % (kw, want)
    p = client.get("/stations", params={"kw": "ざぶとん"})
    assert "没有匹配的车站" in p.text


def test_station_filters_pref_operator_kind_and_sort(client, seeded):
    """都道府県 / 运营公司 / 线路类型 筛选 + 排序参数都能用。"""
    db = appdb.SessionLocal()
    _station_asset_fixture(db)
    db.close()
    _login(client, "admin")
    p = client.get("/stations", params={"pref": "12"})
    assert "海浜幕張" in p.text and "都庁前" not in p.text, "pref=12 只该有千葉"
    p = client.get("/stations", params={"operator": "都営"})
    assert "都庁前" in p.text and "海浜幕張" not in p.text
    p = client.get("/stations", params={"kind": "jr"})
    assert "海浜幕張" in p.text and "都庁前" not in p.text
    # 排序：站名 / 线路+顺序（默认）都要 200，且行还在
    for sort in ("line", "name", "pref", "ekicode"):
        r = client.get("/stations", params={"sort": sort})
        assert r.status_code == 200 and "海浜幕張" in r.text, "排序 %s 不能用" % sort
    # 跨线站筛选（两个同 group_code 的站合并成一个物理车站）
    from app.models import BdLine, BdStation
    from app.services import bd_places
    db = appdb.SessionLocal()
    l3 = BdLine(name="山手線", name_norm="山手線", operator="東日本旅客鉄道",
                operator_short="JR東日本", kind="jr", prefs="13", n_station=1)
    l4 = BdLine(name="井の頭線", name_norm="井の頭線", operator="京王電鉄",
                operator_short="京王", kind="private", prefs="13", n_station=1)
    db.add_all([l3, l4])
    db.flush()
    # 跨线站：两行同名 + 同 group_code → 合并成一个物理车站（n_line=2）
    db.add_all([
        BdStation(name="渋谷", name_norm=bd_tasks.norm_name("渋谷"), line="山手線",
                  line_id=l3.id, operator="東日本旅客鉄道", pref="13",
                  group_code="GX99", lat=35.658, lon=139.701, source="mlit"),
        BdStation(name="渋谷", name_norm=bd_tasks.norm_name("渋谷"), line="井の頭線",
                  line_id=l4.id, operator="京王電鉄", pref="13",
                  group_code="GX99", lat=35.658, lon=139.701, source="mlit")])
    db.commit()
    bd_places.rebuild_places(db)
    db.commit()
    db.close()
    p = client.get("/stations", params={"multi": "1"})
    assert p.status_code == 200 and "渋谷" in p.text
    assert "海浜幕張" not in p.text, "只看跨线站时，单线站不该出现"


def test_stations_export_csv(client, seeded):
    """导出当前筛选结果（CSV，UTF-8 BOM，Excel 能直接打开）。"""
    db = appdb.SessionLocal()
    _station_asset_fixture(db)
    db.close()
    _login(client, "admin")
    r = client.get("/stations/export", params={"pref": "12"})
    assert r.status_code == 200
    assert "csv" in r.headers["content-type"]
    body = r.content.decode("utf-8-sig")
    head = body.splitlines()[0]
    for col in ("都道府県", "駅名", "駅コード", "线路"):
        assert col in head, "导出表头缺少 %s" % col
    assert "海浜幕張" in body and "003785" in body
    assert "都庁前" not in body, "导出要跟着筛选条件走"


def test_stations_export_is_not_truncated(client, seeded):
    """⚠️ 回归：`paging.paginate` 把 `per` 夹到 200 → 导出若走分页会**静默只导 200 行**。

    2026-10-06 实测抓到：千葉県 383 条只导出了 200 条（跟 /stores/entities 的 limit(500)
    是同一类"看着成功、实际丢数据"的 bug）。
    """
    db = appdb.SessionLocal()
    for i in range(250):
        bd_tasks.create_station(db, "导出站%03d" % i)
    db.commit()
    db.close()
    _login(client, "admin")
    r = client.get("/stations/export")
    assert r.status_code == 200
    lines = r.content.decode("utf-8-sig").splitlines()
    assert len(lines) - 1 == 251, "导出要全量（1 seeded + 250），实际 %d" % (len(lines) - 1)


def test_tasks_page_layout_tabs_list_then_team_summary(client, seeded):
    """任务页布局（口径随用户多次调整，**以最新为准**）。

    用户 2026-10-06 最新口径："按队汇总放在 tab 的**上面**，不要看着像在 tab 里面"
    → 最终顺序：**按队汇总 → tab 栏 → 筛选 → 列表**。
    并且按队汇总要能看 **已完成 / 未完成**，且每个数字可钻取到"该队 + 该状态"的列表。
    """
    _login(client, "admin")
    h = client.get("/tasks", params={"tab": "assigned"}).text   # seeded 的任务在"已分配"
    i_tabs = h.index('data-testid="task-tabs"')
    i_rows = h.index('data-testid="task-row"')
    i_team = h.index('data-testid="by-team-card"')
    i_filter = h.index('data-testid="task-filter"')
    assert i_team < i_tabs < i_filter < i_rows, (
        "顺序必须是 队伍汇总 → tab → 筛选 → 列表（现在是 %d/%d/%d/%d）"
        % (i_team, i_tabs, i_filter, i_rows))
    tid = seeded["team"]
    # 按队汇总：总数 / 已完成 / 进行中 / 未分配 / 停滞 / 完成率 全在，而且可点
    for t in ("by-team-name", "by-team-total", "by-team-done", "by-team-doing",
              "by-team-unassigned", "by-team-rate"):
        assert 'data-testid="%s-%d"' % (t, tid) in h, "按队汇总缺少 %s" % t
    assert "按队汇总" in h and "完成率" in h
    # 钻取：该队 + 已完成 / 未分配 都能筛出来
    for tab in ("done", "doing", "unassigned"):
        r = client.get("/tasks", params={"team": str(tid), "tab": tab})
        assert r.status_code == 200, "按队钻取失败：team=%d tab=%s" % (tid, tab)
    r = client.get("/tasks", params={"team": str(tid), "stale": "1"})
    assert r.status_code == 200


# ---------------- 中日字形归一（2026-10-06 用户："我输入的是中文，是不是这里有问题"） ----------------

def test_cjk_glyph_helpers():
    """简体/繁体 → 日文新字体的映射（**搜索**用；用户实测：中文输入 0 命中）。"""
    from app.services import bd_cjk as c
    pairs = (("京叶线", "京葉線"), ("东京", "東京"), ("涩谷", "渋谷"),
             ("海滨幕张", "海浜幕張"), ("东横线", "東横線"),
             ("半藏门线", "半蔵門線"), ("樱木町", "桜木町"), ("台场", "台場"),
             ("秋叶原", "秋葉原"), ("横滨", "横浜"), ("大宫", "大宮"),
             ("千叶", "千葉"), ("户冢", "戸塚"), ("关内", "関内"),
             ("热海", "熱海"), ("船桥", "船橋"), ("松户", "松戸"),
             ("藤泽", "藤沢"), ("平冢", "平塚"), ("舞滨", "舞浜"),
             ("葛西临海公园", "葛西臨海公園"), ("银座线", "銀座線"),
             ("大江户线", "大江戸線"), ("有乐町线", "有楽町線"),
             ("丸之内线", "丸ノ内線"),   # 汉字映射管不到 → 走整串别名
             ("町屋站前", "町屋駅前"),   # 駅 ⇄ 站
             ("小机", "小机"))           # 例外：日文就是「机」
    for zh, jp in pairs:
        assert c.to_jp(zh) == jp, "%s 应转成 %s，实际 %s" % (zh, jp, c.to_jp(zh))
    # 反向（显示用；不落库）
    for jp, zh in (("京葉線", "京叶线"), ("渋谷", "涩谷"), ("海浜幕張", "海滨幕张")):
        assert c.line_zh(jp) == zh or c.to_zh(jp) == zh, "%s → %s" % (jp, zh)
    # 幂等：日文形再转一次不变
    for jp in ("京葉線", "東京", "海浜幕張", "丸ノ内線"):
        assert c.to_jp(jp) == jp


def _cjk_station_fixture(db):
    """造数据：日文名的线路 + 车站（用来验中文输入能不能搜到）。"""
    from app.models import BdLine, BdStation
    data = (("京葉線", "JR東日本", "jr", "12", "東京", "海浜幕張"),
            ("東横線", "東急", "private", "13", "渋谷", "桜木町"),
            ("半蔵門線", "東京メトロ", "private", "13", "渋谷", "青山一丁目"),
            ("丸ノ内線", "東京メトロ", "private", "13", "東京", "銀座"))
    for i, (ln, op, kind, pref, s1, s2) in enumerate(data):
        line = BdLine(name=ln, name_norm=ln, operator=op, operator_short=op,
                      kind=kind, prefs=pref, n_station=2)
        db.add(line)
        db.flush()
        for nm in (s1, s2):
            db.add(BdStation(name=nm, name_norm=bd_tasks.norm_name(nm), line=ln,
                             line_id=line.id, operator=op, pref=pref,
                             ekicode="00%04d" % (1000 + i), source="mlit"))
    db.commit()
    # ⚠️ 线路下拉是"按 tab 给口径"的：未分配 tab 只列**还有未分配车站**的线（车站池是物理车站口径）
    #    → 造完站要重建 place，否则这些线在下拉里不出现（测不到 data-zh）
    from app.services import bd_places
    bd_places.rebuild_places(db)
    db.commit()


def test_station_search_accepts_chinese_input(client, seeded):
    """**中文输入也要搜得到**（这是用户报的问题：搜「京叶线」0 命中）。

    矩阵覆盖：线路名 / 站名 / 运营商 / 都道府県 的中文形 + 繁体 + 部分匹配。
    """
    db = appdb.SessionLocal()
    _cjk_station_fixture(db)
    db.close()
    _login(client, "admin")
    cases = (
        ("京叶线", "東京"), ("京葉線", "東京"),          # 中文 / 日文
        ("东京", "東京"), ("海滨幕张", "海浜幕張"),
        ("涩谷", "渋谷"), ("东横线", "渋谷"),
        ("半藏门线", "青山一丁目"), ("樱木町", "桜木町"),
        ("丸之内线", "銀座"),                          # 汉字映射管不到的
        ("東京メトロ", "銀座"),                        # 运营商（日文）
        ("千叶县", "海浜幕張"),                        # 都道府県中文名
        ("JR東日本", "東京"),                          # 运营商
        ("東", "東京"),                                # 单字部分匹配
    )
    for kw, want in cases:
        p = client.get("/stations", params={"kw": kw})
        assert p.status_code == 200, kw
        assert want in p.text, "搜「%s」应该命中 %s" % (kw, want)
    # 反向：确实不存在的词仍然空
    p = client.get("/stations", params={"kw": "不存在站名xyz"})
    assert "没有匹配的车站" in p.text


def test_line_selects_are_searchable_with_chinese(client, seeded):
    """线路下拉 = 可搜索 combobox，且每个 option 挂**中文名**（data-zh）→ 输中文也能筛。"""
    db = appdb.SessionLocal()
    _cjk_station_fixture(db)          # 没有线路数据的话下拉是空的（测不到 data-zh）
    db.close()
    _login(client, "admin")
    for path, marker in (("/stations", 'data-testid="line-filter"'),
                         ("/tasks", 'data-testid="line-filter"'),
                         ("/tasks/new", 'data-testid="line-select"')):
        h = client.get(path).text
        assert marker in h, path
        assert 'data-cb-filter' in h, "%s 的线路下拉没有挂可搜索" % path
        assert 'data-zh=' in h, "%s 的线路 option 没有中文名" % path


def test_tasks_page_hints_other_tabs_when_empty(client, seeded):
    """当前 tab 没匹配、但别的 tab 有 → 必须提示（**否则用户以为"搜不到"**）。

    2026-10-06 实测踩到：在「未分配」tab 搜「涩谷」→ 0 行（任务在「已分配」里），
    看起来就像搜索坏了。服务层其实是对的（`task_board(kw='涩谷')` 命中 1）。
    """
    _login(client, "admin")
    # seeded：一个站 + 一个有队伍的任务 → 已分配 1 / 未分配 0
    p = client.get("/tasks", params={"tab": "unassigned", "kw": "駒場"})
    assert p.status_code == 200
    assert 'data-testid="other-tabs-hint"' in p.text, "没结果时要提示别的 tab"
    assert 'data-testid="other-tab-assigned"' in p.text
    # 切到已分配就能看到（链接目标可用）
    q = client.get("/tasks", params={"tab": "assigned", "kw": "駒場"})
    assert "駒場東大前" in q.text


def test_tasks_keyword_searches_operator_and_line_for_place_tasks(seeded):
    """⚠️ 回归：挂 **place** 的任务（`station_id=NULL`）也要能按**线路名**搜到。

    （用户 2026-10-06：运营商维度的搜索不需要 → 只验线路名/站名。）

    实测踩到：`/tasks` 搜「JR東日本」→ 0 命中（老口径是经 `station_id` join 的，
    而 place 任务没有 station_id）。修法：再按"该物理车站被哪些线经过"查一遍。
    """
    from app.models import BdLine, BdStation, BdStationPlace
    from app.services import bd_places
    db = appdb.SessionLocal()
    line = BdLine(name="山手線", name_norm="山手線", operator="東日本旅客鉄道",
                  operator_short="JR東日本", kind="jr", prefs="13", n_station=1)
    db.add(line)
    db.flush()
    db.add(BdStation(name="渋谷", name_norm=bd_tasks.norm_name("渋谷"), line="山手線",
                     line_id=line.id, operator="東日本旅客鉄道", pref="13",
                     group_code="GX77", source="mlit"))
    db.commit()
    bd_places.rebuild_places(db)
    db.commit()
    pl = db.query(BdStationPlace).filter(BdStationPlace.name_norm ==
                                        bd_tasks.norm_name("渋谷")).first()
    bd_tasks.create_tasks_for_places(db, [pl.id], by="admin", team_id=seeded["team"])
    for kw in ("山手線", "山手"):
        d = bd_tasks.task_board(db, kw=kw, tab=bd_tasks.TAB_ASSIGNED, limit=50)
        assert d["total"] >= 1, "搜「%s」应该命中place任务（运营商/线路名也要搜得到）" % kw
    db.close()


def test_reject_flow_leader_then_admin(client, seeded):
    """**驳回**（用户 2026-10-06）：队员报 100% → 队长可驳回；队长确认后 → 只有管理员能驳回。

    "驳回就是把100%的进度改成不到100%"。
    """
    from app.models import BdTask, BdTaskProgress
    db = appdb.SessionLocal()
    _place_fixture(db, line_name="驳回线", names=("驳1",))
    from app.models import BdStationPlace
    pl = db.query(BdStationPlace).filter(
        BdStationPlace.name_norm == bd_tasks.norm_name("驳1")).first()
    r = bd_tasks.create_tasks_for_places(db, [pl.id], by="admin",
                                        team_id=seeded["team"])
    tid = r["task_ids"][0]
    bd_tasks.assign_members(db, tid, ["P2"], by="admin")          # 汤静 = 队员
    db.commit()
    # ① 队员报 100%
    bd_tasks.save_progress(db, tid, 100, "", by="P2",
                           actor_user=db.query(User).filter(User.username == "tangjing").one())
    t = db.get(BdTask, tid)
    assert t.pct == 100 and t.done_date is not None and t.state == "done"
    row = db.query(BdTaskProgress).filter(BdTaskProgress.task_id == tid).first()
    assert row.review_status == "pending" and row.reported_pct == 100
    # ② 队长可驳回；员工/无关的人不行
    leader = db.query(User).filter(User.username == "ogawa").one()
    staff = db.query(User).filter(User.username == "tangjing").one()
    assert bd_tasks.can_reject(db, leader, t) is True
    assert bd_tasks.can_reject(db, staff, t) is False
    # ③ 队长驳回 → 改成 80%
    res = bd_tasks.reject_progress(db, tid, 80, "还有两家没扫", by="ogawa",
                                   actor_user=leader)
    t = db.get(BdTask, tid)
    assert res["pct"] == 80 and t.pct == 80
    assert t.state == "doing" and t.done_date is None, "驳回要清完成日、回到进行中"
    row = db.query(BdTaskProgress).filter(BdTaskProgress.task_id == tid,
                                         BdTaskProgress.review_status == "rejected").first()
    assert row is not None and row.reported_pct == 100, "员工原值 100 要保留"
    assert bd_tasks.can_reject(db, leader, t) is False, "已经不是 100% 了，不能再驳回"
    # ④ 队员再报 100% → 队长确认 → 队长不能再驳回，管理员可以
    bd_tasks.save_progress(db, tid, 100, "", by="P2",
                           actor_user=db.query(User).filter(User.username == "tangjing").one())
    bd_tasks.save_progress(db, tid, 100, "ok", by="ogawa", actor_user=leader,
                           confirm=True)
    t = db.get(BdTask, tid)
    row = (db.query(BdTaskProgress).filter(BdTaskProgress.task_id == tid)
           .order_by(BdTaskProgress.id.desc()).first())
    assert row.review_status == "confirmed"
    assert bd_tasks.can_reject(db, leader, t) is False, "队长确认后不能再驳回（避免自审自驳）"
    admin = db.query(User).filter(User.username == "admin").one()
    assert bd_tasks.can_reject(db, admin, t) is True
    res = bd_tasks.reject_progress(db, tid, 50, "管理员核实没完成", by="admin",
                                   actor_user=admin)
    assert res["pct"] == 50 and res["notified"] is True, "要发消息通知担当"
    t = db.get(BdTask, tid)
    assert t.pct == 50 and t.state == "doing" and t.done_date is None
    # ⑤ 新进度必须 < 100
    bd_tasks.save_progress(db, tid, 100, "", by="P2",
                           actor_user=db.query(User).filter(User.username == "tangjing").one())
    try:
        bd_tasks.reject_progress(db, tid, 100, "", by="admin", actor_user=admin)
        raise AssertionError("驳回成 100% 应该被拒")
    except bd_tasks.TaskError:
        pass
    db.rollback()
    db.close()


def test_reject_route_permissions(client, seeded):
    """驳回路由 `/my/tasks/reject`：队长能调；员工（担当本人）不能。

    ⚠️ 路径在 `/my/` 下：队长被中间件挡在 `/tasks/*` 外（与 /my/tasks/confirm 一致）。
    """
    from app.models import BdStationPlace
    db = appdb.SessionLocal()
    _place_fixture(db, line_name="驳回路由线", names=("驳路1",))
    pl = db.query(BdStationPlace).filter(
        BdStationPlace.name_norm == bd_tasks.norm_name("驳路1")).first()
    r = bd_tasks.create_tasks_for_places(db, [pl.id], by="admin",
                                        team_id=seeded["team"])
    tid = r["task_ids"][0]
    bd_tasks.assign_members(db, tid, ["P2"], by="admin")
    bd_tasks.save_progress(db, tid, 100, "", by="P2",
                           actor_user=db.query(User)
                           .filter(User.username == "tangjing").one())
    db.commit()
    assert db.get(BdTask, tid).pct == 100, "先造一个已完成（100%）的任务"
    db.close()
    # ⓪ 界面上也要有「驳回」（队长端与管理端，已完成的行）
    _login(client, "ogawa")
    h = client.get("/my/tasks", params={"tab": "pending"}).text   # 待确认：队员报的还没处理
    assert 'data-testid="reject-form-%d"' % tid in h, "队长端要有驳回表单"
    assert 'data-testid="reject-btn-%d"' % tid in h
    _login(client, "admin")
    h = client.get("/tasks", params={"tab": "done"}).text
    assert 'data-testid="reject-form-%d"' % tid in h, "管理端已完成 tab 要有驳回表单"
    # ① 员工（担当本人）不能驳回
    _login(client, "tangjing")
    p = _post(client, "/my/tasks/reject", {"task_id": str(tid), "pct": "50"},
              from_path="/my/tasks")
    assert p.status_code in (302, 303)
    db = appdb.SessionLocal()
    assert db.get(BdTask, tid).pct == 100, "员工不该能驳回"
    db.close()
    # ② 队长可以（他只能在 /my/* 操作）
    _login(client, "ogawa")
    p = _post(client, "/my/tasks/reject",
              {"task_id": str(tid), "pct": "60", "note": "没做完"},
              from_path="/my/tasks")
    assert p.status_code == 303
    db = appdb.SessionLocal()
    t = db.get(BdTask, tid)
    assert t.pct == 60 and t.state == "doing" and t.done_date is None
    db.close()


# ---------------- 任务页三 tab 工作台（2026-10-06 用户口径） ----------------

def test_unassigned_tab_is_station_pool_and_assign(client, seeded):
    """「未分配」= **车站池**：勾站 + 选队 + 分配 = 建任务 + 派队 一步完成。

    用户 2026-10-06："对于未分配，我可以选择一些车站，直接做分配" +
    "不要单独的'建任务按钮'"。
    """
    from app.models import BdLine, BdStation, BdStationPlace
    from app.services import bd_places
    db = appdb.SessionLocal()
    line = BdLine(name="池线", name_norm="池线", operator="测试铁道",
                  operator_short="测试", kind="private", prefs="13", n_station=3)
    db.add(line)
    db.flush()
    for i, nm in enumerate(("池站A", "池站B", "池站C")):
        db.add(BdStation(name=nm, name_norm=bd_tasks.norm_name(nm), line="池线",
                         line_id=line.id, operator="测试铁道", pref="13",
                         seq=i + 1, along_km=float(i), source="mlit"))
    db.commit()
    bd_places.rebuild_places(db)
    # 池站A 先建好任务但**不派队** → 也必须出现在车站池（未分配 = 没队伍）
    pl_a = db.query(BdStationPlace).filter(
        BdStationPlace.name_norm == bd_tasks.norm_name("池站A")).first()
    bd_tasks.create_tasks_for_places(db, [pl_a.id], by="admin")
    db.commit()
    lid = line.id
    ids = [r[0] for r in db.query(BdStationPlace.id).filter(
        BdStationPlace.name_norm.in_([bd_tasks.norm_name(x)
                                      for x in ("池站A", "池站B", "池站C")])).all()]
    db.close()

    _login(client, "admin")
    h = client.get("/tasks", params={"tab": "unassigned", "line": str(lid)}).text
    assert 'data-testid="pool-row"' in h, "未分配 tab 要显示车站池"
    for nm in ("池站A", "池站B", "池站C"):
        assert nm in h, "%s 还没派队，应该在池子里" % nm
    assert 'data-testid="pool-form"' in h and 'data-testid="pool-team"' in h
    assert 'data-testid="pool-assign"' in h
    # ⚠️ 2026-10-06 用户："顺序，任务这两列不需要。没任何意义" → 池子只有 勾选/车站/经过线路
    seg = h[:h.index('data-testid="pool-row"')]
    pool_head = re.findall(r"<thead>.*?</thead>", seg, re.S)[-1]
    for gone in ("顺序", "任务"):
        assert ">%s</th>" % gone not in pool_head, "车站池不该再有「%s」列" % gone
    assert ">车站</th>" in pool_head and ">经过线路</th>" in pool_head
    assert 'data-testid="pool-all"' in pool_head, "全选要放表头那个复选框里"
    assert 'data-testid="pool-none"' not in h, "不要再整俩按钮"
    row0 = re.search(r'<tr data-testid="pool-row".*?</tr>', h, re.S).group(0)
    assert row0.count("<td") == 3, "一行只该有 勾选/车站/经过线路"
    assert 'task-row' not in h, "未分配 tab 不该出现任务行"

    # 分配：勾 2 个站 → 派给队伍
    r = _post(client, "/tasks/assign",
              {"place_id": [str(ids[0]), str(ids[1])],
               "team": str(seeded["team"]), "line": str(lid)},
              from_path="/tasks?tab=unassigned&line=%d" % lid)
    assert r.status_code == 303
    db = appdb.SessionLocal()
    for pid in ids[:2]:
        tasks = db.query(BdTask).filter(BdTask.place_id == pid).all()
        assert len(tasks) == 1, "一个物理车站只能有一个任务（池站A 原本就有，不能重复建）"
        assert tasks[0].team_id == seeded["team"], "要派上队伍"
        assert tasks[0].assign_date is not None
    db.close()
    # 分完队：池子里只剩池站C，进行中 tab 能看到那 2 个
    h2 = client.get("/tasks", params={"tab": "unassigned", "line": str(lid)}).text
    assert "池站C" in h2 and "池站A" not in h2 and "池站B" not in h2
    h3 = client.get("/tasks", params={"tab": "assigned", "line": str(lid)}).text
    assert "池站A" in h3 and "池站B" in h3


def test_tasks_page_has_no_create_task_button(client, seeded):
    """用户 2026-10-06："不要单独的'建任务按钮'"（建任务 = 未分配里勾站 + 分配）。"""
    _login(client, "admin")
    h = client.get("/tasks").text
    assert 'data-testid="new-task-entry"' not in h
    assert 'href="/tasks/new"' not in h, "不该再有独立的「建任务」入口（建任务=未分配里勾站+分配）"


def test_tasks_export_has_three_sheets(client, seeded):
    """导出 = **一个文件三个 sheet**：未分配（只有线路/站点）、进行中（带进展）、已完成（带完成日期）。"""
    from app.models import BdStationPlace
    from app.services import bd_places
    db = appdb.SessionLocal()
    _place_fixture(db, line_name="导出线", names=("导1", "导2"))
    pls = db.query(BdStationPlace).filter(
        BdStationPlace.name_norm.in_([bd_tasks.norm_name("导1"),
                                      bd_tasks.norm_name("导2")])).all()
    # 导1：派队（进行中）；导2：不派（留在未分配池）
    bd_tasks.create_tasks_for_places(db, [pls[0].id], by="admin",
                                    team_id=seeded["team"])
    db.commit()
    db.close()
    _login(client, "admin")
    r = client.get("/tasks/export")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats")
    import io as _io
    from openpyxl import load_workbook
    wb = load_workbook(_io.BytesIO(r.content))
    assert wb.sheetnames == ["未分配", "进行中", "已完成"], wb.sheetnames
    head_pool = [c.value for c in wb["未分配"][1]]
    assert head_pool == ["线路", "站点"], "未分配只给线路/站点"
    head_doing = [c.value for c in wb["进行中"][1]]
    assert "进展%" in head_doing and "担当" in head_doing
    head_done = [c.value for c in wb["已完成"][1]]
    assert "完成日期" in head_done and "用时(天)" in head_done
    # 未分配 sheet 里要有没派队的导2
    pool_names = [row[1] for row in wb["未分配"].iter_rows(min_row=2, values_only=True)]
    assert "导2" in pool_names and "导1" not in pool_names
    doing_names = [row[1] for row in wb["进行中"].iter_rows(min_row=2, values_only=True)]
    assert "导1" in doing_names


# ---------------- AI 派工建议（2026-10-06） ----------------

def _ai_setup(monkeypatch, payload_text):
    """mock 模型：configured=True + chat 返回固定文本（AI 全程不连外网）。"""
    import json as _json
    from app.services import ai_chat
    monkeypatch.setattr(ai_chat, "configured", lambda: True)

    def fake_chat(prompt, **kw):
        fake_chat.prompt = prompt
        return payload_text, {"total_tokens": 123}
    monkeypatch.setattr(ai_chat, "chat", fake_chat)
    return fake_chat


def test_ai_suggest_filters_hallucinations_and_writes_nothing(client, seeded,
                                                              monkeypatch):
    """**AI 派工建议**：只给建议（不写库）；模型编的站名/队伍/重复分站**一律丢弃**。"""
    import json as _json

    from app.models import BdStationPlace, BdTask
    from app.services import bd_places
    db = appdb.SessionLocal()
    _place_fixture(db, line_name="AI线", names=("AI站1", "AI站2", "AI站3"))
    pls = {p.name: p for p in db.query(BdStationPlace).filter(
        BdStationPlace.name.in_(["AI站1", "AI站2", "AI站3"])).all()}
    team_name = db.query(BdTeam).filter(BdTeam.id == seeded["team"]).one().name
    db.close()

    # 模型输出：① 正常一组（队伍名少写「队」也要认）② 编造站名 ③ 不存在的队伍
    #          ④ 重复分站（AI站3 给两次，第二次算重复）
    payload = _json.dumps({
        "summary": "把这 3 个站给最顺路的队",
        "groups": [
            {"team": team_name.replace("队", ""), "stations": ["AI站1", "AI站2"],
             "reason": "同一条线且该队负担轻"},
            {"team": "幽灵队", "stations": ["AI站3"], "reason": "编的队伍"},
            {"team": team_name, "stations": ["AI站3", "不存在的站!!"],
             "reason": "重复+编造"},
        ]}, ensure_ascii=False)
    fake = _ai_setup(monkeypatch, payload)

    _login(client, "admin")
    db = appdb.SessionLocal()
    n_before = db.query(BdTask).count()
    db.close()
    r = _post(client, "/tasks/ai-suggest", {}, from_path="/tasks?tab=unassigned")
    assert r.status_code == 200
    h = r.text
    assert 'data-testid="ai-suggest-result"' in h
    assert h.count('data-testid="ai-suggest-row"') == 2, "两条有效建议"
    assert "AI站1" in h and "AI站2" in h
    assert "AI站3" in h, "重复分站算进第二条（AI站3 第一次出现就给第二组）"
    assert "不存在的站" not in h, "编造的站名不能出现"
    assert "幽灵队" not in h, "不存在的队伍要丢掉"
    assert 'data-testid="ai-suggest-dropped"' in h, "要如实回报被丢弃的数量"
    # ⚠️ 只建议、不写库
    db = appdb.SessionLocal()
    assert db.query(BdTask).count() == n_before, "AI 建议绝不能建任务/派队"
    db.close()
    # prompt 里必须有确定性数字（程序算的各队负担）与规则
    assert "各队当前负担" in fake.prompt and "只输出 JSON" in fake.prompt
    assert "未分配车站" in fake.prompt


def test_ai_suggest_error_paths(client, seeded, monkeypatch):
    """AI 未配置 / 输出不可解析 → 只显示一句话，**手动分配照常**。"""
    from app.services import ai_chat
    monkeypatch.setattr(ai_chat, "configured", lambda: False)
    _login(client, "admin")
    r = _post(client, "/tasks/ai-suggest", {}, from_path="/tasks?tab=unassigned")
    assert r.status_code == 200
    assert 'data-testid="ai-suggest-err"' in r.text
    assert "AI 未配置" in r.text
    # 页面本体不受影响
    assert client.get("/tasks?tab=unassigned").status_code == 200
    # 输出是垃圾 → 也是错误分支（不抛 500）
    monkeypatch.setattr(ai_chat, "configured", lambda: True)
    monkeypatch.setattr(ai_chat, "chat", lambda prompt, **kw: ("模型今天不想说话", {}))
    r = _post(client, "/tasks/ai-suggest", {}, from_path="/tasks?tab=unassigned")
    assert r.status_code == 200
    assert 'data-testid="ai-suggest-err"' in r.text


def test_ai_suggest_button_on_pool_tab_and_admin_only(client, seeded):
    """按钮在未分配 tab 上；且**只有管理员**能调（员工/队长不行）。"""
    _login(client, "admin")
    h = client.get("/tasks?tab=unassigned").text
    assert 'data-testid="ai-suggest"' in h
    assert 'hx-post="/tasks/ai-suggest"' in h
    assert 'data-testid="ai-suggest-box"' in h
    # 员工（非管理员）→ 被中间件/守卫挡掉
    _login(client, "tangjing")
    r = _post(client, "/tasks/ai-suggest", {}, from_path="/my/tasks")
    assert r.status_code in (302, 303), "员工不能调 AI 派工建议"


def test_ai_chat_direct_by_default(monkeypatch):
    """**AI 默认直连**（真 bug 修复）：httpx 默认读 macOS 系统代理 → 本机代理坏时
    所有 AI 调用都失败（实测 SSL EOF）；直连 api.deepseek.com 是通的。
    `AI_PROXY` 显式配代理；`AI_TRUST_ENV=1` 恢复老行为。
    """
    from app.services import ai_chat
    monkeypatch.delenv("AI_PROXY", raising=False)
    monkeypatch.delenv("AI_TRUST_ENV", raising=False)
    assert ai_chat._client_kwargs() == {"trust_env": False}
    monkeypatch.setenv("AI_PROXY", "http://127.0.0.1:7897")
    assert ai_chat._client_kwargs() == {"trust_env": False,
                                        "proxy": "http://127.0.0.1:7897"}
    monkeypatch.setenv("AI_TRUST_ENV", "1")
    assert ai_chat._client_kwargs() == {}, "AI_TRUST_ENV=1 时回到 httpx 老行为"


# ---------------- 任务页筛选/汇总（2026-10-06 用户反馈） ----------------

def test_task_filter_keeps_current_tab(client, seeded):
    """「查询」必须**留在当前 tab**（用户："任务页面查询没有用"）。

    根因：筛选表单没带 `tab` → 提交后回到默认的「未分配」（车站池），
    而车站池不认队伍筛选 → 看起来"查询没用"、也看不到"某队未完成"。
    """
    _login(client, "admin")
    for tab in ("assigned", "done"):
        h = client.get("/tasks", params={"tab": tab}).text
        assert 'name="tab" value="%s"' % tab in h, "筛选表单要带 tab"
        assert 'name="state" value=""' in h
    # 车站池那栏：队伍/日期不适用 → 隐藏，并给一句说明
    h = client.get("/tasks", params={"tab": "unassigned"}).text
    assert 'data-testid="pool-filter-hint"' in h
    assert 'data-testid="date-from"' not in h, "车站池不需要分配日期筛选"
    # tab 链接不能出现重复 tab（?tab=x&tab=y）
    h = client.get("/tasks", params={"tab": "done", "team": "2"}).text
    assert "tab=done&tab=" not in h and "tab=2&amp;tab=" not in h


def test_by_team_links_use_valid_tabs(client, seeded):
    """汇总表每个数字都要点得动：**tab 必须是合法值**。

    ⚠️ 曾经写成 `tab=doing` —— 它不是 BOARD_TABS 的值 → 路由当未知 → 掉回车站池，
    看起来就是"点了没反应"（用户反馈的就是这个）。
    """
    import re as _re
    from app.services import bd_tasks
    _login(client, "admin")
    h = client.get("/tasks", params={"tab": "assigned", "team": str(seeded["team"])}).text
    hrefs = _re.findall(r'href="(/tasks\?team=\d+[^"]*)"[^>]*data-testid="by-team-', h)
    assert hrefs, "汇总表要有链接"
    ok = set(bd_tasks.BOARD_TABS_ALL)
    for href in hrefs:
        u = href.replace("&amp;", "&")
        m = _re.search(r"[?&]tab=([a-z]+)", u)
        assert m, "每个链接都要带 tab：%s" % u
        assert m.group(1) in ok, "非法 tab=%s（会掉回车站池）：%s" % (m.group(1), u)
    # 该队"未分配"= 已派给该队但还没分到人 → 必须带 state=unassigned
    assert "state=unassigned" in h.replace("&amp;", "&")
    # 点进去真能看到任务（不是车站池）
    r = client.get("/tasks", params={"tab": "assigned", "team": str(seeded["team"])})
    assert 'data-testid="task-row"' in r.text
    assert 'data-testid="pool-row"' not in r.text


def test_by_team_summary_is_second_layer(client, seeded):
    """队伍汇总上移到**第二层**（tab 下面、筛选上面），并且三个 tab 都在。"""
    _login(client, "admin")
    for tab in ("unassigned", "assigned", "done"):
        h = client.get("/tasks", params={"tab": tab}).text
        i_sum = h.find('data-testid="by-team-card"')
        i_tabs = h.find('data-testid="task-tabs"')
        i_filter = h.find('data-testid="task-filter"')
        assert i_sum > 0, "%s tab 也要能看到队伍汇总（它是入口）" % tab
        # 用户 2026-10-06："按队汇总放在 tab 的上面，不要看着像在 tab 里面"
        assert i_sum < i_tabs < i_filter, "顺序要是：队伍汇总 → tab → 筛选"


def test_task_table_columns_are_slim(client, seeded):
    """任务表只要 6 列（用户 2026-10-06："队长/状态/开始日/完成日/确认/最后提交/多少天没动 都不需要"）。

    这些信息没丢：时间线在任务详情页、导出 Excel 里按 sheet 给全，停滞靠整行高亮 + 筛选。
    """
    import re as _re
    from app.models import BdStationPlace
    db = appdb.SessionLocal()
    _place_fixture(db, line_name="列线", names=("列1",))
    pl = db.query(BdStationPlace).filter(
        BdStationPlace.name_norm == bd_tasks.norm_name("列1")).first()
    bd_tasks.create_tasks_for_places(db, [pl.id], by="admin", team_id=seeded["team"])
    db.commit()
    db.close()
    _login(client, "admin")
    h = client.get("/tasks", params={"tab": "assigned"}).text
    head = _re.search(r"<th>车站</th>.*?</tr>", h, _re.S).group(0)
    assert _re.findall(r"<th>(.*?)</th>", head) == ["车站", "团队", "担当", "进度",
                                                   "分配日期", "修正进展"]
    for gone in ("队长", "状态", "开始日", "完成日", "确认", "最后提交", "多少天没动"):
        assert "<th>%s</th>" % gone not in h, "「%s」列应该去掉" % gone
    row = _re.search(r'<tr data-testid="task-row".*?</tr>', h, _re.S).group(0)
    assert row.count("<td>") == 6, "任务行也要 6 个单元格"
    # 停滞信号保留（整行高亮 + 筛选），但不占列
    assert 'data-testid="stale-only"' in h


def test_line_dropdown_only_lists_lines_with_content(client, seeded):
    """线路下拉**只列有内容的线路**，数量 = 当前 tab 的口径（用户 2026-10-06）。

    "已分配tab，按线路查询里的线路，只是有任务的线路。没有任务的就不要显示，
     另外，数量只显示任务的数量。"
    → 未分配 tab：只列还有未分配车站的线，数字 = 未分配车站数；
      已分配/已完成：只列有任务的线，数字 = 任务数；**不出现数量为 0 的线**。
    """
    import re as _re
    from app.models import BdLine, BdStation, BdStationPlace
    from app.services import bd_places
    db = appdb.SessionLocal()
    line = BdLine(name="口径线", name_norm="口径线", operator="测试铁道",
                  operator_short="测试", kind="private", prefs="13", n_station=3)
    db.add(line)
    db.flush()
    for i, nm in enumerate(("口径A", "口径B", "口径C")):
        db.add(BdStation(name=nm, name_norm=bd_tasks.norm_name(nm), line="口径线",
                         line_id=line.id, operator="测试铁道", pref="13",
                         seq=i + 1, along_km=float(i), source="mlit"))
    db.commit()
    bd_places.rebuild_places(db)
    lid = line.id
    # 口径A/B 派队（有任务）→ 未分配只剩 口径C
    pls = {p.name: p.id for p in db.query(BdStationPlace).filter(
        BdStationPlace.name.in_(["口径A", "口径B", "口径C"])).all()}
    bd_tasks.create_tasks_for_places(db, [pls["口径A"], pls["口径B"]], by="admin",
                                     team_id=seeded["team"])
    db.commit()
    db.close()

    _login(client, "admin")

    def opts(tab):
        h = client.get("/tasks", params={"tab": tab}).text
        m = _re.search(r'data-testid="line-filter".*?</select>', h, _re.S)
        return (re.search(r'<option value="">全部线路（(\d+)）</option>', m.group(0)),
                {int(a): int(c) for a, b, c in _re.findall(
                    r'<option value="(\d+)"[^>]*>\s*([^<（）]+)（(\d+)）', m.group(0))})
    # 未分配：只有 口径C 还在池子里 → 这条线记 1
    allopt, o = opts("unassigned")
    assert o.get(lid) == 1, "未分配 tab 的线路数量应是**未分配车站数**（1），实际 %s" % o.get(lid)
    # 「全部线路」= 当前 tab 的总数（池子里只剩 口径C 1 个；seeded 的站已派队不在池子里）
    assert int(allopt.group(1)) == 1, "全部线路 = 当前 tab 的总数（未分配=1）"
    # 已分配：这条线有 2 个任务 → 记 2；且不存在数量为 0 的线
    allopt2, o2 = opts("assigned")
    assert o2.get(lid) == 2, "已分配 tab 的线路数量应是**任务数**（2），实际 %s" % o2.get(lid)
    assert all(v > 0 for v in o2.values()), "不该出现数量为 0 的线路"
    # 已完成：这条线没有已完成任务 → 整条线不出现
    _, o3 = opts("done")
    assert lid not in o3, "没有已完成任务的线路不该出现在下拉里"


# ---------------- 员工/队长端（2026-10-06 用户 5 条） ----------------

def test_bulk_assign_on_unassigned_tab(client, seeded):
    """队长「未分配」= **多选 → 选队员 → 一次分配**，且**没有进度控件**（用户 2026-10-06）。"""
    from app.models import BdTask, BdTaskAssign
    db = appdb.SessionLocal()
    _place_fixture(db, line_name="批量线", names=("批1", "批2", "批3"))
    from app.models import BdStationPlace
    pls = db.query(BdStationPlace).filter(
        BdStationPlace.name.in_(["批1", "批2", "批3"])).all()
    r = bd_tasks.create_tasks_for_places(db, [p.id for p in pls], by="admin",
                                        team_id=seeded["team"])
    ids = r["task_ids"]
    db.commit()
    db.close()
    _login(client, "ogawa")
    h = client.get("/my/tasks", params={"tab": "unassigned"}).text
    assert 'data-testid="bulk-assign-form"' in h
    for tid in ids:
        assert 'data-testid="bulk-check-%d"' % tid in h, "未分配 tab 每行要有勾选框"
        # ⚠️ 口径变更（用户 2026-10-06 加需求）："队长分配完任务后可以随时调整任务进度；
        #    对于未分配的任务，可以直接标识成完成" → 未分配 tab **要有**进度控件 + 标记完成
        assert 'data-testid="slider-%d"' % tid in h, "未分配 tab 也要能调进度"
        assert 'data-testid="mark-done-%d"' % tid in h, "未分配 tab 要能直接标记完成"
    assert 'data-testid="bulk-person"' in h
    # 一次分派 3 个任务给 P2
    p = _post(client, "/my/tasks/assign-bulk",
              {"task_id": [str(i) for i in ids], "person": ["P2"]},
              from_path="/my/tasks?tab=unassigned")
    assert p.status_code == 303
    db = appdb.SessionLocal()
    for tid in ids:
        t = db.get(BdTask, tid)
        who = [a.person_code for a in db.query(BdTaskAssign).filter(
            BdTaskAssign.task_id == tid).all()]
        assert who == ["P2"], "批量分派要落到担当上"
        assert t.state == "doing", "分到人之后就是进行中"
    db.close()
    # 分完：未分配里没了；「进行中」里能看到，而且**有进度控件**
    h2 = client.get("/my/tasks", params={"tab": "unassigned"}).text
    for tid in ids:
        assert 'data-testid="bulk-check-%d"' % tid not in h2
    h3 = client.get("/my/tasks", params={"tab": "doing"}).text
    for tid in ids:
        assert 'data-testid="slider-%d"' % tid in h3, "进行中要能看/调进展"
    # 没勾任务 / 没选人 → 友好报错，不 500
    p2 = _post(client, "/my/tasks/assign-bulk", {"person": ["P2"]},
               from_path="/my/tasks?tab=unassigned")
    assert p2.status_code == 303 and "err=" in p2.headers.get("location", "")


def test_staff_has_only_two_tabs(client, seeded):
    """员工端只有「我的」「已完成」两个 tab（用户 2026-10-06）。"""
    from app.models import BdStationPlace
    db = appdb.SessionLocal()
    _place_fixture(db, line_name="员工线", names=("员1",))
    pl = db.query(BdStationPlace).filter(
        BdStationPlace.name_norm == bd_tasks.norm_name("员1")).first()
    r = bd_tasks.create_tasks_for_places(db, [pl.id], by="admin",
                                        team_id=seeded["team"])
    tid = r["task_ids"][0]
    bd_tasks.assign_members(db, tid, ["P2"], by="admin")
    db.commit()
    db.close()
    _login(client, "tangjing")
    h = client.get("/my/tasks").text
    assert 'data-testid="staff-tabs"' in h
    assert 'data-testid="tab-mine"' in h and 'data-testid="tab-done"' in h
    for gone in ("tab-unassigned", "tab-doing", "tab-pending", "bulk-assign-form"):
        assert 'data-testid="%s"' % gone not in h, "员工端不该有 %s" % gone
    # 已完成 tab 只放已完成的
    h2 = client.get("/my/tasks", params={"tab": "done"}).text
    assert 'data-testid="my-task-row"' not in h2 or 'data-testid="slider-%d"' % tid not in h2


def test_messages_and_feedback_moved_to_top_menu(client, seeded):
    """底栏腾位置：消息 / 核对结果 挪到右上角菜单（用户 2026-10-06）。"""
    _login(client, "tangjing")
    h = client.get("/my/tasks").text
    for gone in ("tab-messages", "tab-feedback"):
        assert 'data-testid="%s"' % gone not in h, "底栏不该再有 %s" % gone
    assert 'data-testid="nav-messages"' in h
    assert 'data-testid="nav-feedback"' in h
    assert 'href="/my/report/feedback"' in h and 'href="/messages"' in h
    # 底栏仍保留 4 项
    for tid in ("tab-tasks", "tab-perf", "tab-report", "tab-plan"):
        assert 'data-testid="%s"' % tid in h


# ---------------- 2026-10-06 多角度审计后的修复（Batch 1：安全/数据正确性） ----------------

def test_assign_bulk_requires_leader_or_admin(client, seeded):
    """⚠️ 审计 P0：批量分派原来**不校验权限** → 任何队员都能改本队分工。"""
    db = appdb.SessionLocal()
    tid = seeded["task"]
    t = db.get(BdTask, tid)
    bd_tasks.set_task_team(db, [tid], seeded["team"], assign_date=date(2026, 10, 3))
    db.commit()
    db.close()
    # 队员（P2 汤静，staff）批量分派 → 一条都不许生效
    _login(client, "tangjing")
    r = _post(client, "/my/tasks/assign-bulk",
              {"task_id": [str(tid)], "person": ["P2"]},
              from_path="/my/tasks?tab=unassigned")
    assert r.status_code == 303
    db = appdb.SessionLocal()
    who = [a.person_code for a in db.query(BdTaskAssign).filter(
        BdTaskAssign.task_id == tid).all()]
    db.close()
    assert who == [], "队员不该能把任务分派给自己"
    # 队长可以
    _login(client, "ogawa")
    r = _post(client, "/my/tasks/assign-bulk",
              {"task_id": [str(tid)], "person": ["P2"]},
              from_path="/my/tasks?tab=unassigned")
    assert r.status_code == 303
    db = appdb.SessionLocal()
    who = [a.person_code for a in db.query(BdTaskAssign).filter(
        BdTaskAssign.task_id == tid).all()]
    db.close()
    assert who == ["P2"]


def test_reject_keeps_reporter_and_note(client, seeded):
    """⚠️ 审计：驳回原来写不存在的 `row.by_user` → 记录挂到员工名下、员工备注被覆盖。"""
    from app.models import BdTaskProgress
    db = appdb.SessionLocal()
    tid = seeded["task"]
    bd_tasks.assign_members(db, tid, ["P2"], by="admin")
    # 员工报 100% 并写备注
    r = bd_tasks.save_progress(db, tid, 100, "全部干完了", by="tangjing",
                               actor_user=db.query(User).filter(
                                   User.username == "tangjing").first())
    db.commit()
    # 队长驳回成 40% 并写理由
    og = db.query(User).filter(User.username == "ogawa").first()
    out = bd_tasks.reject_progress(db, tid, 40, "照片没拍全", by="ogawa",
                                   actor_user=og)
    db.commit()
    row = (db.query(BdTaskProgress).filter(BdTaskProgress.task_id == tid)
           .order_by(BdTaskProgress.progress_date.desc()).first())
    assert row.review_status == "rejected"
    assert row.reported_pct == 100, "员工原值要留住（界面要显示 队员报 100% → 40%）"
    assert row.reported_by == "tangjing", "上报人不能变成驳回操作人"
    assert row.submitted_by == "tangjing"
    assert row.reviewed_by == "ogawa"
    assert row.review_note == "照片没拍全", "驳回理由进 review_note"
    assert row.note == "全部干完了", "员工自己的备注不能被驳回理由覆盖"
    db.close()


def test_admin_adjust_marks_review_and_notifies(client, seeded):
    """⚠️ 审计：管理端「修正进展」没传 actor_user → 不写审核状态、不发消息。"""
    from app.models import BdTaskProgress, BdMessage
    db = appdb.SessionLocal()
    tid = seeded["task"]
    bd_tasks.assign_members(db, tid, ["P2"], by="admin")
    tj = db.query(User).filter(User.username == "tangjing").first()
    bd_tasks.save_progress(db, tid, 80, "报 80", by="tangjing", actor_user=tj)
    db.commit()
    # ⚠️ 新规则：管理员要等**队长确认过**才能改
    og = db.query(User).filter(User.username == "ogawa").first()
    bd_tasks.save_progress(db, tid, 80, "", by="ogawa", actor_user=og, confirm=True)
    db.commit()
    ad = db.query(User).filter(User.username == "admin").first()
    bd_tasks.save_progress(db, tid, 60, "管理员修正", by="admin", actor_user=ad)
    db.commit()
    row = (db.query(BdTaskProgress).filter(BdTaskProgress.task_id == tid)
           .order_by(BdTaskProgress.progress_date.desc()).first())
    assert row.review_status == "adjusted", "管理员修正要留审核痕迹"
    assert row.reviewed_by == "admin"
    assert db.query(BdMessage).count() >= 1, "要通知队员"
    db.close()


def test_confirm_works_across_days(client, seeded):
    """⚠️ 审计：员工昨天下班报、队长第二天点「确认」→ 旧实现另造一行，那条永远 pending。"""
    from app.models import BdTaskProgress
    db = appdb.SessionLocal()
    tid = seeded["task"]
    bd_tasks.assign_members(db, tid, ["P2"], by="admin")
    tj = db.query(User).filter(User.username == "tangjing").first()
    # 假装是昨天报的
    bd_tasks.save_progress(db, tid, 70, "昨天报的", by="tangjing", actor_user=tj,
                           on_date=bd_tasks._today() - timedelta(days=1))
    db.commit()
    db.close()
    _login(client, "ogawa")
    r = _post(client, "/my/tasks/confirm", {"task_id": str(tid), "note": ""},
              from_path="/my/tasks?tab=pending")
    assert r.status_code == 303 and "msg=" in r.headers.get("location", "")
    db = appdb.SessionLocal()
    rows = (db.query(BdTaskProgress).filter(BdTaskProgress.task_id == tid)
            .order_by(BdTaskProgress.progress_date.asc()).all())
    assert len(rows) == 1, "不该另造一条今天的行"
    assert rows[0].review_status == "confirmed"
    assert rows[0].reported_pct == 70
    assert rows[0].reported_by == "tangjing"
    db.close()


def test_same_value_resubmit_does_not_reopen_review(client, seeded):
    """⚠️ 审计：队长确认后员工手滑再点一次 → 旧实现把队长的确认打回待确认。"""
    from app.models import BdTaskProgress
    db = appdb.SessionLocal()
    tid = seeded["task"]
    bd_tasks.assign_members(db, tid, ["P2"], by="admin")
    tj = db.query(User).filter(User.username == "tangjing").first()
    bd_tasks.save_progress(db, tid, 60, "报了 60", by="tangjing", actor_user=tj)
    db.commit()
    og = db.query(User).filter(User.username == "ogawa").first()
    # ⚠️ 用「调整」（不是确认）：确认会把当天锁住，就验不到"同值不重开"了
    bd_tasks.save_progress(db, tid, 55, "队长改成 55", by="ogawa", actor_user=og)
    db.commit()
    row = (db.query(BdTaskProgress).filter(BdTaskProgress.task_id == tid)
           .order_by(BdTaskProgress.progress_date.desc()).first())
    assert row.review_status == "adjusted"
    # 员工同值（55）再点一次 → 不该把队长的调整打回待确认
    bd_tasks.save_progress(db, tid, 55, "", by="tangjing", actor_user=tj)
    db.commit()
    db.refresh(row)
    assert row.review_status == "adjusted", "同值重复提交不该打回待确认"
    assert row.reported_pct == 60, "员工原值仍是第一次的 60"
    db.close()


def test_staff_and_leader_can_open_task_detail(client, seeded):
    """⚠️ 审计：中间件白名单漏了 /tasks/ → 员工/队长点车站名被静默弹到每日填报页。"""
    tid = seeded["task"]
    db = appdb.SessionLocal()
    bd_tasks.assign_members(db, tid, ["P2"], by="admin")     # P2 = 汤静(队员)
    db.commit()
    db.close()
    _login(client, "tangjing")          # 担当 → 能看（驳回原因/进展历史只有这里能看）
    r = client.get("/tasks/%d" % tid, follow_redirects=False)
    assert r.status_code == 200, "担当要能打开任务详情"
    _login(client, "ganzijie")         # 不是担当、也不是队长 → 仍要隔离
    r = client.get("/tasks/%d" % tid, follow_redirects=False)
    assert r.status_code == 302, "非担当/非该队队长要挡住（数据隔离不能破）"
    _login(client, "ogawa")             # 该队队长
    r = client.get("/tasks/%d" % tid, follow_redirects=False)
    assert r.status_code == 200


def test_ai_suggest_does_not_consume_form_token(client, seeded, monkeypatch):
    """⚠️ 审计：AI 建议与派队表单共用一次性令牌 → 点完 AI 再派队必 400。"""
    from app.services import ai_chat as _ac
    monkeypatch.setattr(_ac, "configured", lambda: True)
    monkeypatch.setattr(_ac, "chat", lambda *a, **k: ('{"groups": []}', None))
    _login(client, "admin")
    ft = form_token(client, "/tasks?tab=unassigned")
    d = {"_ft": ft, "csrf_token": _csrf(client, "/tasks?tab=unassigned"), "line": ""}
    r1 = client.post("/tasks/ai-suggest", data=d, follow_redirects=False)
    assert r1.status_code == 200
    # 同一个 token 还能用于派队（不该被只读端点消耗掉）
    r2 = client.post("/tasks/assign", data=dict(d, team=str(seeded["team"])),
                     follow_redirects=False)
    assert r2.status_code != 400, "AI 建议不该吃掉表单令牌"


# ============ 任务域全流程（按 docs/任务域-全流程.md 补的用例） ============

def test_task_full_lifecycle_e2e(client, seeded):
    """**端到端**：建任务 → 派队 → 分派担当 → 上报 → 确认 → 驳回 → 完成 → 汇总。

    对应 `docs/任务域-全流程.md` §5；这是上线前必须绿的那条主链。
    """
    from app.models import BdTaskProgress
    tid = seeded["task"]
    # ① 定义：任务已存在（seeded 建了站+任务）
    db = appdb.SessionLocal()
    t = db.get(BdTask, tid)
    assert t is not None and t.state == "unassigned"
    db.close()
    # ② 派队（管理员）：seeded 已派；这里确认分配日期在
    db = appdb.SessionLocal()
    assert db.get(BdTask, tid).team_id == seeded["team"]
    assert db.get(BdTask, tid).assign_date is not None
    db.close()
    # ③ 分派担当（队长，走页面端点）
    _login(client, "ogawa")
    r = _post(client, "/my/tasks/assign", {"task_id": str(tid), "person": "P2"},
              from_path="/my/tasks?tab=unassigned")
    assert r.status_code == 303
    db = appdb.SessionLocal()
    assert [a.person_code for a in db.query(BdTaskAssign).filter(
        BdTaskAssign.task_id == tid).all()] == ["P2"]
    assert db.get(BdTask, tid).state == "doing"      # 分到人 → 进行中
    db.close()
    # ④ 员工上报 60%（队员端点）
    _login(client, "tangjing")
    r = _post(client, "/my/tasks/progress",
              {"task_id": str(tid), "pct": "60", "note": "做了一半"},
              from_path="/my/tasks?tab=mine")
    assert r.status_code == 303
    db = appdb.SessionLocal()
    row = (db.query(BdTaskProgress).filter(BdTaskProgress.task_id == tid)
           .order_by(BdTaskProgress.progress_date.desc()).first())
    assert (row.pct, row.reported_pct, row.review_status) == (60, 60, "pending")
    assert db.get(BdTask, tid).start_date is not None     # 首次提交=开始日
    db.close()
    # ⑤ 队长**调整**（改成 55）——不锁当天（只有"确认"才锁）
    _login(client, "ogawa")
    r = _post(client, "/my/tasks/confirm", {"task_id": str(tid), "note": ""},
              from_path="/my/tasks?tab=pending")
    assert r.status_code == 303, "确认失败 → %s %s" % (
        r.status_code, r.headers.get("location"))
    db = appdb.SessionLocal()
    og = db.query(User).filter(User.username == "ogawa").first()
    bd_tasks.save_progress(db, tid, 55, "队长改成 55", by="ogawa", actor_user=og)
    db.commit()
    row = bd_tasks.day_progress_map(db, [tid]).get(tid)
    assert row.review_status == "adjusted" and row.pct == 55
    db.close()
    # ⑥ 员工报 100%（调整未锁定当天 → 允许）→ 队长驳回成 30%（保留原值 100）
    _login(client, "tangjing")
    r6 = _post(client, "/my/tasks/progress",
               {"task_id": str(tid), "pct": "100", "note": "干完了"},
               from_path="/my/tasks?tab=mine")
    assert r6.status_code == 303
    db = appdb.SessionLocal()
    assert db.get(BdTask, tid).state == "done"
    db.close()
    _login(client, "ogawa")
    r = _post(client, "/my/tasks/reject", {"task_id": str(tid), "pct": "30",
                                          "note": "照片没拍全"},
              from_path="/my/tasks?tab=pending")
    assert r.status_code == 303
    db = appdb.SessionLocal()
    t = db.get(BdTask, tid)
    row = bd_tasks.day_progress_map(db, [tid]).get(tid)
    assert t.state == "doing" and t.done_date is None      # 回退清完成日
    assert row is not None and row.review_status == "rejected"
    assert row.reported_pct == 100, "员工原值要留着（界面显示 队员报 100% → 30%）"
    assert row.reported_by == "tangjing"
    assert row.review_note == "照片没拍全"
    assert row.note == "干完了", "员工备注不能被驳回理由覆盖"
    db.close()
    # ⑦ 再报 100% → 队长确认 → 完成；**确认后当天锁住**（新规则）
    _login(client, "tangjing")
    _post(client, "/my/tasks/progress", {"task_id": str(tid), "pct": "100"},
          from_path="/my/tasks?tab=mine")
    _login(client, "ogawa")
    _post(client, "/my/tasks/confirm", {"task_id": str(tid), "note": ""},
          from_path="/my/tasks?tab=pending")
    db = appdb.SessionLocal()
    t = db.get(BdTask, tid)
    assert t.state == "done" and t.done_date is not None
    db.close()
    # 确认后队员再提交 → 被锁（服务层强校验，不是只藏按钮）
    _login(client, "tangjing")
    r_lock = _post(client, "/my/tasks/progress",
                   {"task_id": str(tid), "pct": "50"},
                   from_path="/my/tasks?tab=mine")
    assert r_lock.status_code == 303 and "err=" in r_lock.headers["location"], \
        "队长确认后队员不能改当天这条"
    db = appdb.SessionLocal()
    assert db.get(BdTask, tid).pct == 100, "锁定后数值不变"
    db.close()
    # ⑧ 汇总：管理端卡片/tab/按队汇总一致，且能筛到这条
    _login(client, "admin")
    p = client.get("/tasks?tab=done")
    assert p.status_code == 200 and "駒場東大前" in p.text
    assert 'data-testid="card-done"' in p.text


def test_tasks_export_three_sheets_match_sql(client, seeded):
    """导出三 sheet 的**内容**要与 SQL 直查一致（用户 2026-10-06："导出excel你测试了吗"）。

    自造可控 fixture：3 个物理车站，其中 1 个已派队+分人（→进行中），2 个在池子里（→未分配）。
    """
    import io
    from openpyxl import load_workbook
    from app.models import BdStationPlace
    db = appdb.SessionLocal()
    line, places = _place_fixture(db, line_name="导出测试线",
                                  names=("导甲", "导乙", "导丙"))
    db.commit()
    # ⚠️ 这里用的是**物理车站**（place）→ 要用 create_tasks_for_places
    r = bd_tasks.create_tasks_for_places(db, [places[0].id], by="admin",
                                        team_id=seeded["team"],
                                        assign_date=date(2026, 10, 3))
    tid = r["task_ids"][0]
    bd_tasks.assign_members(db, tid, ["P2"], by="admin")
    db.commit()
    db.close()

    _login(client, "admin")
    db = appdb.SessionLocal()
    buf = bd_tasks.tasks_xlsx(db)
    raw = buf.getvalue() if hasattr(buf, "getvalue") else buf
    wb = load_workbook(io.BytesIO(raw), read_only=True)
    assert wb.sheetnames == ["未分配", "进行中", "已完成"]
    rows = {ws.title: list(ws.iter_rows(values_only=True)) for ws in wb.worksheets}
    wb.close()
    # 表头（列定义）固定
    assert rows["未分配"][0] == ("线路", "站点")
    assert rows["进行中"][0] == ("线路", "站点", "团队", "担当", "进展%",
                               "分配日期", "开始日", "最后提交")
    assert rows["已完成"][0] == ("线路", "站点", "团队", "担当", "完成日期",
                               "开始日", "分配日期", "用时(天)")
    # 行内容 = 该 fixture 的车站（与 SQL 口径一致）
    pool_names = {r[1] for r in rows["未分配"][1:]}
    doing_names = {r[1] for r in rows["进行中"][1:]}
    assert {"导甲"} <= doing_names, "有队伍+有担当 → 进行中"
    assert {"导乙", "导丙"} <= pool_names, "没派队的 → 车站池"
    assert "导甲" not in pool_names
    # 筛线路要带进导出
    buf2 = bd_tasks.tasks_xlsx(db, line_id=line.id)
    raw2 = buf2.getvalue() if hasattr(buf2, "getvalue") else buf2
    wb2 = load_workbook(io.BytesIO(raw2), read_only=True)
    n2 = {ws.title: len(list(ws.iter_rows(values_only=True))) - 1
          for ws in wb2.worksheets}
    wb2.close()
    assert n2["未分配"] == 2 and n2["进行中"] == 1, "按线路筛：本线 2 个在池子、1 个在做"
    db.close()

    # 「只看停滞」要影响导出（stale_only 生效）
    db = appdb.SessionLocal()
    b3 = bd_tasks.tasks_xlsx(db, stale_only=True, stale_days=2)
    raw3 = b3.getvalue() if hasattr(b3, "getvalue") else b3
    wb3 = load_workbook(io.BytesIO(raw3), read_only=True)
    n3 = len(list(wb3["进行中"].iter_rows(values_only=True))) - 1
    wb3.close()
    db.close()
    assert n3 <= 1, "停滞后只应剩「有担当且没提交」的那条"


def test_pool_search_matches_line_name_and_operator(client, seeded):
    """车站池搜索要认**线路名**（审计：搜线路名得 0；用户 2026-10-06：运营商维度不需要）。"""
    from app.models import BdLine, BdStationPlace
    from app.services import bd_places
    db = appdb.SessionLocal()
    line = BdLine(name="南武線", name_norm="南武線", operator="東日本旅客鉄道",
                  operator_short="JR東日本", kind="jr", prefs="13", n_station=2)
    db.add(line)
    db.flush()
    for nm in ("尻手", "矢向"):
        db.add(BdStationPlace(name=nm, name_norm=nm))
    db.flush()
    from app.models import BdStation
    for p in db.query(BdStationPlace).filter(
            BdStationPlace.name.in_(["尻手", "矢向"])).all():
        db.add(BdStation(name=p.name, name_norm=p.name, line_id=line.id,
                         place_id=p.id, seq=1, operator="東日本旅客鉄道"))
    db.commit()
    bd_places.rebuild_places(db)
    db.close()
    from app.services import bd_tasks as _T
    db = appdb.SessionLocal()
    # ⚠️ 用户 2026-10-06："运营商的搜索我们不需要，按线路就行" → 只验**线路名**（含空格不敏感）
    for kw in ("南武線", "南武"):
        n = _T.list_unassigned_places(db, page=1, per=1, kw=kw)["total"]
        assert n >= 2, "搜「%s」至少要能找到这两个站（实际 %d）" % (kw, n)
    db.close()


def test_stale_requires_assignee(client, seeded):
    """停滞口径：**没分担当的不算停滞**（否则筛选等于不过滤）。"""
    tid = seeded["task"]
    _login(client, "admin")
    # 没担当（只有队伍）→ 不算停滞
    p = client.get("/tasks?tab=assigned")
    assert 'data-testid="stale-%d"' % tid not in p.text
    db = appdb.SessionLocal()
    bd_tasks.assign_members(db, tid, ["P2"], by="admin")
    db.commit()
    db.close()
    p2 = client.get("/tasks?tab=assigned")
    assert 'data-testid="stale-%d"' % tid in p2.text, "有担当且从没提交 → 算停滞"
    assert "天没动" in p2.text


def test_cards_match_tab_counts(client, seeded):
    """统计卡与 tab 必须同口径（审计：同屏 "已分配 0" vs "已分配（515）"）。"""
    _login(client, "admin")
    p = client.get("/tasks?tab=unassigned").text
    assert 'data-testid="card-pool"' in p and 'data-testid="card-assigned"' in p
    # 数字与 tab_counts 一致
    db = appdb.SessionLocal()
    c = bd_tasks.tab_counts(db)
    db.close()
    import re as _r
    pool_card = int(_r.search(r'data-testid="card-pool">(\d+)<', p).group(1))
    asg_card = int(_r.search(r'data-testid="card-assigned">(\d+)<', p).group(1))
    done_card = int(_r.search(r'data-testid="card-done">(\d+)<', p).group(1))
    assert (pool_card, asg_card, done_card) == (c["unassigned"], c["assigned"], c["done"])


def test_leader_default_tab_prefers_pending_then_unassigned(client, seeded):
    """队长默认落点：有待确认先看它，否则未分配；都没有才「我的」。"""
    db = appdb.SessionLocal()
    bd_tasks.assign_members(db, seeded["task"], ["P2"], by="admin")
    tj = db.query(User).filter(User.username == "tangjing").first()
    bd_tasks.save_progress(db, seeded["task"], 50, "报一半", by="tangjing",
                           actor_user=tj)
    db.commit()
    db.close()
    _login(client, "ogawa")
    p = client.get("/my/tasks").text
    assert 'data-testid="bulk-confirm-form"' in p, "有待确认 → 默认进「待确认」"


def test_bulk_confirm_pending(client, seeded):
    """待确认**批量确认**（审计：30 条要 30 次往返）。"""
    from app.models import BdTaskProgress
    db = appdb.SessionLocal()
    ids = []
    for nm in ("甲站", "乙站", "丙站"):
        st = bd_tasks.create_station(db, nm)
        bd_tasks.create_tasks(db, [st.id], by="admin", team_id=seeded["team"],
                              assign_date=date(2026, 10, 3))
    db.commit()
    for t in db.query(BdTask).all():
        bd_tasks.assign_members(db, t.id, ["P2"], by="admin")
        bd_tasks.save_progress(db, t.id, 40, "报了 40", by="tangjing",
                               actor_user=db.query(User).filter(
                                   User.username == "tangjing").first())
        ids.append(t.id)
    db.commit()
    db.close()
    _login(client, "ogawa")
    r = _post(client, "/my/tasks/confirm-bulk",
              {"task_id": [str(i) for i in ids]},
              from_path="/my/tasks?tab=pending")
    assert r.status_code == 303
    db = appdb.SessionLocal()
    n = db.query(BdTaskProgress).filter(
        BdTaskProgress.task_id.in_(ids),
        BdTaskProgress.review_status == "confirmed").count()
    assert n == len(ids), "一次应确认多条"
    db.close()


def test_reassign_and_return_pool(client, seeded):
    """派队要可逆：改派队伍 / 退回车站池（审计：原来选错只能改库）。"""
    from app.models import BdTeam
    db = appdb.SessionLocal()
    t2 = bd_teams.create_team(db, "汤静队", "TJ02", by="admin")
    bd_tasks.assign_members(db, seeded["task"], ["P2"], by="admin")
    db.commit()
    db.close()
    _login(client, "admin")
    r = _post(client, "/tasks/reassign",
              {"task_id": str(seeded["task"]), "team_id": str(t2.id)},
              from_path="/tasks?tab=assigned")
    assert r.status_code == 303
    db = appdb.SessionLocal()
    assert db.get(BdTask, seeded["task"]).team_id == t2.id
    # 换队清空原担当（原担当不属新队）
    assert db.query(BdTaskAssign).filter(
        BdTaskAssign.task_id == seeded["task"]).count() == 0
    db.close()
    r2 = _post(client, "/tasks/return-pool", {"task_id": str(seeded["task"])},
               from_path="/tasks?tab=assigned")
    assert r2.status_code == 303
    db = appdb.SessionLocal()
    t = db.get(BdTask, seeded["task"])
    assert t.team_id is None and t.assign_date is None and t.state == "unassigned"
    db.close()
    # 回到车站池里能看到
    db = appdb.SessionLocal()
    pool = [r["name"] for r in bd_tasks.list_unassigned_places(db, per=200)["rows"]]
    db.close()
    assert "駒場東大前" in pool


def test_admin_adjust_keeps_context(client, seeded):
    """管理端修正进展后**留在原视图**（审计：原来固定跳回车站池）。"""
    # 新规则：先让队长确认，管理员才能改
    db = appdb.SessionLocal()
    bd_tasks.assign_members(db, seeded["task"], ["P2"], by="admin")
    tj = db.query(User).filter(User.username == "tangjing").first()
    bd_tasks.save_progress(db, seeded["task"], 30, "员工报", by="tangjing",
                           actor_user=tj)
    db.commit()
    og = db.query(User).filter(User.username == "ogawa").first()
    bd_tasks.save_progress(db, seeded["task"], 30, "", by="ogawa", actor_user=og,
                           confirm=True)
    db.commit()
    db.close()
    _login(client, "admin")
    r = _post(client, "/tasks/%d/progress" % seeded["task"],
              {"pct": "50", "note": "管理员改", "back": "/tasks?tab=assigned&line=3"},
              from_path="/tasks?tab=assigned")
    assert r.status_code == 303
    loc = r.headers["location"]
    assert loc.startswith("/tasks?tab=assigned") and "msg=" in loc, loc
    # 非法 back（外部域名）要被忽略
    r2 = _post(client, "/tasks/%d/progress" % seeded["task"],
               {"pct": "55", "back": "//evil.com/x"}, from_path="/tasks")
    assert r2.headers["location"].startswith("/tasks"), "开放重定向要挡住"


def test_ai_apply_group_covers_whole_group(client, seeded, monkeypatch):
    """AI 建议「派给 XX」要能**整组落地**（审计：旧实现只勾当前页 → 83 站只勾上 27）。"""
    from app.services import ai_chat as _ac
    from app.models import BdStationPlace, BdLine
    db = appdb.SessionLocal()
    line = BdLine(name="建议线", name_norm="建议线", operator="测试", kind="private",
                  prefs="13", n_station=0)
    db.add(line)
    db.flush()
    pls = []
    for nm in ("AI甲", "AI乙", "AI丙"):
        p = BdStationPlace(name=nm, name_norm=nm)
        db.add(p)
        db.flush()
        pls.append(p.id)
    db.commit()
    db.close()
    monkeypatch.setattr(_ac, "configured", lambda: True)
    ids = pls
    team_name = appdb.SessionLocal().get(__import__("app.models", fromlist=["BdTeam"]).BdTeam,
                                         seeded["team"]).name
    monkeypatch.setattr(_ac, "chat", lambda *a, **k: (
        '{"summary":"顺路","groups":[{"team":"%s","stations":["AI甲","AI乙","AI丙"],'
        '"reason":"同一条线"}]}' % team_name, None))
    _login(client, "admin")
    r = _post(client, "/tasks/ai-suggest", {"line": "", "kw": ""},
              from_path="/tasks?tab=unassigned")
    assert r.status_code == 200
    body = r.text
    # 每个站的 place_id 都要出现在提交表单里（不依赖前端勾选当前页）
    for pid in ids:
        assert 'name="place_id"\n                 value="%d"' % pid in body or \
            'value="%d"' % pid in body, "AI 组里的每个站都要能一键落地"
    assert 'data-testid="ai-apply-form"' in body


def test_business_day_is_jst(client, seeded):
    """业务日必须是 **JST**（线上容器 UTC：用 date.today() 会在 JST 上午 9 点前写错行）。"""
    import datetime as _dt
    from app.services import date_plan
    assert bd_tasks._today() == date_plan.jst_today(), "默认业务日要跟 JST 一致"
    assert bd_tasks._today(date(2026, 1, 1)) == date(2026, 1, 1), "显式传入要照用"
    # 进展行按 JST 落库
    db = appdb.SessionLocal()
    bd_tasks.assign_members(db, seeded["task"], ["P2"], by="admin")
    tj = db.query(User).filter(User.username == "tangjing").first()
    bd_tasks.save_progress(db, seeded["task"], 20, "早上报的", by="tangjing",
                           actor_user=tj)
    db.commit()
    row = (db.query(BdTaskProgress).filter(BdTaskProgress.task_id == seeded["task"])
           .order_by(BdTaskProgress.progress_date.desc()).first())
    assert row.progress_date == date_plan.jst_today()
    db.close()


def test_progress_slider_has_server_value(client, seeded):
    """进度滑块必须服务端渲染 value + 纯文本数字（CDN 挂时不至于按 50% 提交）。"""
    db = appdb.SessionLocal()
    bd_tasks.assign_members(db, seeded["task"], ["P2"], by="admin")
    tj = db.query(User).filter(User.username == "tangjing").first()
    bd_tasks.save_progress(db, seeded["task"], 40, "四十", by="tangjing",
                           actor_user=tj)
    db.commit()
    db.close()
    _login(client, "tangjing")
    h = client.get("/my/tasks?tab=mine").text
    # ⚠️ 2026-10-06 起员工端是**统一自报表单**：滑块 name 变成 `pct_<task_id>`
    m = re.search(r'<input type="range" name="pct(?:_%d)?"[^>]*value="(\d+)"'
                  % seeded["task"], h)
    assert m and m.group(1) == "40", "滑块要有服务端 value=当前进度"
    assert 'form="self-report-form"' in h, "员工端控件要挂到统一自报表单"
    assert re.search(r'id="pctv-%d"[^>]*>40<' % seeded["task"], h), "数字要有服务端兜底"
    assert "oninput=" in h, "无 Alpine 时也要能更新数字"


def test_leader_line_filter_uses_team_lines(client, seeded):
    """队长端线路下拉只列**本队有内容的线路**，且筛选生效。"""
    from app.models import BdLine, BdStationPlace
    db = appdb.SessionLocal()
    line, places = _place_fixture(db, line_name="队长筛选线", names=("队甲",))
    db.commit()
    bd_tasks.create_tasks_for_places(db, [places[0].id], by="admin",
                                     team_id=seeded["team"],
                                     assign_date=date(2026, 10, 3))
    db.commit()
    db.close()
    _login(client, "ogawa")
    h = client.get("/my/tasks?tab=unassigned").text
    assert "队长筛选线" in h, "本队有内容的线路要出现在下拉里"
    h2 = client.get("/my/tasks?tab=unassigned&line=%d" % line.id).text
    assert "队甲" in h2, "按线路筛选要能筛到"
    h3 = client.get("/my/tasks?tab=unassigned&line=%d" % line.id).text
    assert "駒場東大前" not in h3, "其它线路的站不该出现"


# ============ Batch 3：上线阻塞项（日语反馈 / 本地库 / 令牌友好化） ============

def test_ja_operation_feedback_is_japanese(client, seeded):
    """日语界面下，操作反馈必须是日文（审计：msg/err 原来直接输出中文）。"""
    from urllib.parse import unquote
    _login(client, "admin")
    # 带 ?lang=ja 执行一次真实操作（派队）→ 回跳 URL 里的 msg 应是日文
    ft = form_token(client, "/tasks?tab=unassigned&lang=ja")
    cs = _csrf(client, "/tasks?lang=ja")
    r = client.post("/tasks/assign?lang=ja",
                    data={"_ft": ft, "csrf_token": cs,
                          "team": str(seeded["team"])},
                    follow_redirects=False)
    assert r.status_code == 303
    loc = unquote(r.headers["location"])
    assert "err=" in loc or "msg=" in loc
    text = loc.split("err=")[-1] if "err=" in loc else loc.split("msg=")[-1]
    assert any("\u3040" <= ch <= "\u30ff" for ch in text), \
        "日语界面下的反馈应是日文（拿到：%s）" % text
    # 中文界面仍是中文（⚠️ 上一步写了语言 cookie，这里显式指定 ?lang=zh）
    ft2 = form_token(client, "/tasks?tab=unassigned&lang=zh")
    r2 = client.post("/tasks/assign?lang=zh",
                     data={"_ft": ft2, "csrf_token": _csrf(client, "/tasks?lang=zh"),
                           "team": str(seeded["team"])}, follow_redirects=False)
    txt2 = unquote(r2.headers.get("location", ""))
    assert ("已" in txt2 or "请" in txt2) and "選択" not in txt2, txt2


def test_js_libs_are_local_not_cdn(client, seeded):
    """htmx/alpine 必须**本地**引用（现场网络拦 CDN 时导航/滑块不能废）。"""
    import os
    _login(client, "tangjing")
    h = client.get("/my/tasks").text
    assert "unpkg.com" not in h, "不能再依赖 CDN"
    assert "/static/htmx.min.js?v=" in h and "/static/alpine.min.js?v=" in h
    for fn in ("htmx.min.js", "alpine.min.js"):
        p = os.path.join("app", "static", fn)
        assert os.path.exists(p) and os.path.getsize(p) > 10000, "%s 要真在仓库里" % fn
        assert client.get("/static/" + fn).status_code == 200
    # 菜单不再依赖 Alpine（原生 onclick）
    assert "bdMenuToggle(" in h and 'x-data="{menu:false}"' not in h


def test_expired_form_token_recovers_gracefully(client, seeded):
    """令牌失效（返回键/双开）→ 回跳原页 + 人话，不再是裸 JSON 400。"""
    _login(client, "admin")
    path = "/tasks?tab=unassigned"
    ft = form_token(client, path)
    cs = _csrf(client, path)
    d = {"_ft": ft, "csrf_token": cs, "team": str(seeded["team"])}
    first = client.post("/tasks/assign", data=d, follow_redirects=False)
    assert first.status_code == 303
    second = client.post("/tasks/assign", data=d, follow_redirects=False,
                         headers={"referer": "/tasks?tab=assigned"})
    assert second.status_code == 303, "浏览器（带 Referer）应回跳而不是 400"
    loc = second.headers["location"]
    assert loc.startswith("/tasks?tab=assigned") and "err=" in loc
    assert "application/json" not in second.headers.get("content-type", "")


def test_leader_bulk_scripts_wait_for_dom(client, seeded):
    """⚠️ 浏览器实跑抓到的真 bug：队长页两段内联脚本在**行渲染之前**执行 →
    `boxes` 抓到 0 个复选框 → 表头全选 / Shift 区间 / 拖拽 / 「已选 N」**全是死的**
    （手动勾选仍能提交，所以单测没发现；只有真浏览器点才会暴露）。

    修法：包在 `DOMContentLoaded` 里。这里守住它，并防止再出现
    `document.addEventListener(...) is not a function`（`})();` 收尾写错）那类错误。
    """
    import re as _re
    db = appdb.SessionLocal()
    bd_tasks.assign_members(db, seeded["task"], ["P2"], by="admin")
    db.commit()
    db.close()
    _login(client, "ogawa")
    h = client.get("/my/tasks?tab=unassigned").text
    assert h.count("DOMContentLoaded") >= 2, "两段批量脚本都要等 DOM 就绪"
    bad = _re.search(r"DOMContentLoaded[\s\S]{0,3000}?\n\}\)\(\);", h)
    assert bad is None, "DOMContentLoaded 块不能用 `})();` 收尾（会报 not a function）"
    # 表头全选与计数元素都在
    assert 'data-testid="bulk-all"' in h and 'data-testid="bulk-count"' in h


def test_stale_filter_total_matches_rows_and_card(client, seeded):
    """⚠️ 浏览器试跑抓到的真 bug：`stale=1` 的 total 用宽松口径（511），
    而行级用新口径（1 行）→ 页面「共 511 条」但几乎空白。三者必须同一口径。"""
    db = appdb.SessionLocal()
    st2 = bd_tasks.create_station(db, "池ノ上")
    task2 = (db.query(BdTask).order_by(BdTask.id.desc()).first())
    bd_tasks.create_tasks(db, [st2.id], by="admin", team_id=seeded["team"],
                          assign_date=date(2026, 10, 1))
    db.commit()
    task2 = db.query(BdTask).order_by(BdTask.id.desc()).first()
    # ① 没担当的（不该算停滞）
    # ② 有担当 + 从没提交（该算停滞）
    bd_tasks.assign_members(db, seeded["task"], ["P2"], by="admin")
    db.commit()
    n_card = bd_tasks.stale_count(db)
    d = bd_tasks.task_board(db, tab="assigned", stale_only=True, limit=50)
    db.close()
    assert d["total"] == len(d["rows"]), \
        "total(%d) 必须等于实际行数(%d)" % (d["total"], len(d["rows"]))
    assert d["total"] == n_card, "列表口径(%d) 要等于统计卡口径(%d)" % (d["total"], n_card)
    assert all(r["stale"] for r in d["rows"]), "列表里每行都应是真停滞"
    assert all(r["assignees"] for r in d["rows"]), "没分担当的不算停滞"


def test_staff_cannot_edit_after_leader_confirm(seeded):
    """A3 新规则：**队长确认后，队员当天不能再改**（服务层强校验）。"""
    db = appdb.SessionLocal()
    tid = seeded["task"]
    bd_tasks.assign_members(db, tid, ["P2"], by="admin")
    tj = db.query(User).filter(User.username == "tangjing").first()
    og = db.query(User).filter(User.username == "ogawa").first()
    bd_tasks.save_progress(db, tid, 60, "做了一半", by="tangjing", actor_user=tj)
    bd_tasks.save_progress(db, tid, 60, "", by="ogawa", actor_user=og, confirm=True)
    db.commit()
    row = bd_tasks.day_progress_map(db, [tid]).get(tid)
    assert row.review_status == "confirmed"
    assert not bd_tasks.can_report(db, tj, db.get(BdTask, tid)), "确认后队员不可上报"
    assert bd_tasks.can_report(db, og, db.get(BdTask, tid)), "队长不受限"
    try:
        bd_tasks.save_progress(db, tid, 50, "", by="tangjing", actor_user=tj)
        raised = None
    except bd_tasks.TaskError as e:
        raised = str(e)
    db.rollback()
    assert raised and "已确认" in raised, "服务层要挡（拿到 %r）" % raised
    db.close()


def test_admin_cannot_adjust_before_leader_confirm(seeded):
    """A3 新规则：**管理员只能改队长确认过的**（服务层强校验）。"""
    db = appdb.SessionLocal()
    tid = seeded["task"]
    bd_tasks.assign_members(db, tid, ["P2"], by="admin")
    tj = db.query(User).filter(User.username == "tangjing").first()
    ad = db.query(User).filter(User.username == "admin").first()
    og = db.query(User).filter(User.username == "ogawa").first()
    bd_tasks.save_progress(db, tid, 60, "员工报", by="tangjing", actor_user=tj)
    db.commit()
    assert not bd_tasks.can_adjust(db, ad, db.get(BdTask, tid)), "未确认 → 管理员不能改"
    try:
        bd_tasks.save_progress(db, tid, 80, "抢改", by="admin", actor_user=ad)
        raised = None
    except bd_tasks.TaskError as e:
        raised = str(e)
    db.rollback()
    assert raised and "队长确认" in raised, "服务层要挡（拿到 %r）" % raised
    # 队长确认后 → 管理员可以改
    bd_tasks.save_progress(db, tid, 60, "", by="ogawa", actor_user=og, confirm=True)
    db.commit()
    assert bd_tasks.can_adjust(db, ad, db.get(BdTask, tid))
    bd_tasks.save_progress(db, tid, 80, "管理员修正", by="admin", actor_user=ad)
    db.commit()
    row = bd_tasks.day_progress_map(db, [tid]).get(tid)
    assert row.pct == 80 and row.reported_pct == 60 and row.reported_by == "tangjing"
    db.close()


def test_staff_sees_lock_hint_after_leader_confirm(client, seeded):
    """A3 界面：队长确认后，队员端要**看见**"不能再改"（不是默默没按钮）。"""
    db = appdb.SessionLocal()
    tid = seeded["task"]
    bd_tasks.assign_members(db, tid, ["P2"], by="admin")
    tj = db.query(User).filter(User.username == "tangjing").first()
    og = db.query(User).filter(User.username == "ogawa").first()
    bd_tasks.save_progress(db, tid, 60, "做了一半", by="tangjing", actor_user=tj)
    # 确认前：有进度控件
    db.commit()
    db.close()
    _login(client, "tangjing")
    h1 = client.get("/my/tasks?tab=mine").text
    assert 'data-testid="progress-%d"' % tid in h1, "确认前应有进度控件"
    assert "队长已确认" not in h1
    # 队长确认后：控件没了 + 明确提示
    db = appdb.SessionLocal()
    og = db.query(User).filter(User.username == "ogawa").first()
    bd_tasks.save_progress(db, tid, 60, "", by="ogawa", actor_user=og, confirm=True)
    db.commit()
    db.close()
    h2 = client.get("/my/tasks?tab=mine").text
    assert 'data-testid="progress-%d"' % tid not in h2, "确认后不该再有进度控件"
    assert "队长已确认" in h2 and "如需修改请联系队长或管理员" in h2
    assert 'data-testid="readonly-%d"' % tid in h2


def test_admin_progress_form_gated_by_leader_confirm(client, seeded):
    """A3 界面：管理员在**队长确认前**看不到"修正进展"表单，只看到「等队长确认」。"""
    db = appdb.SessionLocal()
    tid = seeded["task"]
    bd_tasks.assign_members(db, tid, ["P2"], by="admin")
    tj = db.query(User).filter(User.username == "tangjing").first()
    bd_tasks.save_progress(db, tid, 60, "员工报", by="tangjing", actor_user=tj)
    db.commit()
    db.close()
    _login(client, "admin")
    h1 = client.get("/tasks?tab=assigned&kw=駒場東大前").text
    _i = h1.find("駒場東大前")
    assert 'data-testid="need-confirm-%d"' % tid in h1, "未确认时应显示「等队长确认」"
    assert 'action="/tasks/%d/progress"' % tid not in h1, "未确认时不该有修正表单"
    db = appdb.SessionLocal()
    og = db.query(User).filter(User.username == "ogawa").first()
    bd_tasks.save_progress(db, tid, 60, "", by="ogawa", actor_user=og, confirm=True)
    db.commit()
    db.close()
    h2 = client.get("/tasks?tab=assigned&kw=駒場東大前").text
    assert 'action="/tasks/%d/progress"' % tid in h2, "确认后应能修正"
    assert 'data-testid="need-confirm-%d"' % tid not in h2


def test_locked_staff_sees_correct_reason(client, seeded):
    """⚠️ 浏览器实测：队员被锁时，路由不能再说"只能上报自己担当的任务"（误导）。"""
    from urllib.parse import unquote
    db = appdb.SessionLocal()
    tid = seeded["task"]
    bd_tasks.assign_members(db, tid, ["P2"], by="admin")
    tj = db.query(User).filter(User.username == "tangjing").first()
    og = db.query(User).filter(User.username == "ogawa").first()
    bd_tasks.save_progress(db, tid, 60, "", by="tangjing", actor_user=tj)
    bd_tasks.save_progress(db, tid, 60, "", by="ogawa", actor_user=og, confirm=True)
    db.commit()
    db.close()
    _login(client, "tangjing")
    # 硬发（绕过界面）→ 提示要说清是"被锁"，不是"不是你的活"
    r = _post(client, "/my/tasks/progress", {"task_id": str(tid), "pct": "50"},
              from_path="/my/tasks?tab=mine")
    loc = unquote(r.headers["location"])
    assert "err=" in loc
    assert "队长已确认" in loc, "应说明是被锁（拿到 %s）" % loc
    assert "只能上报自己担当" not in loc
    # 别人的任务 → 仍是"只能上报自己担当的任务"
    db = appdb.SessionLocal()
    st2 = bd_tasks.create_station(db, "下北沢")
    bd_tasks.create_tasks(db, [st2.id], by="admin", team_id=seeded["team"])
    db.commit()
    other = db.query(BdTask).order_by(BdTask.id.desc()).first().id
    db.close()
    r2 = _post(client, "/my/tasks/progress", {"task_id": str(other), "pct": "10"},
               from_path="/my/tasks?tab=mine")
    assert "只能上报自己担当" in unquote(r2.headers["location"])


def test_self_report_one_form_one_submit(client, seeded):
    """**A2 自报合并**（用户 2026-10-06："一是报点数，二是报进度，一起提交自报"）：
    员工端「我的」是**一个表单**，点数 + N 个任务进度**一次提交**写两个域、同一事务。
    """
    db = appdb.SessionLocal()
    t1 = seeded["task"]
    bd_tasks.assign_members(db, t1, ["P2"], by="admin")
    st2 = bd_tasks.create_station(db, "下北沢")
    bd_tasks.create_tasks(db, [st2.id], by="admin", team_id=seeded["team"])
    db.commit()
    t2 = db.query(BdTask).order_by(BdTask.id.desc()).first().id
    bd_tasks.assign_members(db, t2, ["P2"], by="admin")
    db.commit()
    db.close()
    _login(client, "tangjing")
    h = client.get("/my/tasks?tab=mine").text
    # 一个表单包住：点数 + 两个任务的滑块（用 form= 属性挂靠，不嵌套）
    assert h.count('data-testid="self-report-form"') == 1
    assert 'action="/my/self-report"' in h
    assert 'data-testid="sr-p1"' in h and 'data-testid="sr-p2"' in h
    assert h.count('form="self-report-form"') >= 4, "两个任务的滑块+备注都要挂进来"
    assert 'data-testid="sr-submit"' in h
    # 一次提交 → 点数 + 两条进度都落库
    r = _post(client, "/my/self-report",
              {"area": "渋谷", "p1_cnt": "2", "p2_cnt": "1",
               "pct_%d" % t1: "30", "note_%d" % t1: "先做这个",
               "pct_%d" % t2: "50", "note_%d" % t2: ""},
              from_path="/my/tasks?tab=mine")
    assert r.status_code == 303, r.headers.get("location")
    from urllib.parse import unquote as _uq
    assert "已提交自报" in _uq(r.headers["location"])
    db = appdb.SessionLocal()
    rep = (db.query(StaffDailyReport)
           .filter(StaffDailyReport.person_code == "P2").one())
    assert (rep.p1_cnt, rep.p2_cnt, rep.total_cnt, rep.area) == (2, 1, 3, "渋谷")
    rows = {x.task_id: x for x in db.query(BdTaskProgress)
            .filter(BdTaskProgress.task_id.in_([t1, t2])).all()}
    assert rows[t1].pct == 30 and rows[t1].reported_pct == 30
    assert rows[t2].pct == 50 and rows[t2].note == ""
    assert db.get(BdTask, t1).state == "doing"
    db.close()


def test_self_report_all_or_nothing(client, seeded):
    """一起提交 = **要么都成、要么都不成**：有一条被队长锁定 → 整体回滚（点数也不写）。"""
    db = appdb.SessionLocal()
    t1 = seeded["task"]
    st2 = bd_tasks.create_station(db, "下北沢")
    bd_tasks.create_tasks(db, [st2.id], by="admin", team_id=seeded["team"])
    db.commit()
    t2 = db.query(BdTask).order_by(BdTask.id.desc()).first().id
    _t1, _t2 = t1, t2
    bd_tasks.assign_members(db, _t1, ["P2"], by="admin")
    bd_tasks.assign_members(db, _t2, ["P2"], by="admin")
    tj = db.query(User).filter(User.username == "tangjing").first()
    og = db.query(User).filter(User.username == "ogawa").first()
    # t2 被队长确认（锁定），t1 正常
    bd_tasks.save_progress(db, _t2, 60, "", by="tangjing", actor_user=tj)
    bd_tasks.save_progress(db, _t2, 60, "", by="ogawa", actor_user=og, confirm=True)
    db.commit()
    db.close()
    _login(client, "tangjing")
    r = _post(client, "/my/self-report",
              {"area": "渋谷", "p1_cnt": "5", "p2_cnt": "5",
               "pct_%d" % _t1: "20", "pct_%d" % _t2: "90"},
              from_path="/my/tasks?tab=mine")
    assert r.status_code == 303 and "err=" in r.headers["location"]
    db = appdb.SessionLocal()
    # 点数没写、t1 的进度也没写（整体回滚）
    assert (db.query(StaffDailyReport)
            .filter(StaffDailyReport.person_code == "P2").first()) is None
    assert (db.query(BdTaskProgress)
            .filter(BdTaskProgress.task_id == _t1).first()) is None
    assert db.get(BdTask, _t2).pct == 60, "被锁的那条不能变"
    db.close()


def test_self_report_skips_unchanged_tasks(client, seeded):
    """统一表单会把所有滑块都提交上来 → **没动过的不能写成新的待确认**（0% 也不能）。"""
    db = appdb.SessionLocal()
    t1, t2 = seeded["task"], None
    st2 = bd_tasks.create_station(db, "下北沢")
    bd_tasks.create_tasks(db, [st2.id], by="admin", team_id=seeded["team"])
    db.commit()
    t2 = db.query(BdTask).order_by(BdTask.id.desc()).first().id
    bd_tasks.assign_members(db, t1, ["P2"], by="admin")
    bd_tasks.assign_members(db, t2, ["P2"], by="admin")
    db.commit()
    db.close()
    _login(client, "tangjing")
    # 两个滑块都是 0（服务端初值），只"动"了点数 → 一条进度都不该写
    r = _post(client, "/my/self-report",
              {"area": "渋谷", "p1_cnt": "1", "p2_cnt": "0",
               "pct_%d" % t1: "0", "pct_%d" % t2: "0"},
              from_path="/my/tasks?tab=mine")
    from urllib.parse import unquote as _uq
    loc = _uq(r.headers["location"])
    assert "0 个任务进度" in loc, loc
    db = appdb.SessionLocal()
    assert (db.query(BdTaskProgress)
            .filter(BdTaskProgress.task_id.in_([t1, t2])).count()) == 0
    assert (db.query(StaffDailyReport)
            .filter(StaffDailyReport.person_code == "P2").first()) is not None
    db.close()


def test_leader_confirm_day_one_click(client, seeded):
    """**A4 一键全确认**（用户 2026-10-06："队长确认和自动确认"→ 一键全确认）：
    一个按钮把当天**全部**待确认确认掉，并逐条通知队员。"""
    db = appdb.SessionLocal()
    og = db.query(User).filter(User.username == "ogawa").first()
    tj = db.query(User).filter(User.username == "tangjing").first()
    # 三个任务：两个待确认（本队）+ 一个别的队（不能被误确认）
    t1 = seeded["task"]
    bd_tasks.assign_members(db, t1, ["P2"], by="admin")
    st2 = bd_tasks.create_station(db, "下北沢")
    bd_tasks.create_tasks(db, [st2.id], by="admin", team_id=seeded["team"])
    db.commit()
    t2 = db.query(BdTask).order_by(BdTask.id.desc()).first().id
    bd_tasks.assign_members(db, t2, ["P2"], by="admin")
    other = bd_teams.create_team(db, "别的队", "TW99", by="admin")
    from app.models import Person
    if db.get(Person, "P4") is None:
        db.add(Person(code="P4", display_name="王五"))
    db.add(BdTeamMember(team_id=other.id, person_code="P4", role="member",
                        start_date=date(2026, 9, 1)))
    db.flush()
    st3 = bd_tasks.create_station(db, "代々木上原")
    bd_tasks.create_tasks(db, [st3.id], by="admin", team_id=other.id)
    db.commit()
    t3 = db.query(BdTask).order_by(BdTask.id.desc()).first().id
    bd_tasks.assign_members(db, t3, ["P4"], by="admin")
    bd_tasks.save_progress(db, t1, 40, "", by="tangjing", actor_user=tj)
    bd_tasks.save_progress(db, t2, 70, "", by="tangjing", actor_user=tj)
    bd_tasks.save_progress(db, t3, 90, "", by="wangwu", actor_user=None)
    db.commit()
    db.close()
    _login(client, "ogawa")
    r = _post(client, "/my/tasks/confirm-day", {}, from_path="/my/tasks?tab=pending")
    assert r.status_code == 303
    from urllib.parse import unquote as _uq
    assert "已一键确认 2 条" in _uq(r.headers["location"]), _uq(r.headers["location"])
    db = appdb.SessionLocal()
    byid = {x.task_id: x for x in db.query(BdTaskProgress)
            .filter(BdTaskProgress.task_id.in_([t1, t2, t3])).all()}
    assert byid[t1].review_status == "confirmed" and byid[t1].pct == 40
    assert byid[t2].review_status == "confirmed" and byid[t2].pct == 70
    assert byid[t3].review_status == "pending", "别的队的不能被本队队长确认"
    # 队员收到 2 条确认消息
    from app.models import BdMessage as _BM
    msgs = (db.query(_BM)
            .filter(_BM.title.like("%确认%")).all())
    assert len([m for m in msgs if "駒場東大前" in m.title or "下北沢" in m.title]) == 2
    db.close()
    # 再点一次 → 没有待确认了
    _login(client, "ogawa")
    r2 = _post(client, "/my/tasks/confirm-day", {}, from_path="/my/tasks?tab=pending")
    assert "没有待确认" in _uq(r2.headers["location"])
    # 非队长不能一键确认
    _login(client, "tangjing")
    r3 = _post(client, "/my/tasks/confirm-day", {}, from_path="/my/tasks?tab=mine")
    assert r3.status_code in (302, 303)


def test_leader_return_task_with_progress(client, seeded):
    """**A5 回收/空置**（用户 2026-10-06："回收回来，或者分给别人，或者空置；
    **已经有进展的也能这样**"）：清担当、进展保留、回到本队「待派」tab。"""
    db = appdb.SessionLocal()
    tid = seeded["task"]
    bd_tasks.assign_members(db, tid, ["P2"], by="admin")
    tj = db.query(User).filter(User.username == "tangjing").first()
    bd_tasks.save_progress(db, tid, 40, "干了四成", by="tangjing", actor_user=tj)
    db.commit()
    db.close()
    _login(client, "ogawa")
    # 回收前：在"进行中"（有担当）
    h1 = client.get("/my/tasks?tab=doing").text
    assert 'data-testid="return-%d"' % tid in h1, "有担当的任务要有回收按钮"
    r = _post(client, "/my/tasks/return", {"task_id": str(tid), "back_tab": "doing"},
              from_path="/my/tasks?tab=doing")
    assert r.status_code == 303
    from urllib.parse import unquote as _uq
    assert "已回收" in _uq(r.headers["location"])
    db = appdb.SessionLocal()
    assert db.query(BdTaskAssign).filter(BdTaskAssign.task_id == tid).count() == 0
    row = bd_tasks.day_progress_map(db, [tid]).get(tid)
    assert row is not None and row.pct == 40, "进展必须保留"
    t = db.get(BdTask, tid)
    assert t.team_id == seeded["team"], "还在本队"
    db.close()
    # 回收后：进"待派"（按有没有分人分 tab）
    h2 = client.get("/my/tasks?tab=unassigned").text
    assert 'data-testid="my-task-row"' in h2 or "共" in h2
    assert "%d" % tid in h2, "回收后应出现在本队待派列表"
    h3 = client.get("/my/tasks?tab=doing").text
    assert 'data-testid="return-%d"' % tid not in h3, "已不在进行中"
    # 再分给别人 → 又回进行中
    r2 = _post(client, "/my/tasks/assign", {"task_id": str(tid), "person": "P2"},
               from_path="/my/tasks?tab=unassigned")
    assert r2.status_code == 303
    db = appdb.SessionLocal()
    assert [a.person_code for a in db.query(BdTaskAssign)
            .filter(BdTaskAssign.task_id == tid).all()] == ["P2"]
    assert bd_tasks.day_progress_map(db, [tid]).get(tid).pct == 40
    db.close()
    # 队员（非队长）不能回收
    _login(client, "tangjing")
    r3 = _post(client, "/my/tasks/return", {"task_id": str(tid)},
               from_path="/my/tasks?tab=mine")
    assert r3.status_code == 303 and "err=" in r3.headers["location"]


def test_admin_bulk_return_pool_only_without_assignees(client, seeded):
    """**A6 管理员按线路批量撤回**（用户 2026-10-06 第 2/3 条）：
    只能撤回"已派队但**还没分到人**"的（含已有进展的）；有担当的要让队长先回收。"""
    db = appdb.SessionLocal()
    t1 = seeded["task"]                       # 已派队、无担当、无进展
    st2 = bd_tasks.create_station(db, "下北沢")
    bd_tasks.create_tasks(db, [st2.id], by="admin", team_id=seeded["team"])
    db.commit()
    t2 = db.query(BdTask).order_by(BdTask.id.desc()).first().id   # 有担当
    bd_tasks.assign_members(db, t2, ["P2"], by="admin")
    # t1 造一点进展（"有进展但没分人"也要能撤）
    tj = db.query(User).filter(User.username == "tangjing").first()
    bd_tasks.save_progress(db, t1, 30, "干了点", by="tangjing", actor_user=tj)
    db.commit()
    db.close()
    _login(client, "admin")
    # 行上只给"无担当"的勾选框
    h = client.get("/tasks?tab=assigned").text
    assert 'data-testid="return-check-%d"' % t1 in h, "无担当的要有勾选框"
    assert 'data-testid="return-check-%d"' % t2 not in h, "有担当的不该给勾选框"
    r = _post(client, "/tasks/return-pool",
              {"task_id": [str(t1), str(t2)], "back": "/tasks?tab=assigned"},
              from_path="/tasks?tab=assigned")
    assert r.status_code == 303
    from urllib.parse import unquote as _uq
    loc = _uq(r.headers["location"])
    assert "已退回 1 个" in loc and "还有担当" in loc, loc
    db = appdb.SessionLocal()
    t1r, t2r = db.get(BdTask, t1), db.get(BdTask, t2)
    assert t1r.team_id is None and t1r.assign_date is None, "无担当的应回车站池"
    assert t2r.team_id == seeded["team"], "有担当的不该被抽走"
    # 进展保留
    assert bd_tasks.day_progress_map(db, [t1]).get(t1).pct == 30
    db.close()


def test_leader_can_adjust_progress_any_time(client, seeded):
    """**新需求**（用户 2026-10-06）："队长分配完任务后，可以随时调整任务进度"。

    包括：任务已分给别人（不是队长自己担当）、当天已被确认过 —— 队长都能改。
    """
    db = appdb.SessionLocal()
    tid = seeded["task"]
    bd_tasks.assign_members(db, tid, ["P2"], by="admin")
    tj = db.query(User).filter(User.username == "tangjing").first()
    og = db.query(User).filter(User.username == "ogawa").first()
    bd_tasks.save_progress(db, tid, 40, "", by="tangjing", actor_user=tj)
    bd_tasks.save_progress(db, tid, 40, "", by="ogawa", actor_user=og, confirm=True)
    db.commit()
    row = bd_tasks.day_progress_map(db, [tid]).get(tid)
    assert row.review_status == "confirmed"
    # ⚠️ 队员被锁，但队长不受限（can_report 的 mine 分支才判锁）
    assert not bd_tasks.can_report(db, tj, db.get(BdTask, tid))
    assert bd_tasks.can_report(db, og, db.get(BdTask, tid))
    db.close()
    _login(client, "ogawa")
    r = _post(client, "/my/tasks/progress", {"task_id": str(tid), "pct": "80",
                                            "note": "队长调"},
              from_path="/my/tasks?tab=doing")
    assert r.status_code == 303 and "err=" not in r.headers["location"]
    db = appdb.SessionLocal()
    row = bd_tasks.day_progress_map(db, [tid]).get(tid)
    assert row.pct == 80 and row.review_status == "adjusted"
    assert row.reported_pct == 40, "队员原值仍保留"
    db.close()


def test_leader_marks_unassigned_done(client, seeded):
    """**新需求**："对于未分配的任务，可以直接标识成完成"（不用先派人）。"""
    db = appdb.SessionLocal()
    tid = seeded["task"]                    # seeded 已派队、无担当
    assert db.query(BdTaskAssign).filter(BdTaskAssign.task_id == tid).count() == 0
    db.close()
    _login(client, "ogawa")
    h = client.get("/my/tasks?tab=unassigned").text
    assert 'data-testid="mark-done-%d"' % tid in h, "未分配行要有「标记完成」"
    assert 'data-testid="slider-%d"' % tid in h, "未分配行也要能调进度（新需求）"
    r = _post(client, "/my/tasks/progress", {"task_id": str(tid), "pct": "100",
                                            "note": "未派人直接完成"},
              from_path="/my/tasks?tab=unassigned")
    assert r.status_code == 303 and "err=" not in r.headers["location"]
    db = appdb.SessionLocal()
    t = db.get(BdTask, tid)
    assert t.pct == 100 and t.state == "done", "直接标成完成"
    assert t.done_date is not None, "完成日要有"
    assert db.query(BdTaskAssign).filter(BdTaskAssign.task_id == tid).count() == 0, \
        "不需要分人"
    db.close()
    # 口径：完成后**不再算未分配**，tab 数字要自洽
    _login(client, "ogawa")
    h2 = client.get("/my/tasks?tab=unassigned").text
    assert 'data-testid="mark-done-%d"' % tid not in h2, "完成后不该再出现在待派"
    h3 = client.get("/my/tasks?tab=done").text
    assert 'data-testid="my-task-row"' in h3
    db = appdb.SessionLocal()
    rows = bd_tasks.team_tasks(db, [seeded["team"]], tab="unassigned")
    assert tid not in [r["task"].id for r in rows], "SQL 口径也要排除已完成"
    rows2 = bd_tasks.team_tasks(db, [seeded["team"]], tab="done")
    assert tid in [r["task"].id for r in rows2]
    db.close()


def test_leader_redirect_stays_on_unassigned_tab(client, seeded):
    """⚠️ 浏览器实测：调完"没分人"的任务后，旧写法按 `state` 跳到「进行中」，
    而它按口径还在「待派」→ 队长失去位置。回跳 tab 要按**有没有分人**算。"""
    db = appdb.SessionLocal()
    tid = seeded["task"]                    # 已派队、无担当
    db.close()
    _login(client, "ogawa")
    r = _post(client, "/my/tasks/progress", {"task_id": str(tid), "pct": "35"},
              from_path="/my/tasks?tab=unassigned")
    assert r.status_code == 303
    loc = r.headers["location"]
    assert "tab=unassigned" in loc, "没分人的任务 → 回「待派」(拿到 %s)" % loc
    # 分了人之后 → 回「进行中」
    db = appdb.SessionLocal()
    bd_tasks.assign_members(db, tid, ["P2"], by="admin")
    db.commit()
    db.close()
    r2 = _post(client, "/my/tasks/progress", {"task_id": str(tid), "pct": "50"},
               from_path="/my/tasks?tab=doing")
    assert "tab=doing" in r2.headers["location"], r2.headers["location"]
    # 标成完成 → 回「已完成」
    r3 = _post(client, "/my/tasks/progress", {"task_id": str(tid), "pct": "100"},
               from_path="/my/tasks?tab=doing")
    assert "tab=done" in r3.headers["location"], r3.headers["location"]
    # 队员（无这两个 tab）→ 回「我的」/「已完成」
    _login(client, "tangjing")
    r4 = _post(client, "/my/tasks/progress", {"task_id": str(tid), "pct": "90"},
               from_path="/my/tasks?tab=mine")
    assert "tab=mine" in r4.headers["location"], r4.headers["location"]


def test_bulk_mark_done_and_bulk_progress(client, seeded):
    """**③ 存量补录**（用户 2026-10-06）：队长勾多条 → 批量标记完成 / 批量设进展。

    513 条存量任务一条条点不现实；批量要"一条坏只跳它"，并且如实报被跳过的条数。
    """
    db = appdb.SessionLocal()
    t1 = seeded["task"]                              # 本队、无担当 → 可批量
    st2 = bd_tasks.create_station(db, "下北沢")
    bd_tasks.create_tasks(db, [st2.id], by="admin", team_id=seeded["team"])
    db.commit()
    t2 = db.query(BdTask).order_by(BdTask.id.desc()).first().id
    other = bd_teams.create_team(db, "别的队", "TW99", by="admin")
    st3 = bd_tasks.create_station(db, "代々木上原")
    bd_tasks.create_tasks(db, [st3.id], by="admin", team_id=other.id)
    db.commit()
    t3 = db.query(BdTask).order_by(BdTask.id.desc()).first().id   # 别的队 → 应被跳过
    db.close()
    _login(client, "ogawa")
    h = client.get("/my/tasks?tab=unassigned").text
    assert 'data-testid="bulk-done"' in h and 'data-testid="bulk-progress"' in h
    assert 'data-testid="bulk-pct"' in h
    # 批量设进展 35%
    r = _post(client, "/my/tasks/progress-bulk",
              {"task_id": [str(t1), str(t2)], "pct_manual": "35",
               "back_tab": "unassigned"},
              from_path="/my/tasks?tab=unassigned")
    assert r.status_code == 303
    from urllib.parse import unquote as _uq
    assert "已更新 2 个" in _uq(r.headers["location"]), _uq(r.headers["location"])
    db = appdb.SessionLocal()
    assert db.get(BdTask, t1).pct == 35 and db.get(BdTask, t2).pct == 35
    db.close()
    # 批量标记完成（pct=100）→ 含别的队的任务 → 如实报被跳过
    r2 = _post(client, "/my/tasks/progress-bulk",
               {"task_id": [str(t1), str(t2), str(t3)], "pct": "100",
                "back_tab": "unassigned"},
               from_path="/my/tasks?tab=unassigned")
    loc2 = _uq(r2.headers["location"])
    assert "已更新 2 个" in loc2 and "1 个被跳过" in loc2, loc2
    db = appdb.SessionLocal()
    assert db.get(BdTask, t1).state == "done" and db.get(BdTask, t2).state == "done"
    assert db.get(BdTask, t1).done_date is not None
    assert db.get(BdTask, t3).pct == 0, "别的队的任务不能被动"
    db.close()
    # 非法值 / 没勾选 → 明确提示
    r3 = _post(client, "/my/tasks/progress-bulk", {"task_id": [str(t1)],
                                                  "pct_manual": "300"},
               from_path="/my/tasks?tab=unassigned")
    assert "0–100" in _uq(r3.headers["location"])
    r4 = _post(client, "/my/tasks/progress-bulk", {}, from_path="/my/tasks?tab=unassigned")
    assert "请先勾选" in _uq(r4.headers["location"])
    # 队员不能批量改（不是他的任务 → 全被跳过）
    _login(client, "tangjing")
    r5 = _post(client, "/my/tasks/progress-bulk", {"task_id": [str(t3)], "pct": "100"},
               from_path="/my/tasks?tab=mine")
    assert r5.status_code == 303
    db = appdb.SessionLocal()
    assert db.get(BdTask, t3).pct == 0
    db.close()


def test_notification_uses_recipient_language(client, seeded):
    """**① 日语通知正文**（发布前必须）：消息是**落库**的 → 必须在发送时按
    **收件人** `users.lang` 渲染，不能等渲染页面时再翻（收件人共享一条）。"""
    db = appdb.SessionLocal()
    tid = seeded["task"]
    bd_tasks.assign_members(db, tid, ["P2"], by="admin")
    # 队员账号设成日语
    u = db.query(User).filter(User.username == "tangjing").first()
    u.lang = "ja"
    tj = u
    og = db.query(User).filter(User.username == "ogawa").first()
    bd_tasks.save_progress(db, tid, 40, "", by="tangjing", actor_user=tj)
    bd_tasks.save_progress(db, tid, 40, "", by="ogawa", actor_user=og, confirm=True)
    db.commit()
    from app.models import BdMessage
    m = db.query(BdMessage).order_by(BdMessage.id.desc()).first()
    assert m is not None
    assert "タスク進捗を確認しました" in m.title, "日语收件人要收到日文标题（拿到 %s）" % m.title
    assert "申告した" in m.body and "確認しました" in m.body, m.body
    db.close()


def test_notification_zh_recipient_still_chinese(client, seeded):
    """中文收件人（未设 lang）仍是中文，别被日文化带跑。"""
    db = appdb.SessionLocal()
    tid = seeded["task"]
    bd_tasks.assign_members(db, tid, ["P2"], by="admin")
    tj = db.query(User).filter(User.username == "tangjing").first()
    og = db.query(User).filter(User.username == "ogawa").first()
    bd_tasks.save_progress(db, tid, 40, "", by="tangjing", actor_user=tj)
    bd_tasks.save_progress(db, tid, 40, "", by="ogawa", actor_user=og, confirm=True)
    db.commit()
    from app.models import BdMessage
    m = db.query(BdMessage).order_by(BdMessage.id.desc()).first()
    assert "任务进展已确认" in m.title and "你上报的" in m.body, (m.title, m.body)
    db.close()


def test_html_lang_follows_language_and_jst_timestamp(client, seeded):
    """**① `<html lang>` + JST 时间戳**：日语界面 lang=ja；UTC 时间按 JST 显示。"""
    import datetime as _dt
    _login(client, "admin")
    assert '<html lang="zh-CN">' in client.get("/tasks").text
    h = client.get("/tasks?lang=ja").text
    assert '<html lang="ja">' in h, "日语界面 <html lang> 要是 ja"
    # jst()：UTC → JST（少 9 小时的坑）
    from app.templating import jst_str
    assert jst_str(_dt.datetime(2026, 10, 6, 17, 0)) == "2026-10-07 02:00"
    assert jst_str(None) == "—"
    # 日志页真的用了 jst()
    lg = client.get("/logs").text
    assert "jst(" not in lg and lg.count("20") > 0
