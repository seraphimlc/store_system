# -*- coding: utf-8 -*-
"""角色能力（权限表 · 用户 2026-10-03 选 (a) 方案）。

**一份定义，两处使用**：服务层判权 + 页面按钮显隐，都读这里（落库在 `bd_role_cap`）。

- 表为空时**回退到 `DEFAULT_CAPS`**（读路径不写库；测试用 `create_all` 建表没有种子行，
  本地库/线上由迁移 `d1e2f3a4b5c6` 种子）。要落库显式调 `seed(db)`。
- 进程内缓存：`clear_cache()` 在能力被改动后调用。

能力清单（保持小而实用，够用就好）：

| 能力 | 含义 |
|---|---|
| `task.view_all` | 看**全部**任务（管理员） |
| `task.view_team` | 看**本队**任务（队长） |
| `task.dispatch` | 把任务派给团队 / 改分配日期（管理员） |
| `task.assign` | 分派担当（管理员 / 队长） |
| `task.report` | **上报自己担当的**任务进展（管理员 / 队长 / 队员） |
| `task.adjust` | **调整**本队任何任务的进展（管理员 / 队长） |
| `team.manage` | 建队 / 圈人 / 指定队长（管理员） |
| `station.manage` | 车站与任务主数据（管理员） |
| `log.view` | 看变更日志（管理员 / 队长） |
"""
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models import BdRoleCap

ROLE_ADMIN = "admin"
ROLE_LEADER = "leader"
ROLE_STAFF = "staff"
ROLES = (ROLE_ADMIN, ROLE_LEADER, ROLE_STAFF)

#: (能力键, 中文标签, 日文标签)
CAPABILITIES: List[Tuple[str, str, str]] = [
    ("task.view_all", "查看全部任务", "全タスク閲覧"),
    ("task.view_team", "查看本队任务", "自チームのタスク閲覧"),
    ("task.dispatch", "派活给团队", "チームへの割当"),
    ("task.assign", "分派担当", "担当の割当"),
    ("task.report", "上报自己的进展", "自分の進捗提出"),
    ("task.adjust", "调整进展", "進捗の調整"),
    ("team.manage", "团队管理", "チーム管理"),
    ("station.manage", "车站/任务管理", "駅・タスク管理"),
    ("log.view", "查看变更日志", "変更ログ閲覧"),
]

#: 缺省能力（与迁移里的种子保持一致）
DEFAULT_CAPS: Dict[str, Dict[str, bool]] = {
    ROLE_ADMIN: {k: True for k, _, _ in CAPABILITIES},
    ROLE_LEADER: {k: (k in ("task.view_team", "task.assign", "task.report",
                            "task.adjust", "log.view"))
                  for k, _, _ in CAPABILITIES},
    ROLE_STAFF: {k: (k == "task.report") for k, _, _ in CAPABILITIES},
}

_cache: Optional[Dict[str, Dict[str, bool]]] = None


def clear_cache() -> None:
    global _cache
    _cache = None


def _load(db: Session) -> Dict[str, Dict[str, bool]]:
    """读库；表为空（或没有这张表）→ 回退缺省。**只读不写**。"""
    global _cache
    if _cache is not None:
        return _cache
    caps = {r: dict(DEFAULT_CAPS[r]) for r in ROLES}
    try:
        rows = db.query(BdRoleCap).all()
    except Exception:                     # 表还没建（极端情况）：直接用缺省
        rows = []
    if rows:
        for row in rows:
            caps.setdefault(row.role, {})[row.capability] = bool(row.allowed)
    _cache = caps
    return caps


def seed(db: Session, commit: bool = False) -> int:
    """把缺省能力落库（幂等；表为空或缺失的行才补）。返回写入行数。"""
    have = {(r.role, r.capability) for r in db.query(BdRoleCap).all()}
    n = 0
    for role, caps in DEFAULT_CAPS.items():
        for cap, allowed in caps.items():
            if (role, cap) in have:
                continue
            db.add(BdRoleCap(role=role, capability=cap, allowed=allowed))
            n += 1
    if commit:
        db.commit()
    clear_cache()
    return n


def caps_of(db: Session, role: str) -> Dict[str, bool]:
    return dict(_load(db).get(role or "", {}))


def allowed(db: Session, role: str, cap: str) -> bool:
    return bool(_load(db).get(role or "", {}).get(cap, False))


def can(db: Session, user, cap: str) -> bool:
    """当前用户是否有某能力（**只看角色能力**；数据范围另由 is_leader_of 等判定）。"""
    if user is None:
        return False
    return allowed(db, getattr(user, "role", "") or "", cap)


def user_caps(db: Session, user) -> Dict[str, bool]:
    return caps_of(db, getattr(user, "role", "") if user is not None else "")


def capability_labels(lang: str = "zh") -> Dict[str, str]:
    """能力键 → 可读标签（页面/导出共用）。"""
    return {k: (ja if lang == "ja" else zh) for k, zh, ja in CAPABILITIES}
