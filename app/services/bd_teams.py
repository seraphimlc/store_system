# -*- coding: utf-8 -*-
"""团队定义（规格 `docs/specs-team-management.md`）。

口径唯一来源：路由只做校验与跳转，**渲染路径不写库**。

- 一层队；队名唯一，队编号可空唯一
- 成员**含历史**：移出 = 写 `end_date`，**永不删行**
- "谁是队长"落在 `bd_team_member.role`（`leader`/`member`）；
  账号角色 `users.role == "leader"` 只决定"能不能进队长端"
"""
from datetime import date
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models import BdTeam, BdTeamMember, Person, User

ROLE_LEADER = "leader"
ROLE_MEMBER = "member"
ROLES = (ROLE_LEADER, ROLE_MEMBER)


class TeamError(Exception):
    """团队操作的业务错误（路由转成页面提示，不 500）。"""


class NameExists(TeamError):
    pass


class CodeExists(TeamError):
    pass


def norm_code(s: Optional[str]) -> str:
    """队编号归一（NFKC + 去空白 + 大写）。"""
    import unicodedata
    t = unicodedata.normalize("NFKC", str(s or ""))
    return "".join(t.split()).upper()


def _today(today: Optional[date] = None) -> date:
    return today or date.today()


# ---------------- 队 ----------------

def create_team(db: Session, name: str, code: str = "", note: str = "",
                by: str = "", today: Optional[date] = None,
                actor_user=None) -> BdTeam:
    from app.services import bd_log
    nm = (name or "").strip()
    if not nm:
        raise TeamError("队名不能为空")
    code_n = norm_code(code)
    if db.query(BdTeam).filter(BdTeam.name == nm).first() is not None:
        raise NameExists("队名已存在：%s（不覆盖）" % nm)
    if code_n and db.query(BdTeam).filter(BdTeam.code == code_n).first():
        raise CodeExists("队编号已存在：%s（不覆盖）" % code_n)
    if actor_user is not None:
        by = getattr(actor_user, "username", "") or by
    t = BdTeam(name=nm, code=(code_n or None), note=(note or "").strip(),
               status="active", created_by=(by or ""))
    db.add(t)
    db.flush()
    bd_log.log_op(db, actor_user, "team", "create", ref_id=t.id,
                  ref_label=t.name, new=t.name)
    return t


def update_team(db: Session, team_id: int, name: Optional[str] = None,
                code: Optional[str] = None, note: Optional[str] = None,
                status: Optional[str] = None, by: str = "",
                actor_user=None) -> BdTeam:
    """改队信息（**队名/编号/备注/停用都写日志**）。"""
    from app.services import bd_log
    t = db.get(BdTeam, team_id)
    if t is None:
        raise TeamError("团队不存在")
    before = {"name": t.name, "code": t.code, "note": t.note, "status": t.status}
    if name is not None:
        nm = name.strip()
        if not nm:
            raise TeamError("队名不能为空")
        dup = db.query(BdTeam).filter(BdTeam.name == nm,
                                      BdTeam.id != team_id).first()
        if dup is not None:
            raise NameExists("队名已存在：%s（不覆盖）" % nm)
        t.name = nm
    if code is not None:
        code_n = norm_code(code)
        if code_n:
            dup = db.query(BdTeam).filter(BdTeam.code == code_n,
                                          BdTeam.id != team_id).first()
            if dup is not None:
                raise CodeExists("队编号已存在：%s（不覆盖）" % code_n)
        t.code = code_n or None
    if note is not None:
        t.note = note.strip()
    if status in ("active", "closed"):
        t.status = status
    t.updated_at = _now()
    db.flush()
    for f, label in (("name", "队名"), ("code", "队编号"), ("note", "备注"),
                     ("status", "状态")):
        after = getattr(t, f)
        if before[f] != after:
            bd_log.log_op(db, actor_user, "team",
                          "rename" if f == "name" else "update",
                          ref_id=t.id, ref_label=t.name, field=label,
                          old=before[f] or "", new=after or "")
    return t


def _now():
    from datetime import datetime
    return datetime.utcnow()


def list_teams(db: Session, kw: str = "", status: str = "") -> List[dict]:
    """队列表 + 队长名 + 现役人数（**一次聚合**，别 N+1）。"""
    q = db.query(BdTeam)
    if kw:
        like = "%%%s%%" % kw.strip()
        q = q.filter(or_(BdTeam.name.like(like), BdTeam.code.like(like)))
    if status in ("active", "closed"):
        q = q.filter(BdTeam.status == status)
    teams = q.order_by(BdTeam.status.asc(), BdTeam.id.asc()).all()
    ids = [t.id for t in teams]
    if not ids:
        return []
    rows = (db.query(BdTeamMember.team_id, BdTeamMember.role,
                     BdTeamMember.person_code, Person.display_name)
            .outerjoin(Person, Person.code == BdTeamMember.person_code)
            .filter(BdTeamMember.team_id.in_(ids),
                    BdTeamMember.end_date.is_(None)).all())
    agg: Dict[int, dict] = {t.id: {"leaders": [], "members": [],
                                   "n_leader": 0, "n_member": 0}
                            for t in teams}
    for team_id, role, code, disp in rows:
        a = agg[team_id]
        name = disp or code
        if role == ROLE_LEADER:
            a["n_leader"] += 1
            a["leaders"].append(name)
        else:
            a["n_member"] += 1
            a["members"].append(name)
    out = []
    for t in teams:
        a = agg[t.id]
        out.append({"team": t, "leaders": a["leaders"], "members": a["members"],
                    "n_leader": a["n_leader"],
                    "n_total": a["n_leader"] + a["n_member"]})
    return out


def team_options(db: Session) -> List[dict]:
    return [{"id": t.id, "name": t.name}
            for t in db.query(BdTeam).filter(BdTeam.status == "active")
            .order_by(BdTeam.id.asc()).all()]


# ---------------- 成员 ----------------

def team_members(db: Session, team_id: int, active_only: bool = True,
                 today: Optional[date] = None) -> List[dict]:
    """成员（默认只看现役），带姓名、**有无账号**、以及**员工状态**。

    ⚠️ 用户 2026-10-03 口径：「**队员的状态直接用员工状态**」——所以这里**不另造状态**，
    直接把 `users.status`（active/leave/disabled/resigned）带出来给页面显示。
    """
    q = (db.query(BdTeamMember, Person.display_name)
         .outerjoin(Person, Person.code == BdTeamMember.person_code)
         .filter(BdTeamMember.team_id == team_id))
    if active_only:
        q = q.filter(BdTeamMember.end_date.is_(None))
    rows = q.order_by(BdTeamMember.role.asc(),
                      BdTeamMember.person_code.asc()).all()
    codes = [m.person_code for m, _ in rows]
    st: Dict[str, str] = {}
    if codes:
        for code, status in (db.query(User.person_code, User.status)
                             .filter(User.person_code.in_(codes)).all()):
            if code:
                st[code] = status or "active"
    out = []
    for m, disp in rows:
        status = st.get(m.person_code, "")      # 空 = 没账号
        out.append({"row": m, "person_code": m.person_code,
                    "display_name": disp or m.person_code, "role": m.role,
                    "start_date": m.start_date, "end_date": m.end_date,
                    "has_account": m.person_code in st,
                    "status": status,
                    "can_work": status in ("active", "leave")})
    return out


def STATUS_LABELS(lang: str = "zh") -> Dict[str, str]:
    """员工状态标签（与员工管理页同一套口径）。"""
    zh = {"active": "在岗", "leave": "请假", "disabled": "停用",
          "resigned": "离职", "": "未开通账号"}
    ja = {"active": "在籍", "leave": "休暇", "disabled": "停止",
          "resigned": "退職", "": "アカウント未開設"}
    return ja if lang == "ja" else zh


def set_members(db: Session, team_id: int,
                entries: Sequence[Tuple[str, str]],
                today: Optional[date] = None,
                actor_user=None) -> dict:
    """覆盖式保存成员。

    `entries` = [(person_code, role)]；未出现在里面的现役成员 → 写 `end_date`（移出）。
    role 变化就地更新（成员期不重开）。返回 `{"added","removed","changed"}`。

    **加入 / 移出 / 队长任免都写日志**（用户 2026-10-03：团队变化、成员变化、队长变化要留痕）。
    """
    t = db.get(BdTeam, team_id)
    if t is None:
        raise TeamError("团队不存在")
    day = _today(today)
    desired: Dict[str, str] = {}
    for code, role in entries:
        c = (code or "").strip()
        if not c:
            continue
        r = ROLE_LEADER if role == ROLE_LEADER else ROLE_MEMBER
        desired[c] = r
    # 人员必须存在
    if desired:
        found = {c for (c,) in db.query(Person.code)
                 .filter(Person.code.in_(list(desired))).all()}
        missing = sorted(set(desired) - found)
        if missing:
            raise TeamError("人员不存在：%s" % "、".join(missing))
    active = {m.person_code: m for m in
              db.query(BdTeamMember)
              .filter(BdTeamMember.team_id == team_id,
                      BdTeamMember.end_date.is_(None)).all()}
    added = removed = changed = 0
    from app.services import bd_log
    names = {c: (d or c) for c, d in db.query(Person.code, Person.display_name)
             .filter(Person.code.in_(list(set(active) | set(desired)))).all()}

    def _label(code):
        return "%s / %s" % (t.name, names.get(code, code))

    def _role_cn(role):
        return "队长" if role == ROLE_LEADER else "队员"

    for code in list(active):
        if code not in desired:
            old_role = active[code].role
            active[code].end_date = day
            removed += 1
            bd_log.log_op(db, actor_user, "member", "remove", ref_id=team_id,
                          ref_label=_label(code), field="成员",
                          old="%s（%s）" % (names.get(code, code),
                                          _role_cn(old_role)),
                          new="", note="移出团队（记录保留）")
    for code, role in desired.items():
        m = active.get(code)
        if m is None:
            db.add(BdTeamMember(team_id=team_id, person_code=code, role=role,
                                start_date=day))
            added += 1
            bd_log.log_op(db, actor_user, "member", "create", ref_id=team_id,
                          ref_label=_label(code), field="成员", old="",
                          new="%s（%s）" % (names.get(code, code), _role_cn(role)))
        elif m.role != role:
            old_role = m.role
            m.role = role
            changed += 1
            bd_log.log_op(db, actor_user, "member", "role", ref_id=team_id,
                          ref_label=_label(code), field="角色",
                          old=_role_cn(old_role), new=_role_cn(role),
                          note="队长任免")
    db.flush()
    roles = sync_account_roles(db)
    return {"added": added, "removed": removed, "changed": changed,
            "promoted": roles["promoted"], "demoted": roles["demoted"]}


def sync_account_roles(db: Session) -> dict:
    """把「团队里的队长身份」同步到账号角色（**避免指定了队长却进不去队长端**）。

    - 在某队当 `leader`（现役且该队 `active`）→ `users.role = "leader"`
    - 不再当任何队队长 → 退回 `"staff"`
    - ⚠️ **只动 `role in ("staff","leader")` 的账号，绝不动 `admin`**
    - 只影响"能不能进队长端"；数据范围仍由 `bd_team_member` 决定

    返回 `{"promoted": [...], "demoted": [...]}`（改动的人）。
    """
    leads = {c for (c,) in
             db.query(BdTeamMember.person_code)
             .join(BdTeam, BdTeam.id == BdTeamMember.team_id)
             .filter(BdTeamMember.role == ROLE_LEADER,
                     BdTeamMember.end_date.is_(None),
                     BdTeam.status == "active").all() if c}
    promoted, demoted = [], []
    for u in db.query(User).filter(User.role.in_(("staff", "leader"))).all():
        if not u.person_code:
            continue
        want = ROLE_LEADER if u.person_code in leads else "staff"
        if u.role != want:
            u.role = want
            (promoted if want == ROLE_LEADER else demoted).append(u.person_code)
    db.flush()
    return {"promoted": promoted, "demoted": demoted}


def leader_teams(db: Session, person_code: Optional[str]) -> List[BdTeam]:
    """该人**当队长**的队（现役）。"""
    if not person_code:
        return []
    return (db.query(BdTeam)
            .join(BdTeamMember, BdTeamMember.team_id == BdTeam.id)
            .filter(BdTeamMember.person_code == person_code,
                    BdTeamMember.role == ROLE_LEADER,
                    BdTeamMember.end_date.is_(None),
                    BdTeam.status == "active")
            .order_by(BdTeam.id.asc()).all())


def is_leader_of(db: Session, person_code: Optional[str],
                 team_id: Optional[int]) -> bool:
    """越权校验**唯一入口**（车站任务的分派/进展也走这里）。"""
    if not person_code or not team_id:
        return False
    return db.query(BdTeamMember).filter(
        BdTeamMember.team_id == team_id,
        BdTeamMember.person_code == person_code,
        BdTeamMember.role == ROLE_LEADER,
        BdTeamMember.end_date.is_(None)).first() is not None


def teams_of_person(db: Session, person_code: Optional[str]) -> List[dict]:
    """该人的全部现役身份（队长/队员），带队名。"""
    if not person_code:
        return []
    rows = (db.query(BdTeamMember, BdTeam)
            .join(BdTeam, BdTeam.id == BdTeamMember.team_id)
            .filter(BdTeamMember.person_code == person_code,
                    BdTeamMember.end_date.is_(None))
            .order_by(BdTeamMember.role.asc()).all())
    return [{"team": t, "role": m.role, "start_date": m.start_date}
            for m, t in rows]


def person_options(db: Session, kw: str = "") -> List[dict]:
    """人员下拉（圈选成员用）：姓名 + 编号 + **有无账号** + **员工状态**。"""
    q = db.query(Person)
    if kw:
        like = "%%%s%%" % kw.strip()
        q = q.filter(or_(Person.display_name.like(like),
                         Person.code.like(like)))
    people = q.order_by(Person.code.asc()).all()
    st: Dict[str, str] = {}
    for code, status in (db.query(User.person_code, User.status)
                         .filter(User.person_code.isnot(None)).all()):
        st[code] = status or "active"
    return [{"code": p.code, "display_name": p.display_name or p.code,
             "has_account": p.code in st,
             "status": st.get(p.code, ""),
             "can_work": st.get(p.code) in ("active", "leave")}
            for p in people]


def summary(db: Session) -> dict:
    """团队汇总（**一次查询**后在 Python 里聚合，避开方言差异）：

    返回 `{"n_team", "n_active_member", "no_leader"}`；`no_leader` = [(队id, 队名)]。
    """
    teams = (db.query(BdTeam.id, BdTeam.name)
             .filter(BdTeam.status == "active")
             .order_by(BdTeam.id.asc()).all())
    ids = [t[0] for t in teams]
    lead_cnt: Dict[int, int] = {i: 0 for i in ids}
    n_member = 0
    if ids:
        rows = (db.query(BdTeamMember.team_id, BdTeamMember.role)
                .filter(BdTeamMember.team_id.in_(ids),
                        BdTeamMember.end_date.is_(None)).all())
        for team_id, role in rows:
            n_member += 1
            if role == ROLE_LEADER:
                lead_cnt[team_id] = lead_cnt.get(team_id, 0) + 1
    return {"n_team": len(ids), "n_active_member": n_member,
            "no_leader": [(tid, nm) for tid, nm in teams
                          if not lead_cnt.get(tid)]}
