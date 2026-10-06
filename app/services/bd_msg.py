# -*- coding: utf-8 -*-
"""站内消息（用户 2026-10-03）：

- 任务进展**确认/调整结果要通知员工**（系统发件人）
- 管理员可以发消息给员工；队长可以发消息给**本队**员工
- 收件人可以是**一个 / 多个 / 全体**
- 「类似其它系统的消息模块」→ 一条消息 + N 个收件人、按人已读、未读红点、点开跳转

本模块只写 `bd_message` / `bd_message_recipient` 两张表（+ `bd_log` 留痕）。
"""
from datetime import datetime
from typing import Dict, List, Optional, Sequence

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import BdMessage, BdMessageRecipient, Person, User

KIND_SYSTEM = "system"
KIND_ADMIN = "admin"
KIND_LEADER = "leader"
SCOPES = ("manual", "task_review", "announce")
MAX_RECIPIENTS = 1000


class MsgError(Exception):
    """消息模块的操作错误（页面直接显示）。"""


# ---------------- 收件人范围（数据隔离唯一入口） ----------------

def recipients_for(db: Session, user, mode: str = "pick",
                   codes: Optional[Sequence[str]] = None,
                   team_id: Optional[int] = None) -> List[str]:
    """**谁可以发给谁**的唯一判定（页面候选与发送校验共用）。

    - 管理员：`pick`（指定的人）/ `all`（全体在岗员工）
    - 队长：**只能发本队现役成员**（`pick` 也要落在本队内，越界直接拒绝）
    - 员工：不能发（`MsgError`）
    """
    from app.services import bd_teams
    if user is None:
        raise MsgError("未登录")
    role = getattr(user, "role", "") or ""
    if role == "staff":
        raise MsgError("员工不能发消息给自己以外的范围")
    if mode == "all":
        if role != "admin":
            # 队长的"全体" = 我的队全体
            codes = _my_team_codes(db, user)
            if not codes:
                raise MsgError("你不是任何队的队长，没有可发送的队员")
            return codes[:MAX_RECIPIENTS]
        rows = (db.query(User.person_code)
                .filter(User.person_code.isnot(None),
                        User.status.in_(("active", "leave"))).all())
        out = sorted({c for (c,) in rows if c})
        if not out:
            raise MsgError("没有可发送的员工")
        return out[:MAX_RECIPIENTS]
    wanted = [c for c in dict.fromkeys(codes or []) if c]
    if not wanted:
        raise MsgError("请选择收件人")
    if role == "admin":
        return wanted[:MAX_RECIPIENTS]
    allowed = set(_my_team_codes(db, user))
    bad = [c for c in wanted if c not in allowed]
    if bad:
        names = [n or c for c, n in
                 db.query(Person.code, Person.display_name)
                 .filter(Person.code.in_(bad)).all()]
        raise MsgError("只能发给自己队里的队员，越界：%s" % "、".join(names or bad))
    return wanted[:MAX_RECIPIENTS]


def _my_team_codes(db: Session, user) -> List[str]:
    from app.services import bd_teams
    codes: List[str] = []
    for t in bd_teams.leader_teams(db, getattr(user, "person_code", None)):
        for m in bd_teams.team_members(db, t.id):
            if m["person_code"] not in codes:
                codes.append(m["person_code"])
    return codes


def candidates(db: Session, user) -> List[dict]:
    """发消息页的候选收件人（带姓名/状态；**队长只列本队**）。"""
    from app.services import bd_teams
    role = getattr(user, "role", "") or ""
    status = {}
    for code, st in (db.query(User.person_code, User.status)
                     .filter(User.person_code.isnot(None)).all()):
        status[code] = st or "active"
    if role == "admin":
        people = (db.query(Person).order_by(Person.code.asc()).all())
        return [{"code": p.code, "display_name": p.display_name or p.code,
                 "status": status.get(p.code, ""),
                 "can_work": status.get(p.code) in ("active", "leave", None)}
                for p in people]
    if role == "leader":
        names = {p.code: (p.display_name or p.code)
                 for p in db.query(Person).all()}
        return [{"code": c, "display_name": names.get(c, c),
                 "status": status.get(c, ""),
                 "can_work": status.get(c) in ("active", "leave")}
                for c in _my_team_codes(db, user)]
    return []


# ---------------- 发送 / 读取 ----------------

def send(db: Session, user, person_codes: Sequence[str],
         title: str, body: str = "", url: str = "", scope: str = "manual",
         ref_type: str = "", ref_id: Optional[int] = None,
         sender_kind: Optional[str] = None, sender_name: str = "",
         system_title: str = "") -> BdMessage:
    """发一条消息（一条消息 + N 个收件人）。返回消息头。"""
    from app.services import bd_log
    t = (title or "").strip() or (system_title or "").strip()
    if not t:
        raise MsgError("请填写消息标题")
    codes = [c for c in dict.fromkeys(person_codes or []) if c]
    if not codes:
        raise MsgError("没有收件人")
    if len(codes) > MAX_RECIPIENTS:
        raise MsgError("一次最多发给 %d 人" % MAX_RECIPIENTS)
    kind = sender_kind or ((getattr(user, "role", "") or "system")
                           if user is not None else KIND_SYSTEM)
    if kind == "staff":
        kind = KIND_SYSTEM
    msg = BdMessage(sender_kind=kind,
                    sender=(getattr(user, "username", "") or ""),
                    sender_name=(sender_name
                                 or getattr(user, "display_name", "")
                                 or getattr(user, "username", "")),
                    title=t[:200], body=(body or "").strip(), url=(url or ""),
                    scope=(scope if scope in SCOPES else "manual"),
                    ref_type=ref_type or "", ref_id=ref_id)
    db.add(msg)
    db.flush()
    for c in codes:
        db.add(BdMessageRecipient(message_id=msg.id, person_code=c))
    db.flush()
    bd_log.log_op(db, user, "message", "create", ref_id=msg.id,
                  ref_label=t[:128], field="收件人",
                  new="%d 人" % len(codes), note=(msg.body or "")[:200])
    return msg


def lang_of(db: Session, person_code: str) -> str:
    """收件人的界面语言（`users.lang`；没设置/查不到 → zh）。"""
    if not person_code:
        return "zh"
    from app.models import User
    row = (db.query(User.lang).filter(User.person_code == person_code).first())
    lg = (row[0] if row else "") or ""
    return lg if lg in ("zh", "ja") else "zh"


def send_localized(db: Session, user, person_codes: Sequence[str],
                   build, **kw) -> list:
    """**按收件人语言分组发送**（同一批人语言不同就发多条）。

    为什么必须分组：消息标题/正文是**落库**的（收件人共享一条），
    没法"渲染时按看的人翻译"—— 所以发送时就按 `users.lang` 分好组。
    `build(lang) -> (title, body)`。
    """
    groups: dict = {}
    for c in dict.fromkeys(person_codes or []):
        if c:
            groups.setdefault(lang_of(db, c), []).append(c)
    out = []
    for lg, codes in groups.items():
        title, body = build(lg)
        out.append(send(db, user, codes, title, body, **kw))
    return out


def notify_task_progress(db: Session, task, action: str, old_pct,
                         new_pct, by_user=None, note: str = "",
                         station: str = "", reporter: str = "") -> Optional[BdMessage]:
    """任务进展的**确认/调整结果通知员工**（用户明确要求）。

    `action`：`confirmed`（认可）/ `adjusted`（改了值）/ `rejected`（**驳回**：把 100% 退回）

    ⚠️ **收件人 = 真正上报的人 ∪ 当前担当**（2026-10-06 复盘）：
    原来只发 `assignees_of(task)` → 队长**回收/改派之后**，消息发给"现在挂着的人"
    （他根本没上报过），而**上报的人收不到**。`reporter` 传上报人的**用户名**
    （`bd_task_progress.reported_by`），这里换算成人员编号。
    """
    from app.services import bd_tasks
    codes = []
    if reporter:
        from app.models import User as _U
        pc = (db.query(_U.person_code)
              .filter(_U.username == reporter).scalar())
        if pc:
            codes.append(pc)
    for r in bd_tasks.assignees_of(db, task.id):
        if r.get("code"):
            codes.append(r["code"])
    codes = list(dict.fromkeys(codes))            # 去重（上报人可能也是担当）
    if not codes:
        return None
    who = (getattr(by_user, "display_name", "") or
           getattr(by_user, "username", "") or "")
    old_s = ("%s" % old_pct) if old_pct is not None else "—"
    who_s = who or "队长"

    def build(lang: str):
        """按**收件人语言**渲染标题/正文（消息落库 → 必须在发送时定语言）。"""
        from app.i18n import render_msg as R
        if action == "confirmed":
            return (R("任务进展已确认：%s", station, lang=lang),
                    R("你上报的 %s%% 已被%s确认。", new_pct, who_s, lang=lang))
        if action == "rejected":
            return (R("任务进展被驳回：%s", station, lang=lang),
                    R("你上报的 %s%% 被%s驳回，进度改回 %s%%。%s",
                      old_s, who_s, new_pct, note or "", lang=lang))
        return (R("任务进展已调整：%s", station, lang=lang),
                R("你上报的 %s%%，被%s调整为 %s%%。%s",
                  old_s, who_s, new_pct, note or "", lang=lang))

    msgs = send_localized(db, by_user, codes, build,
                          url="/tasks/%d" % task.id, scope="task_review",
                          ref_type="task", ref_id=task.id,
                          sender_kind=KIND_SYSTEM if by_user is None else None)
    return msgs[0] if msgs else None


def inbox(db: Session, person_code: str, unread_only: bool = False,
          page: int = 1, per: int = None) -> dict:
    """我收到的消息（新→旧，**分页**），带已读标记。

    以前是 `limit=100` 硬截断（第 101 条起永远看不到）；2026-10-03 改真分页。
    """
    from app.services import paging as _pg
    q = (db.query(BdMessageRecipient, BdMessage)
         .join(BdMessage, BdMessage.id == BdMessageRecipient.message_id)
         .filter(BdMessageRecipient.person_code == person_code))
    if unread_only:
        q = q.filter(BdMessageRecipient.read_at.is_(None))
    pg = _pg.paginate(q.order_by(BdMessage.created_at.desc(),
                                 BdMessage.id.desc()), page,
                      per or _pg.PER_DEFAULT)
    pg["rows"] = [{"msg": m, "to": r, "unread": r.read_at is None}
                  for r, m in pg["rows"]]
    return pg


def sent(db: Session, user, page: int = 1, per: int = None) -> dict:
    """我发出的消息 + 已读人数（管理员/队长看谁读了；**分页**）。"""
    from app.services import paging as _pg
    login = getattr(user, "username", "") or ""
    pg = _pg.paginate(db.query(BdMessage).filter(BdMessage.sender == login)
                      .order_by(BdMessage.created_at.desc(),
                                BdMessage.id.desc()), page,
                      per or _pg.PER_DEFAULT)
    rows = pg["rows"]
    if not rows:
        pg["rows"] = []
        return pg
    ids = [m.id for m in rows]
    agg = dict(db.query(BdMessageRecipient.message_id,
                        func.count(BdMessageRecipient.id))
               .filter(BdMessageRecipient.message_id.in_(ids))
               .group_by(BdMessageRecipient.message_id).all())
    read = dict(db.query(BdMessageRecipient.message_id,
                         func.count(BdMessageRecipient.id))
                .filter(BdMessageRecipient.message_id.in_(ids),
                        BdMessageRecipient.read_at.isnot(None))
                .group_by(BdMessageRecipient.message_id).all())
    pg["rows"] = [{"msg": m, "n": agg.get(m.id, 0),
                   "read": read.get(m.id, 0)} for m in rows]
    return pg


def unread_count(db: Session, person_code: Optional[str]) -> int:
    if not person_code:
        return 0
    return int(db.query(func.count(BdMessageRecipient.id))
               .filter(BdMessageRecipient.person_code == person_code,
                       BdMessageRecipient.read_at.is_(None)).scalar() or 0)


def get_for(db: Session, message_id: int,
            person_code: str) -> Optional[BdMessageRecipient]:
    """取"我"的那一行收件记录（**越权返回 None**）。"""
    return (db.query(BdMessageRecipient)
            .filter(BdMessageRecipient.message_id == message_id,
                    BdMessageRecipient.person_code == person_code).first())


def mark_read(db: Session, message_id: int, person_code: str) -> bool:
    row = get_for(db, message_id, person_code)
    if row is None or row.read_at is not None:
        return False
    row.read_at = datetime.utcnow()
    db.flush()
    return True


def mark_all_read(db: Session, person_code: str) -> int:
    n = (db.query(BdMessageRecipient)
         .filter(BdMessageRecipient.person_code == person_code,
                 BdMessageRecipient.read_at.is_(None))
         .update({BdMessageRecipient.read_at: datetime.utcnow()},
                 synchronize_session=False))
    db.flush()
    return int(n or 0)


def unread_by_kind(db: Session, person_code: Optional[str]) -> Dict[str, int]:
    """未读分类（tabbar / 页面角标用）。"""
    if not person_code:
        return {"task_review": 0, "manual": 0, "announce": 0}
    rows = (db.query(BdMessage.scope, func.count(BdMessageRecipient.id))
            .join(BdMessageRecipient,
                  BdMessageRecipient.message_id == BdMessage.id)
            .filter(BdMessageRecipient.person_code == person_code,
                    BdMessageRecipient.read_at.is_(None))
            .group_by(BdMessage.scope).all())
    out = {"task_review": 0, "manual": 0, "announce": 0}
    for scope, n in rows:
        out[scope or "manual"] = int(n or 0)
    return out
