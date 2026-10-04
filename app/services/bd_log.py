# -*- coding: utf-8 -*-
"""作业域变更日志（规格 `docs/specs-station-tasks.md` §0.2）。

用户 2026-10-03：「每个任务的变化日志；团队变化、团队成员变化、队长变化等，要记录日志」。
设计：**一张通用追加表 `bd_log`**（domain 区分），写入只有一处、时间线一次查询出全。

**只写不改不删**（追加型）：所以"员工先报 40%、队长改成 60%"这种过程永远查得到
（`bd_task_progress` 是一天一条的快照，会被覆盖，看不到改了几次、谁改的）。
"""
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models import BdLog, User

DOMAINS = ("task", "team", "member", "station")


def actor_of(db: Session, user) -> Dict[str, str]:
    """操作人 → {"actor": 登录名或编号, "actor_name": 显示名}。"""
    if user is None:
        return {"actor": "", "actor_name": ""}
    login = getattr(user, "username", "") or ""
    code = getattr(user, "person_code", "") or ""
    name = getattr(user, "display_name", "") or ""
    if not name and code and not login:
        u = db.query(User).filter(User.person_code == code).first()
        name = (u.display_name if u else "") or ""
    return {"actor": login or code, "actor_name": name or (login or code)}


def log(db: Session, domain: str, action: str, ref_id: Optional[int] = None,
        ref_label: str = "", field: str = "", old: str = "", new: str = "",
        actor: str = "", actor_name: str = "", note: str = "",
        when: Optional[datetime] = None) -> BdLog:
    """追加一条日志（**调用方不要改/删**）。同值不写：`old == new` 且都是空则跳过。"""
    row = BdLog(domain=domain, action=action, ref_id=ref_id,
                ref_label=(ref_label or "")[:128], field=field,
                old_value=str(old if old is not None else ""),
                new_value=str(new if new is not None else ""),
                actor=actor or "", actor_name=actor_name or "",
                note=note or "", created_at=when or datetime.utcnow())
    db.add(row)
    return row


def log_op(db: Session, user, domain: str, action: str,
           ref_id: Optional[int] = None, ref_label: str = "",
           field: str = "", old: str = "", new: str = "",
           note: str = "") -> BdLog:
    """带操作人的便捷写入（路由/服务层优先用它）。"""
    a = actor_of(db, user)
    return log(db, domain, action, ref_id=ref_id, ref_label=ref_label,
               field=field, old=old, new=new, actor=a["actor"],
               actor_name=a["actor_name"], note=note)


def timeline(db: Session, domain: str, ref_id: int,
             limit: int = 200) -> List[BdLog]:
    """某个对象的时间线（新→旧）。"""
    return (db.query(BdLog)
            .filter(BdLog.domain == domain, BdLog.ref_id == ref_id)
            .order_by(BdLog.created_at.desc(), BdLog.id.desc())
            .limit(limit).all())


def recent(db: Session, domain: str = "", limit: int = 200,
           offset: int = 0) -> List[BdLog]:
    """全站最近日志（可按 domain 过滤）——管理端"发生了什么"入口。"""
    q = db.query(BdLog)
    if domain in DOMAINS:
        q = q.filter(BdLog.domain == domain)
    return (q.order_by(BdLog.created_at.desc(), BdLog.id.desc())
            .limit(limit).offset(offset).all())


def ACTION_LABELS(lang: str = "zh") -> Dict[str, str]:
    """动作 → 可读标签（页面/导出共用）。"""
    zh = {"create": "创建", "update": "修改", "dispatch": "派给团队",
          "assign": "分派担当", "unassign": "取消担当", "role": "改角色",
          "state": "状态变化", "progress": "进展上报", "remove": "移出",
          "status": "状态", "rename": "改名",
          "wipe": "清空重建", "merge": "合并"}
    ja = {"create": "作成", "update": "変更", "dispatch": "チーム割当",
          "assign": "担当割当", "unassign": "担当解除", "role": "役割変更",
          "state": "状態変更", "progress": "進捗提出", "remove": "退出",
          "status": "状態", "rename": "名称変更",
          "wipe": "クリア再構築", "merge": "統合"}
    return ja if lang == "ja" else zh


def DOMAIN_LABELS(lang: str = "zh") -> Dict[str, str]:
    zh = {"task": "任务", "team": "团队", "member": "成员", "station": "车站"}
    ja = {"task": "タスク", "team": "チーム", "member": "メンバー",
          "station": "駅"}
    return ja if lang == "ja" else zh
