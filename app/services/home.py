# -*- coding: utf-8 -*-
"""全站落点唯一来源（`landing_home`）。

背景（规格 `docs/specs-team-management.md` §3.3）：加 `leader` 之前只有
`admin`/`staff` 两个角色，落点散落在各处且写死，是自洽的；**加第三个角色会死循环**：

    ／            → 不是 staff → /dashboard
    /dashboard    → role != admin → _denied() → /login
    GET /login    → 有 session → _safe_next("") → ／
    ／            → 回到第一步  →  ERR_TOO_MANY_REDIRECTS

所以：**登录后与越权后的落点一律走这里**，不要在各处再写死 `/my/perf`、`/dashboard`。

- `admin`  → `/dashboard`
- `leader` → `/my/tasks`（队长端在**员工端**里，见规格 §6.2）
- `staff`  → `date_plan.staff_home()`（**不复制**它的逻辑，直接调用）
- 其他/未登录 → `/login`
"""
from typing import Optional

__all__ = ["landing_home", "LEADER_HOME"]


#: 队长端落点（员工端任务页）
LEADER_HOME = "/my/tasks"


def landing_home(db, user) -> str:
    """按角色给出落点；`user is None` → `/login`。

    渲染路径只读，不写库。
    """
    if user is None:
        return "/login"
    role = (getattr(user, "role", "") or "").strip()
    if role == "admin":
        return "/dashboard"
    if role == "leader":
        return LEADER_HOME
    if role == "staff":
        from app.services import date_plan
        try:
            return date_plan.staff_home(db, user)
        except Exception:       # 落点绝不能因为异常把人卡住
            return "/my/perf"
    return "/login"
