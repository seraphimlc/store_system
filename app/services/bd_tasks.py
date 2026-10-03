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
                   status: Optional[str] = None) -> BdStation:
    st = db.get(BdStation, station_id)
    if st is None:
        raise TaskError("车站不存在")
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
    """状态唯一口径：没担当 → 未分配；有担当 pct<100 → 进行中；pct>=100 → 已完成。"""
    if n_assign <= 0:
        return STATE_UNASSIGNED
    return STATE_DONE if int(pct or 0) >= 100 else STATE_DOING


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
                 assign_date: Optional[date] = None) -> dict:
    """给车站**批量建任务**（缺则建，已有则跳过并回报，不覆盖、不报 500）。"""
    created = skipped = 0
    have = set()
    if station_ids:
        have = {sid for (sid,) in db.query(BdTask.station_id)
                .filter(BdTask.station_id.in_(list(station_ids))).all()}
    for sid in station_ids:
        if sid in have:
            skipped += 1
            continue
        if db.get(BdStation, sid) is None:
            skipped += 1
            continue
        db.add(BdTask(station_id=sid, team_id=team_id, assign_date=assign_date,
                      state=STATE_UNASSIGNED, pct=0, created_by=(by or "")))
        created += 1
    db.flush()
    return {"created": created, "skipped": skipped}


def set_task_team(db: Session, task_ids: Sequence[int],
                  team_id: Optional[int], assign_date: Optional[date] = None,
                  by: str = "") -> dict:
    """**派给团队**（管理员）；顺带写分配日期。

    换队 → **清空该任务的担当**（原担当不属于新队），并回报清掉的人数。
    """
    if team_id is not None and db.get(BdTeam, team_id) is None:
        raise TaskError("团队不存在")
    n_team = n_cleared = 0
    for tid in task_ids:
        t = db.get(BdTask, tid)
        if t is None:
            continue
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
        t.team_id = team_id
        if assign_date is not None:
            t.assign_date = assign_date
        refresh_state(db, t)
        n_team += 1
    db.flush()
    return {"updated": n_team, "cleared": n_cleared}


def assign_members(db: Session, task_id: int, person_codes: Sequence[str],
                   by: str = "") -> dict:
    """分派担当（**≤2 人**，必须是该队现役成员）。"""
    t = db.get(BdTask, task_id)
    if t is None:
        raise TaskError("任务不存在")
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
    old = db.query(BdTaskAssign).filter(BdTaskAssign.task_id == task_id).all()
    for a in old:
        db.delete(a)
    for c in codes:
        db.add(BdTaskAssign(task_id=task_id, person_code=c,
                            assigned_by=(by or "")))
    db.flush()
    refresh_state(db, t)
    db.flush()
    return {"n": len(codes), "state": t.state}


def save_progress(db: Session, task_id: int, pct: int, note: str = "",
                  by: str = "", on_date: Optional[date] = None) -> dict:
    """**每日进展提交**：写/改当天一条，并把任务刷新为最新进度。

    - `pct` 夹到 0–100
    - 当天已提交 → 覆盖（`updated_at` 变）
    - 刷新 `bd_task.pct/state`
    """
    t = db.get(BdTask, task_id)
    if t is None:
        raise TaskError("任务不存在")
    try:
        p = int(pct)
    except (TypeError, ValueError):
        raise TaskError("进度必须是 0–100 的整数")
    p = max(0, min(100, p))
    d = _today(on_date)
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
    refresh_state(db, t)
    db.flush()
    return {"pct": p, "state": t.state, "date": d}


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


# ---------------- 权限 ----------------

def is_admin(user) -> bool:
    return user is not None and getattr(user, "role", "") == "admin"


def can_submit(db: Session, user, task: BdTask) -> bool:
    """**提交进展/分派**的唯一权限判定：管理员，或该任务的队长。"""
    from app.services import bd_teams
    if user is None:
        return False
    if is_admin(user):
        return True
    if getattr(user, "role", "") != "leader":
        return False
    return bd_teams.is_leader_of(db, getattr(user, "person_code", None),
                                 task.team_id)


# ---------------- 视图数据 ----------------

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
    for t in tasks:
        st = stations.get(t.station_id)
        a_codes = assigns.get(t.id, [])
        lp = last.get(t.id)
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
            "last_date": (lp.progress_date if lp else None),
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
               only_assigned: bool = True,
               limit: int = 500, offset: int = 0) -> dict:
    """**管理端任务总表**：默认只看"已分配"（派给了团队）的任务。

    - `date_from/date_to` 按**分配日期**区间过滤（用户要求）
    """
    q = db.query(BdTask)
    if only_assigned:
        q = q.filter(BdTask.team_id.isnot(None))
    if team_id:
        q = q.filter(BdTask.team_id == team_id)
    if state in STATES:
        q = q.filter(BdTask.state == state)
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
    if kw:
        k = kw.strip()
        rows = [r for r in rows if k in (r["station_name"] or "")
                or k in (r["team_name"] or "")]
    return {"rows": rows, "total": total, "offset": offset, "limit": limit}


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
    head = ["车站", "线路", "团队", "担当", "状态", "进度%", "分配日期",
            "最后提交", "备注"]
    ws.append(head)
    for c in range(1, len(head) + 1):
        ws.column_dimensions[get_column_letter(c)].width = 18
    for r in data["rows"]:
        ws.append([r["station_name"], r["line"], r["team_name"],
                   "、".join(a["name"] for a in r["assignees"]),
                   labels.get(r["state"], r["state"]), r["pct"],
                   (r["assign_date"].strftime("%Y-%m-%d")
                    if r["assign_date"] else ""),
                   (r["last_date"].strftime("%Y-%m-%d")
                    if r["last_date"] else ""),
                   r["last_note"]])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
