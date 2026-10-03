# -*- coding: utf-8 -*-
"""车站任务（规格 `docs/specs-station-tasks.md`）。

口径唯一来源。核心口径（写死在测试里）：

- **一个车站 = 一个任务**（`bd_task`，UNIQUE(station_id)）
- `assign_date` = **分配日期**（管理员把任务派给团队那天）→ 管理端按区间查询
- `state` 由「担当人数 + 进度」推导：`unassigned` / `doing` / `done`
  （**缺担当 → 未分配；有担当且 pct<100 → 进行中；pct>=100 → 已完成**）
- 担当 **1~2 人**（上限应用层强制），且必须是**该队现役成员**
- 每日进展：一天一条（`bd_task_progress`，UNIQUE(task_id, progress_date)），
  **当天可改（覆盖）**，同时把 `bd_task.pct/state` 刷新为最新值
- 权限：**提交进展/分派 = 该队队长或管理员**；队员只读（`can_submit`）
"""
from datetime import date, datetime
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models import (BdStation, BdTask, BdTaskAssign, BdTaskProgress,
                        BdTeam, BdTeamMember, Person)

STATE_UNASSIGNED = "unassigned"
STATE_DOING = "doing"
STATE_DONE = "done"
STATES = (STATE_UNASSIGNED, STATE_DOING, STATE_DONE)

TAB_UNASSIGNED = "unassigned"
TAB_DOING = "doing"
TAB_DONE = "done"
TABS = (TAB_UNASSIGNED, TAB_DOING, TAB_DONE)

MAX_ASSIGN = 2

STATE_LABELS = {
    "zh": {STATE_UNASSIGNED: "未分配", STATE_DOING: "进行中", STATE_DONE: "已完成"},
    "ja": {STATE_UNASSIGNED: "未割当", STATE_DOING: "進行中", STATE_DONE: "完了"},
}


class TaskError(Exception):
    """业务错误（路由转成页面提示，不 500）。"""


class NameExists(TaskError):
    pass


class NotOpenError(TaskError):
    pass


def norm_name(s: Optional[str]) -> str:
    """车站名归一（NFKC + 去全部空白）——判重唯一入口。"""
    import unicodedata
    t = unicodedata.normalize("NFKC", str(s or ""))
    return "".join(t.split())


def _today(today: Optional[date] = None) -> date:
    return today or date.today()


def state_labels(lang: str = "zh") -> dict:
    return STATE_LABELS.get(lang) or STATE_LABELS["zh"]


# ---------------- 车站主数据 ----------------

def create_station(db: Session, name: str, line: str = "", note: str = "",
                   by: str = "") -> BdStation:
    nm = (name or "").strip()
    if not nm:
        raise TaskError("车站名不能为空")
    key = norm_name(nm)
    if db.query(BdStation).filter(BdStation.name_norm == key).first():
        raise NameExists("车站已存在：%s（不覆盖）" % nm)
    st = BdStation(name=nm, name_norm=key, line=(line or "").strip(),
                   note=(note or "").strip(), status="active")
    db.add(st)
    db.flush()
    return st


def update_station(db: Session, station_id: int, name: Optional[str] = None,
                   line: Optional[str] = None, note: Optional[str] = None,
                   status: Optional[str] = None, actor_user=None) -> BdStation:
    """改车站主数据（**改名/改线路/改备注/改状态都写日志**）。"""
    from app.services import bd_log
    st = db.get(BdStation, station_id)
    if st is None:
        raise TaskError("车站不存在")
    before = {"name": st.name, "line": st.line, "note": st.note,
              "status": st.status}
    if name is not None:
        nm = name.strip()
        if not nm:
            raise TaskError("车站名不能为空")
        key = norm_name(nm)
        dup = db.query(BdStation).filter(BdStation.name_norm == key,
                                         BdStation.id != station_id).first()
        if dup is not None:
            raise NameExists("车站已存在：%s（不覆盖）" % nm)
        st.name, st.name_norm = nm, key
    if line is not None:
        st.line = line.strip()
    if note is not None:
        st.note = note.strip()
    if status in ("active", "closed"):
        st.status = status
    st.updated_at = datetime.utcnow()
    db.flush()
    for f, label in (("name", "车站名"), ("line", "线路"), ("note", "备注"),
                     ("status", "状态")):
        after = getattr(st, f)
        if before[f] != after:
            bd_log.log_op(db, actor_user, "station",
                          "rename" if f == "name" else "update",
                          ref_id=st.id, ref_label=st.name, field=label,
                          old=before[f], new=after)
    return st


def list_stations(db: Session, kw: str = "", status: str = "",
                  only_without_task: bool = False) -> List[dict]:
    """车站列表 + 任务概要（一次查询任务/队，避免 N+1）。"""
    q = db.query(BdStation)
    if kw:
        like = "%%%s%%" % kw.strip()
        q = q.filter(or_(BdStation.name.like(like), BdStation.line.like(like)))
    if status in ("active", "closed"):
        q = q.filter(BdStation.status == status)
    stations = q.order_by(BdStation.id.asc()).all()
    task_map = {}
    if stations:
        tasks = (db.query(BdTask)
                 .filter(BdTask.station_id.in_([s.id for s in stations])).all())
        task_map = {t.station_id: t for t in tasks}
    team_names = {t.id: t.name for t in db.query(BdTeam).all()}
    out = []
    for s in stations:
        t = task_map.get(s.id)
        if only_without_task and t is not None:
            continue
        out.append({"station": s, "task": t,
                    "team_name": (team_names.get(t.team_id) if t else "") or "",
                    "state": (t.state if t else ""),
                    "pct": (t.pct if t else 0)})
    return out


# ---------------- 任务 ----------------

def recompute_state(pct: int, n_assign: int) -> str:
    """状态唯一口径（优先级：已完成 > 进行中 > 未分配）：

    - `pct >= 100` → **已完成**（哪怕担当被撤掉，完成就是完成）
    - 否则 **有担当 或 有进度** → **进行中**（已经开工了就不该显示"未分配"）
    - 其他（没担当且 pct=0）→ **未分配**

    ⚠️ 「有进度也算进行中」是 2026-10-03 修的：旧口径只看担当，会出现
    "pct=30 但停在未分配"的自相矛盾状态（队长自己开工、还没分人时就会踩）。
    """
    p = int(pct or 0)
    if p >= 100:
        return STATE_DONE
    if n_assign > 0 or p > 0:
        return STATE_DOING
    return STATE_UNASSIGNED


def _assign_counts(db: Session, task_ids: Sequence[int]) -> Dict[int, int]:
    if not task_ids:
        return {}
    rows = (db.query(BdTaskAssign.task_id, func.count(BdTaskAssign.id))
            .filter(BdTaskAssign.task_id.in_(list(task_ids)))
            .group_by(BdTaskAssign.task_id).all())
    return {tid: n for tid, n in rows}


def refresh_state(db: Session, task: BdTask) -> BdTask:
    n = (db.query(func.count(BdTaskAssign.id))
         .filter(BdTaskAssign.task_id == task.id).scalar() or 0)
    task.state = recompute_state(task.pct, n)
    task.updated_at = datetime.utcnow()
    return task


def create_tasks(db: Session, station_ids: Sequence[int], by: str = "",
                 team_id: Optional[int] = None,
                 assign_date: Optional[date] = None,
                 actor_user=None) -> dict:
    """给车站**批量建任务**（缺则建，已有则跳过并回报，不覆盖、不报 500）。**写创建日志。**"""
    from app.services import bd_log
    if actor_user is not None:
        by = getattr(actor_user, "username", "") or by
    created = skipped = 0
    have = set()
    if station_ids:
        have = {sid for (sid,) in db.query(BdTask.station_id)
                .filter(BdTask.station_id.in_(list(station_ids))).all()}
    for sid in station_ids:
        if sid in have:
            skipped += 1
            continue
        st = db.get(BdStation, sid)
        if st is None:
            skipped += 1
            continue
        t = BdTask(station_id=sid, team_id=team_id, assign_date=assign_date,
                   state=STATE_UNASSIGNED, pct=0, created_by=(by or ""))
        db.add(t)
        db.flush()
        bd_log.log_op(db, actor_user, "task", "create", ref_id=t.id,
                      ref_label=(st.name or ""), field="",
                      new=("派给 %s" % (db.get(BdTeam, team_id).name
                                       if team_id else "（未派队）")))
        created += 1
    db.flush()
    return {"created": created, "skipped": skipped}


def set_task_team(db: Session, task_ids: Sequence[int],
                  team_id: Optional[int], assign_date: Optional[date] = None,
                  by: str = "", actor_user=None) -> dict:
    """**派给团队**（管理员）；顺带写分配日期。

    换队 → **清空不属于新队的担当**（原担当不属于新队），并回报清掉的人数。
    **每次派活/换队/改分配日期都写日志。**
    """
    from app.services import bd_log
    if team_id is not None and db.get(BdTeam, team_id) is None:
        raise TaskError("团队不存在")
    if actor_user is not None:
        by = getattr(actor_user, "username", "") or by
    n_team = n_cleared = 0
    for tid in task_ids:
        t = db.get(BdTask, tid)
        if t is None:
            continue
        label = _station_name(db, t)
        old_team = db.get(BdTeam, t.team_id) if t.team_id else None
        new_team = db.get(BdTeam, team_id) if team_id else None
        if t.team_id != team_id:
            bd_log.log_op(db, actor_user, "task", "dispatch", ref_id=t.id,
                          ref_label=label, field="team",
                          old=(old_team.name if old_team else "（未派队）"),
                          new=(new_team.name if new_team else "（未派队）"))
        if assign_date is not None and t.assign_date != assign_date:
            bd_log.log_op(db, actor_user, "task", "update", ref_id=t.id,
                          ref_label=label, field="assign_date",
                          old=(t.assign_date.isoformat() if t.assign_date else ""),
                          new=assign_date.isoformat())
        if t.team_id != team_id and team_id is not None:
            keep = {c for (c,) in db.query(BdTeamMember.person_code).filter(
                BdTeamMember.team_id == team_id,
                BdTeamMember.end_date.is_(None)).all()}
            old = db.query(BdTaskAssign).filter(
                BdTaskAssign.task_id == tid).all()
            for a in old:
                if a.person_code not in keep:
                    db.delete(a)
                    n_cleared += 1
                    bd_log.log_op(db, actor_user, "task", "unassign",
                                  ref_id=t.id, ref_label=label,
                                  field="assignee", old=a.person_code,
                                  new="", note="换队清空原担当")
        t.team_id = team_id
        if assign_date is not None:
            t.assign_date = assign_date
        refresh_state(db, t)
        n_team += 1
    db.flush()
    return {"updated": n_team, "cleared": n_cleared}


def assign_members(db: Session, task_id: int, person_codes: Sequence[str],
                   by: str = "", actor_user=None,
                   on_date: Optional[date] = None) -> dict:
    """分派担当（**≤2 人**，必须是该队现役成员）。**谁进谁出都写日志。**

    ⚠️ 离职/停用的人**不能**被分派（他执行不了）；历史担当记录不受影响。
    """
    t = db.get(BdTask, task_id)
    if t is None:
        raise TaskError("任务不存在")
    if actor_user is not None:
        by = getattr(actor_user, "username", "") or by
    codes = []
    for c in person_codes:
        c = (c or "").strip()
        if c and c not in codes:
            codes.append(c)
    if len(codes) > MAX_ASSIGN:
        raise TaskError("一个车站最多分给 %d 个人" % MAX_ASSIGN)
    if codes:
        if t.team_id is None:
            raise TaskError("请先把任务派给团队，再分派队员")
        allowed = {c for (c,) in db.query(BdTeamMember.person_code).filter(
            BdTeamMember.team_id == t.team_id,
            BdTeamMember.end_date.is_(None)).all()}
        bad = [c for c in codes if c not in allowed]
        if bad:
            raise TaskError("不是该队现役成员：%s" % "、".join(bad))
        # 离职/停用的人执行不了 → 不能分派（用户 2026-10-03 口径）
        from app.models import User as _User
        gone = []
        for u in db.query(_User).filter(_User.person_code.in_(codes)).all():
            if u.status in ("resigned", "disabled"):
                gone.append(u.display_name or u.person_code)
        if gone:
            raise TaskError("这些人已离职/停用，不能派工：%s" % "、".join(gone))
    # 出勤计划 / 假期模式 / 请假 → **提醒但不阻断**（用户 2026-10-03 口径）
    warn_lines: List[str] = []
    from app.services import bd_leave
    d = on_date
    if d is None:
        # 看"任务的工作日"：分配日期与今天取**较晚**者 —— 任务还没开始就看开始那天，
        # 已经开始/分配日期已过就看今天（否则拿过期日期判断，提醒永远落不到点上）
        _base = bd_leave.today()
        d = max(t.assign_date, _base) if t.assign_date else _base
    if codes:
        av = bd_leave.availability_map(db, codes, d)
        names = {c: n for c, n in
                 db.query(BdTeamMember.person_code, Person.display_name)
                 .join(Person, Person.code == BdTeamMember.person_code)
                 .filter(BdTeamMember.person_code.in_(codes)).all()}
        for c in codes:
            txt = bd_leave.warn_text(av.get(c) or {}, names.get(c) or c)
            if txt:
                warn_lines.append(txt)
    from app.services import bd_log
    old = db.query(BdTaskAssign).filter(BdTaskAssign.task_id == task_id).all()
    old_codes = [a.person_code for a in old]
    for a in old:
        db.delete(a)
    for c in codes:
        db.add(BdTaskAssign(task_id=task_id, person_code=c,
                            assigned_by=(by or "")))
    db.flush()
    refresh_state(db, t)
    db.flush()
    label = _station_name(db, t)
    for c in old_codes:
        if c not in codes:
            bd_log.log_op(db, actor_user, "task", "unassign", ref_id=t.id,
                          ref_label=label, field="assignee", old=c, new="")
    for c in codes:
        if c not in old_codes:
            bd_log.log_op(db, actor_user, "task", "assign", ref_id=t.id,
                          ref_label=label, field="assignee", old="", new=c)
    return {"n": len(codes), "state": t.state, "warnings": warn_lines}


def save_progress(db: Session, task_id: int, pct: int, note: str = "",
                  by: str = "", on_date: Optional[date] = None,
                  actor_user=None) -> dict:
    """**每日进展上报/调整**：写/改当天一条，并把任务刷新为最新进度。

    - `pct` 夹到 0–100
    - 当天已上报 → 覆盖（`updated_at` 变）——**员工先上报、队长做调整**都走这里
    - 刷新 `bd_task.pct/state`，开始日/完成日自动写
    - **每次上报/调整都写一条日志**（进度表会被覆盖，日志不会：
      "员工报 40% → 队长改成 60%" 能看出是谁改的）
    """
    from app.services import bd_log
    t = db.get(BdTask, task_id)
    if t is None:
        raise TaskError("任务不存在")
    try:
        p = int(pct)
    except (TypeError, ValueError):
        raise TaskError("进度必须是 0–100 的整数")
    p = max(0, min(100, p))
    d = _today(on_date)
    if actor_user is not None:
        by = getattr(actor_user, "username", "") or by
    old_pct, old_state = t.pct, t.state
    row = (db.query(BdTaskProgress)
           .filter(BdTaskProgress.task_id == task_id,
                   BdTaskProgress.progress_date == d).first())
    if row is None:
        row = BdTaskProgress(task_id=task_id, progress_date=d, pct=p,
                             note=(note or "").strip(),
                             submitted_by=(by or ""))
        db.add(row)
    else:
        row.pct = p
        row.note = (note or "").strip()
        row.submitted_by = (by or "")
        row.updated_at = datetime.utcnow()
    t.pct = p
    # 开始日 / 完成日**自动写**（用户 2026-10-03 口径）
    if t.start_date is None:
        t.start_date = d                      # 首次提交 = 开始
    if p >= 100:
        if t.done_date is None:
            t.done_date = d                   # 到 100% = 完成
    else:
        t.done_date = None                    # 进度回退 → 完成日清掉，保持自洽
    refresh_state(db, t)
    db.flush()
    if p != old_pct or t.state != old_state:
        bd_log.log_op(db, actor_user, "task",
                      "progress" if p != old_pct else "state",
                      ref_id=t.id, ref_label=_station_name(db, t),
                      field="pct" if p != old_pct else "state",
                      old=("%d%%" % old_pct) if p != old_pct else old_state,
                      new=("%d%%" % p) if p != old_pct else t.state,
                      note=(note or ""))
    return {"pct": p, "state": t.state, "date": d,
            "start_date": t.start_date, "done_date": t.done_date}


def _station_name(db: Session, task: BdTask) -> str:
    st = db.get(BdStation, task.station_id) if task is not None else None
    return (st.name if st is not None else "") or ""


def latest_progress(db: Session, task_ids: Sequence[int]) -> Dict[int, BdTaskProgress]:
    """每个任务最新一条进展（一次查询，供列表显示"最后提交"）。"""
    if not task_ids:
        return {}
    rows = (db.query(BdTaskProgress)
            .filter(BdTaskProgress.task_id.in_(list(task_ids)))
            .order_by(BdTaskProgress.task_id.asc(),
                      BdTaskProgress.progress_date.desc()).all())
    out: Dict[int, BdTaskProgress] = {}
    for r in rows:
        out.setdefault(r.task_id, r)
    return out


# ---------------- 权限（口径见 bd_perm；数据范围见 bd_teams.is_leader_of） ----------------

def is_admin(user) -> bool:
    return user is not None and getattr(user, "role", "") == "admin"


def _is_team_leader(db: Session, user, team_id) -> bool:
    from app.services import bd_teams
    if user is None or getattr(user, "role", "") != "leader":
        return False
    return bd_teams.is_leader_of(db, getattr(user, "person_code", None), team_id)


def is_assignee(db: Session, user, task: BdTask) -> bool:
    """本人是不是这条任务的担当（1~2 人之一）。"""
    code = getattr(user, "person_code", None) if user is not None else None
    if not code:
        return False
    return db.query(BdTaskAssign).filter(
        BdTaskAssign.task_id == task.id,
        BdTaskAssign.person_code == code).first() is not None


def can_report(db: Session, user, task: BdTask) -> bool:
    """**上报进展**：管理员 / 该任务的队长 / **本人是担当**。

    ⚠️ 用户 2026-10-03 口径变更：「员工自己先上报，队长做调整」——
    旧口径只有队长能提交，已作废。
    """
    from app.services import bd_perm
    if user is None:
        return False
    if is_admin(user):
        return True
    if not bd_perm.can(db, user, "task.report"):
        return False
    return _is_team_leader(db, user, task.team_id) or is_assignee(db, user, task)


def can_assign(db: Session, user, task: BdTask) -> bool:
    """**分派担当 / 调整别人的进展**：管理员 / 该任务的队长（队员不行）。"""
    from app.services import bd_perm
    if user is None:
        return False
    if is_admin(user):
        return True
    if not bd_perm.can(db, user, "task.assign"):
        return False
    return _is_team_leader(db, user, task.team_id)


def can_adjust(db: Session, user, task: BdTask) -> bool:
    """调整（修正）进展：管理员 / 该任务的队长。"""
    from app.services import bd_perm
    if user is None:
        return False
    if is_admin(user):
        return True
    if not bd_perm.can(db, user, "task.adjust"):
        return False
    return _is_team_leader(db, user, task.team_id)


# ---------------- 视图数据 ----------------

def task_rows(db: Session, task_ids: Sequence[int]) -> List[dict]:
    """按 id 取任务行（路由/详情页用；内部走同一个 `_rows`）。"""
    if not task_ids:
        return []
    tasks = (db.query(BdTask).filter(BdTask.id.in_(list(task_ids)))
             .order_by(BdTask.id.asc()).all())
    return _rows(db, tasks)



def _rows(db: Session, tasks: Sequence[BdTask]) -> List[dict]:
    """任务行（车站/队/担当/最新进展），**批量取，避免 N+1**。"""
    if not tasks:
        return []
    tids = [t.id for t in tasks]
    sids = [t.station_id for t in tasks]
    stations = {s.id: s for s in db.query(BdStation)
                .filter(BdStation.id.in_(sids)).all()}
    teams = {t.id: t for t in db.query(BdTeam).all()}
    assigns: Dict[int, List[str]] = {}
    names = {}
    for a in (db.query(BdTaskAssign)
              .filter(BdTaskAssign.task_id.in_(tids))
              .order_by(BdTaskAssign.id.asc()).all()):
        assigns.setdefault(a.task_id, []).append(a.person_code)
    codes = {c for v in assigns.values() for c in v}
    if codes:
        names = {c: (d or c) for c, d in db.query(Person.code, Person.display_name)
                 .filter(Person.code.in_(list(codes))).all()}
    last = latest_progress(db, tids)
    # 每队队长名（显示用）
    team_leaders: Dict[int, List[str]] = {}
    tids_team = [t.team_id for t in tasks if t.team_id]
    if tids_team:
        lrows = (db.query(BdTeamMember.team_id, BdTeamMember.person_code,
                          Person.display_name)
                 .outerjoin(Person, Person.code == BdTeamMember.person_code)
                 .filter(BdTeamMember.team_id.in_(tids_team),
                         BdTeamMember.role == "leader",
                         BdTeamMember.end_date.is_(None)).all())
        for team_id, code, disp in lrows:
            team_leaders.setdefault(team_id, []).append(disp or code)
    out = []
    today = date.today()
    for t in tasks:
        st = stations.get(t.station_id)
        a_codes = assigns.get(t.id, [])
        lp = last.get(t.id)
        last_d = (lp.progress_date if lp else None)
        # "多少天没动"：从最后一次提交算；从没提交过则按分配日期算
        base = last_d or t.assign_date
        days = (today - base).days if base else None
        # 停滞 = 还没完成 且（从没提交 或 超过 2 天没更新）
        stale = (t.state != STATE_DONE
                 and (last_d is None or (days is not None and days >= 2)))
        out.append({
            "task": t, "station": st,
            "station_name": (st.name if st else ""),
            "line": (st.line if st else ""),
            "team_id": t.team_id,
            "team_name": (teams[t.team_id].name if t.team_id in teams else ""),
            "team_leaders": team_leaders.get(t.team_id, []),
            "assignees": [{"code": c, "name": names.get(c, c)} for c in a_codes],
            "n_assign": len(a_codes),
            "pct": t.pct, "state": t.state,
            "assign_date": t.assign_date,
            "start_date": t.start_date, "done_date": t.done_date,
            "days_since": days, "stale": stale,
            "last_date": last_d,
            "last_pct": (lp.pct if lp else None),
            "last_note": (lp.note if lp else ""),
        })
    return out


def team_tasks(db: Session, team_ids: Sequence[int],
               tab: str = "", kw: str = "") -> List[dict]:
    """队长视角：本队任务（可按 tab 过滤）。"""
    if not team_ids:
        return []
    q = db.query(BdTask).filter(BdTask.team_id.in_(list(team_ids)))
    if tab in TABS:
        q = q.filter(BdTask.state == tab)
    tasks = q.order_by(BdTask.state.asc(), BdTask.id.asc()).all()
    rows = _rows(db, tasks)
    if kw:
        k = kw.strip()
        rows = [r for r in rows if k in (r["station_name"] or "")]
    return rows


def member_tasks(db: Session, person_code: Optional[str]) -> List[dict]:
    """队员视角（只读）：分给我的任务。"""
    if not person_code:
        return []
    tids = [tid for (tid,) in db.query(BdTaskAssign.task_id)
            .filter(BdTaskAssign.person_code == person_code).all()]
    if not tids:
        return []
    tasks = (db.query(BdTask).filter(BdTask.id.in_(tids))
             .order_by(BdTask.state.asc(), BdTask.id.asc()).all())
    return _rows(db, tasks)


def task_board(db: Session, team_id: Optional[int] = None, state: str = "",
               date_from: Optional[date] = None,
               date_to: Optional[date] = None, kw: str = "",
               only_assigned: bool = True, stale_only: bool = False,
               stale_days: int = 2,
               limit: int = 500, offset: int = 0) -> dict:
    """**管理端任务总表**：默认只看"已分配"（派给了团队）的任务。

    - `date_from/date_to` 按**分配日期**区间过滤（用户要求）
    - `stale_only` = 只看"停滞"（未完成 且 ≥`stale_days` 天没更新，或从没提交）
    """
    q = db.query(BdTask)
    if only_assigned:
        q = q.filter(BdTask.team_id.isnot(None))
    if team_id:
        q = q.filter(BdTask.team_id == team_id)
    if state in STATES:
        q = q.filter(BdTask.state == state)
    if stale_only:
        from datetime import timedelta as _td
        cutoff = date.today() - _td(days=max(0, stale_days))
        recent = (db.query(BdTaskProgress.task_id)
                  .filter(BdTaskProgress.progress_date >= cutoff))
        q = q.filter(BdTask.state != STATE_DONE, BdTask.id.notin_(recent))
    if date_from is not None:
        q = q.filter(BdTask.assign_date.isnot(None),
                     BdTask.assign_date >= date_from)
    if date_to is not None:
        q = q.filter(BdTask.assign_date.isnot(None),
                     BdTask.assign_date <= date_to)
    total = q.count()
    tasks = (q.order_by(BdTask.assign_date.desc().nullslast(),
                        BdTask.id.asc())
             .limit(limit).offset(offset).all())
    rows = _rows(db, tasks)
    if stale_only:
        rows = [r for r in rows if r["stale"]]
    if kw:
        k = kw.strip()
        rows = [r for r in rows if k in (r["station_name"] or "")
                or k in (r["team_name"] or "")]
    return {"rows": rows, "total": total, "offset": offset, "limit": limit,
            "stale_days": stale_days}


def team_board_summary(db: Session, stale_days: int = 2) -> List[dict]:
    """**按队汇总**（一次查询后在 Python 聚合）：每队 总数/未分配/进行中/已完成/停滞。

    "停滞" = 未完成 且（从没提交 或 超过 `stale_days` 天没更新）——
    管理端一眼看出"哪个队没动"（没有分母时这是最有用的抓手）。
    """
    tasks = db.query(BdTask.id, BdTask.team_id, BdTask.state,
                     BdTask.assign_date).all()
    last = latest_progress(db, [t[0] for t in tasks])
    teams = {t.id: t.name for t in db.query(BdTeam).all()}
    today = date.today()
    agg: Dict[int, dict] = {}
    for tid, team_id, state, assign_date in tasks:
        if team_id is None:
            continue
        a = agg.setdefault(team_id, {"team_id": team_id,
                                     "team_name": teams.get(team_id, ""),
                                     "total": 0, "unassigned": 0, "doing": 0,
                                     "done": 0, "stale": 0})
        a["total"] += 1
        a[state if state in ("unassigned", "doing", "done") else "unassigned"] += 1
        lp = last.get(tid)
        base = (lp.progress_date if lp else assign_date)
        days = (today - base).days if base else None
        if state != STATE_DONE and (lp is None or (days is not None
                                                  and days >= stale_days)):
            a["stale"] += 1
    return sorted(agg.values(), key=lambda x: (-x["stale"], x["team_name"]))


def board_summary(db: Session) -> dict:
    """汇总（一次聚合）：任务总数、各状态数、未派队的任务数、队数。"""
    rows = (db.query(BdTask.state, func.count(BdTask.id))
            .group_by(BdTask.state).all())
    by_state = {s: n for s, n in rows}
    n_task = sum(by_state.values())
    n_no_team = db.query(func.count(BdTask.id)).filter(
        BdTask.team_id.is_(None)).scalar() or 0
    n_station = db.query(func.count(BdStation.id)).scalar() or 0
    return {"n_station": n_station, "n_task": n_task,
            "unassigned": by_state.get(STATE_UNASSIGNED, 0),
            "doing": by_state.get(STATE_DOING, 0),
            "done": by_state.get(STATE_DONE, 0),
            "no_team": n_no_team}


def tasks_xlsx(db: Session, team_id: Optional[int] = None, state: str = "",
               date_from: Optional[date] = None,
               date_to: Optional[date] = None, lang: str = "zh"):
    """导出（**流式写**，不在内存里留整份工作簿）。"""
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter
    labels = state_labels(lang)
    data = task_board(db, team_id=team_id, state=state, date_from=date_from,
                      date_to=date_to, limit=100000)
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("车站任务")
    head = ["车站", "线路", "团队", "担当", "状态", "进度%",
            "分配日期", "开始日", "完成日", "最后提交", "多少天没动", "备注"]
    ws.append(head)
    for c in range(1, len(head) + 1):
        ws.column_dimensions[get_column_letter(c)].width = 18

    def _d(v):
        return v.strftime("%Y-%m-%d") if v else ""

    for r in data["rows"]:
        ws.append([r["station_name"], r["line"], r["team_name"],
                   "、".join(a["name"] for a in r["assignees"]),
                   labels.get(r["state"], r["state"]), r["pct"],
                   _d(r["assign_date"]), _d(r["start_date"]), _d(r["done_date"]),
                   _d(r["last_date"]),
                   ("" if r["days_since"] is None else r["days_since"]),
                   r["last_note"]])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
