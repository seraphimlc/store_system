# -*- coding: utf-8 -*-
"""作业域 MCP 工具的能力层测试（2026-10-06 新增）。

不mock 业务：直接用一个**内存库**建队/建站/建任务，然后走 `mcp_service.task_ops`
（MCP 工具调的就是它）；口径必须与 Web 端**同一个服务层**一致。
"""
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.db as appdb
from app.db import Base
from app.models import Person, User
from app.auth import hash_password
from mcp_service import task_ops


class _Actor:
    """MCP 侧的身份快照（与 authz 用的字段一致）。"""

    def __init__(self, role="leader", username="ogawa", person_code="P1", uid=1):
        self.role = role
        self.username = username
        self.person_code = person_code
        self.uid = uid
        self.display_name = username


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    appdb._ENGINE = engine
    appdb.SessionLocal = sessionmaker(bind=engine, future=True,
                                      expire_on_commit=False)
    Base.metadata.create_all(engine)
    s = appdb.SessionLocal()
    from app.services import bd_teams, bd_tasks
    # 人 + 账号
    for code, name in (("P1", "小川逸"), ("P2", "文伊琪")):
        s.add(Person(code=code, display_name=name))
        s.add(User(username=name, display_name=name, role="staff", is_active=True,
                   status="active", person_code=code,
                   password_hash=hash_password("pw123456")))
    s.flush()
    team = bd_teams.create_team(s, "小川队", "TW01", by="admin")
    # entries = [(person_code, role)]
    # entries = [(person_code, role)]；签名里没有 by（写日志用 actor_user）
    bd_teams.set_members(s, team.id, [("P1", "leader"), ("P2", "member")])
    st = bd_tasks.create_station(s, "駒場東大前", line="井の頭線")
    bd_tasks.create_tasks(s, [st.id], by="admin", team_id=team.id)
    s.commit()
    yield s
    s.close()
    Base.metadata.drop_all(engine)


def _tid(db):
    from app.models import BdTask
    return db.query(BdTask).order_by(BdTask.id.desc()).first().id


def test_my_tasks_and_self_report_round_trip(db):
    """员工：看我的任务（含自动延续）→ 点数 + 进度**一次提交**。"""
    from app.services import bd_tasks
    leader = _Actor(role="leader", username="ogawa", person_code="P1")
    staff = _Actor(role="staff", username="文伊琪", person_code="P2", uid=2)
    tid = _tid(db)
    bd_tasks.assign_members(db, tid, ["P2"], by="ogawa", actor_user=leader)
    db.commit()
    v = task_ops.my_tasks(db, "P2")
    assert v["n_open"] == 1 and v["open"][0]["task_id"] == tid
    assert v["daily_report"] is None
    r = task_ops.self_report(db, staff, area="渋谷", p1_cnt=2, p2_cnt=1,
                             items=[{"task_id": tid, "pct": 40, "note": "开始"}])
    assert r["p1_cnt"] == 2 and r["p2_cnt"] == 1 and r["tasks_reported"] == 1
    v2 = task_ops.my_tasks(db, "P2")
    assert v2["open"][0]["pct"] == 40
    assert v2["daily_report"]["total"] == 3
    # 进度没变又没备注 → 跳过（不产生多余待确认）
    r2 = task_ops.self_report(db, staff, area="渋谷", p1_cnt=2, p2_cnt=1,
                              items=[{"task_id": tid, "pct": 40}])
    assert r2["tasks_reported"] == 0 and r2["tasks_skipped"] == 1
    # 单条上报
    r3 = task_ops.report_one(db, staff, task_id=tid, pct=60, note="继续")
    assert r3["pct"] == 60


def test_team_tasks_confirm_and_reject(db):
    """队长：本队任务/待确认 → 一键全确认 → 驳回（员工原值保留）。"""
    from app.services import bd_tasks
    leader = _Actor(role="leader", username="ogawa", person_code="P1")
    staff = _Actor(role="staff", username="文伊琪", person_code="P2", uid=2)
    tid = _tid(db)
    bd_tasks.assign_members(db, tid, ["P2"], by="ogawa", actor_user=leader)
    db.commit()
    # 先报 40%（进行中 + 待确认），再报 100%（已完成 + 待确认）
    task_ops.report_one(db, staff, task_id=tid, pct=40, note="做一半")
    t = task_ops.team_tasks(db, [1])
    assert t["counts"]["pending"] == 1 and t["counts"]["doing"] == 1
    assert t["pending"][0]["task_id"] == tid
    # ⚠️ 计数口径：100% 的任务算**已完成**，不再算进行中（2026-10-06 统一）
    task_ops.report_one(db, staff, task_id=tid, pct=100, note="干完了")
    t1b = task_ops.team_tasks(db, [1])
    assert t1b["counts"]["doing"] == 0 and t1b["counts"]["done"] == 1, t1b["counts"]
    assert t1b["counts"]["pending"] == 1
    # 一键全确认
    assert task_ops.confirm_day(db, leader, team_ids=[1])["confirmed"] == 1
    t2 = task_ops.team_tasks(db, [1])
    assert t2["counts"]["pending"] == 0 and t2["counts"]["done"] == 1
    # 驳回（队长对已确认的 100%）→ 员工原值保留
    rej = task_ops.reject(db, leader, task_id=tid, pct=40, note="照片没拍全")
    assert rej["pct"] == 40
    row = bd_tasks.day_progress_map(db, [tid]).get(tid)
    assert row.review_status == "rejected" and row.reported_pct == 100


def test_assign_recycle_and_confirm_locked(db):
    """队长：派工 → 回收（空置，进展保留）→ 再分给别人；确认后员工不能再改。"""
    from app.services import bd_tasks
    leader = _Actor(role="leader", username="ogawa", person_code="P1")
    staff = _Actor(role="staff", username="文伊琪", person_code="P2", uid=2)
    tid = _tid(db)
    a = task_ops.assign(db, leader, task_id=tid, person_codes=["P2"])
    assert a["n_assignees"] == 1 and a["state"] == "doing"
    task_ops.report_one(db, staff, task_id=tid, pct=30, note="三成")
    # 回收（空置）
    a2 = task_ops.assign(db, leader, task_id=tid, person_codes=[])
    assert a2["n_assignees"] == 0
    assert bd_tasks.day_progress_map(db, [tid]).get(tid).pct == 30, "进展保留"
    t = task_ops.team_tasks(db, [1])
    assert t["counts"]["unassigned"] == 1, "回收后回到本队待派"
    # 再分给别人 → 又回进行中
    task_ops.assign(db, leader, task_id=tid, person_codes=["P2"])
    assert task_ops.team_tasks(db, [1])["counts"]["doing"] == 1
    # 确认（锁当天）→ 员工再报会被 service 层挡
    task_ops.confirm_day(db, leader, task_id=tid)
    with pytest.raises(Exception) as ei:
        task_ops.report_one(db, staff, task_id=tid, pct=50)
    assert "已确认" in str(ei.value)


def test_board_and_return_pool(db):
    """管理员：任务总表 + 按线路撤回（只撤没分到人的）。"""
    from app.services import bd_tasks
    leader = _Actor(role="leader", username="ogawa", person_code="P1")
    admin = _Actor(role="admin", username="admin", person_code=None, uid=9)
    tid = _tid(db)
    b = task_ops.board(db)
    assert b["counts"]["assigned"] == 1 and b["total"] == 1
    assert b["by_team"] and b["by_team"][0]["team_name"] == "小川队"
    # 无担当 → 可撤
    r = task_ops.return_pool(db, admin, task_ids=[tid])
    assert r["returned"] == 1 and not r["skipped"]
    b2 = task_ops.board(db)
    assert b2["counts"]["unassigned"] == 1, "撤回后回车站池"
    # 再派队 + 分人 → 有担当不能撤
    bd_tasks.set_task_team(db, [tid], 1, by="admin")
    bd_tasks.assign_members(db, tid, ["P2"], by="ogawa", actor_user=leader)
    db.commit()
    r2 = task_ops.return_pool(db, admin, task_ids=[tid])
    assert r2["returned"] == 0 and r2["skipped"][0]["why"]


def test_transfer_to_other_team(db):
    """**转给别的队**：换队 + 原担当被移出（进度保留）+ 目标队能查到。

    MCP 工具（`visit_task_transfer`）调的就是 `task_ops.transfer`，
    口径必须与 Web 端同一个服务层一致。
    """
    from app.services import bd_teams, bd_tasks
    from app.models import BdTask, BdTaskAssign
    t2 = bd_teams.create_team(db, "汤静队", "TW02", by="admin")
    # ⚠️ 人要先存在，才能进队（set_members 会校验"人员不存在"）
    db.add(Person(code="P3", display_name="甘子杰"))
    db.add(User(username="ganzijie", display_name="甘子杰", role="staff",
                is_active=True, status="active", person_code="P3",
                password_hash=hash_password("pw123456")))
    db.flush()
    bd_teams.set_members(db, t2.id, [("P3", "leader")])
    tid = _tid(db)
    actor = _Actor(role="leader", username="ogawa", person_code="P1")
    task_ops.assign(db, actor, task_id=tid, person_codes=["P2"])
    db.commit()
    r = task_ops.transfer(db, actor, task_ids=[tid], to_team_id=t2.id)
    db.commit()
    assert r["transferred"] == 1 and r["to_team"] == "汤静队"
    assert r["cleared"] == 1 and r["removed_people"] == ["P2"]
    t = db.get(BdTask, tid)
    assert t.team_id == t2.id
    assert db.query(BdTaskAssign).filter(BdTaskAssign.task_id == tid).count() == 0
    # 目标队（汤静队）的池子里能看到它（用服务层查，形状稳定）
    from app.services import bd_tasks as _bt
    rows = _bt.team_tasks(db, [t2.id], tab="unassigned")
    assert tid in [x["task"].id for x in rows]


def test_transfer_denied_for_non_leader_of_that_team(db):
    """不是那个队的队长 → 拒绝（只能转自己队里的任务）。"""
    from app.services import bd_teams
    t2 = bd_teams.create_team(db, "汤静队", "TW03", by="admin")
    # P2 已在小川队（一人一队）→ 另外造个人当二队队长
    db.add(Person(code="P4", display_name="罗子傑"))
    db.flush()
    bd_teams.set_members(db, t2.id, [("P4", "leader")])
    db.commit()
    tid = _tid(db)
    outsider = _Actor(role="leader", username="outsider", person_code="P9")
    with pytest.raises(Exception) as e:
        task_ops.transfer(db, outsider, task_ids=[tid], to_team_id=t2.id)
    db.rollback()
    assert "队长" in str(e.value)
