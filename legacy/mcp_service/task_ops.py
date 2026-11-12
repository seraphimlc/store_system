# -*- coding: utf-8 -*-
"""MCP 作业域（团队 / 车站 / 任务）能力层。

**只做数据**：入参 → 结果 dict（或抛业务异常），不碰信封/审计/授权
（那些在 `scenario_ops.py` 的 `_read` / `_write` 里统一处理）。

口径来源（与 Web 端**同一套服务层**，绝不另写一套）：
- `app/services/bd_tasks.py`：任务/担当/进展/审核/回收/退回池
- `app/services/bd_teams.py`：队伍与成员
- `app/services/self_report.py`：点数 + 进度**一次提交**（跨域编排层）
- `app/services/daily_report.py`：每日点数

用法约定：调用方负责 `db.commit()`（写操作成功后统一提交）——
这里只 `flush()`，方便调用方决定事务边界。
"""
from __future__ import annotations

from datetime import date
from typing import Any

JST_TODAY = None                     # 延迟 import，避免 MCP venv 启动期依赖 app


def _today() -> date:
    from app.services.date_plan import jst_today
    return jst_today()


def _task_brief(r: dict) -> dict:
    """把 Web 端的任务行压成 MCP 用的精简结构（字段少而稳）。"""
    t = r["task"]
    return {
        "task_id": t.id,
        "station": r.get("station_name"),
        "line": r.get("line"),
        "team": r.get("team_name"),
        "assignees": [a.get("name") for a in (r.get("assignees") or [])],
        "state": r.get("state"),
        "pct": r.get("pct"),
        "review_status": r.get("review_status"),
        "last_reported_pct": r.get("last_reported_pct"),
        "last_date": (r.get("last_date").isoformat() if r.get("last_date") else None),
        "start_date": (r.get("start_date").isoformat() if r.get("start_date") else None),
        "done_date": (r.get("done_date").isoformat() if r.get("done_date") else None),
        "assign_date": (t.assign_date.isoformat() if t.assign_date else None),
        "dispatch_date": (r.get("dispatch_date").isoformat()
                          if r.get("dispatch_date") else None),
        "carried": bool(r.get("carried")),          # 昨天没做完自动延续过来的
        "stale": bool(r.get("stale")),
        "days_since": r.get("days_since"),
    }


def my_tasks(db, person_code: str) -> dict:
    """**我的任务**（今天派的 ∪ 之前派给我但没做完的）+ 今天已填的点数。"""
    from app.services import bd_tasks, daily_report
    rows = [r for r in bd_tasks.member_tasks(db, person_code)
            if r["state"] != bd_tasks.STATE_DONE]
    done = [r for r in bd_tasks.member_tasks(db, person_code)
            if r["state"] == bd_tasks.STATE_DONE]
    rep = daily_report.today_report(db, person_code)
    d = _today()
    return {
        "date": d.isoformat(),
        "open": [_task_brief(r) for r in rows],
        "done_count": len(done),
        "n_open": len(rows),
        "daily_report": ({"area": rep.area, "p1_cnt": rep.p1_cnt,
                          "p2_cnt": rep.p2_cnt, "total": rep.total_cnt}
                         if rep is not None else None),
        "hint": "open = 今天要做的（含昨天没做完自动延续的）；"
                "daily_report = 今天已填的点数（可空）",
    }


def self_report(db, actor, *, area: str = "", p1_cnt: Any = 0, p2_cnt: Any = 0,
                items: list | None = None) -> dict:
    """**点数 + 任务进度一次提交**（与 Web 端同一编排层，同一事务）。"""
    from app.services import self_report as sr
    r = sr.submit(db, actor, area=area, p1_cnt=p1_cnt, p2_cnt=p2_cnt,
                  items=items or [])
    return {"date": r["report_date"].isoformat(), "p1_cnt": r["p1"],
            "p2_cnt": r["p2"], "tasks_reported": r["tasks"],
            "tasks_skipped": r.get("skipped", 0)}


def _resolve_person(db, person: str):
    """工号（精确）或姓名（包含）→ Person 行；找不到抛 ValueError（→ BAD_PARAM）。"""
    from app.models import Person
    key = (person or "").strip()
    if not key:
        raise ValueError("person 不能为空：传工号（精确）或姓名（包含）")
    rows = db.query(Person).all()
    hit = next((p for p in rows if p.code == key), None)
    if hit is None:
        cands = [p for p in rows if key in (p.display_name or "")]
        if len(cands) > 1:
            raise ValueError("姓名 %r 匹配到多人（%s），请传工号"
                             % (key, "、".join(p.code for p in cands[:5])))
        hit = cands[0] if cands else None
    if hit is None:
        raise ValueError("未找到人员：%r（可先用 visit_staff(view='list') 查编号）" % key)
    return hit


def day_report(db, actor, *, person: str, on_date=None, action: str = "view",
               area: Any = None, p1_cnt: Any = None, p2_cnt: Any = None,
               items: list | None = None) -> dict:
    """**管理员改 / 查某员工某天的自报**（点数 + 当天该员工的任务进展）。

    口径与 Web 端 `/staff-reports` 的「补录 / 修改自报」**完全同一套**（走跨域编排层
    `self_report.admin_day_*`）：
    - `action="view"`：那天已有自报 → 把点数 + 那天的进展行（含"能不能改"）列出来；
    - `action="save"`：那天已有自报 → 点数 + `items` 里**真改了**的进展一次保存（同一事务）；
      那天**没有**自报 → 只能补录点数，带 `items` 直接报错（用户 2026-10-10 口径②）。
    """
    from app.services import self_report as sr
    from app.services.date_plan import jst_today
    d = on_date or jst_today()
    if hasattr(d, "strip"):                      # 允许传 "YYYY-MM-DD" 字符串
        from datetime import date as _date
        d = _date.fromisoformat(str(d).strip())
    p = _resolve_person(db, person)
    if action == "view":
        v = sr.admin_day_view(db, p.code, d)
        rep = v["report"]
        return {
            "person_code": p.code, "name": p.display_name or p.code,
            "date": d.isoformat(), "is_backfill": bool(v["is_backfill"]),
            "report": ({"area": rep.area or "", "p1_cnt": rep.p1_cnt,
                        "p2_cnt": rep.p2_cnt, "total_cnt": rep.total_cnt,
                        "source": rep.source} if rep is not None else None),
            "progress": [{"task_id": r["task_id"], "station": r["station"],
                          "line": r["line"], "pct": r["pct"],
                          "reported_pct": r["reported_pct"],
                          "review_status": r["review_status"],
                          "review_label": r["review_label"],
                          "editable": r["editable"], "note": r["note"]}
                         for r in v["rows"]],
            "hint": ("is_backfill=true = 那天还没有自报：只能补录点数，不能补录任务进展"
                     if v["is_backfill"] else
                     "editable=false 的进展 = 队长还没处理过，管理员暂不能改"),
        }
    if action != "save":
        raise ValueError("未知 action：%r（可选：view / save）" % action)
    if p1_cnt is None or p2_cnt is None:
        raise ValueError("save 必须给 p1_cnt 与 p2_cnt（那天没有自报时就是补录点数）")
    r = sr.admin_day_save(db, actor, person_code=p.code, on_date=d,
                          area=("" if area is None else area),
                          p1_cnt=p1_cnt, p2_cnt=p2_cnt, items=items or [])
    return {"person_code": p.code, "name": p.display_name or p.code,
            "date": r["report_date"].isoformat(), "1点": r["p1"], "2点": r["p2"],
            "tasks_changed": r["tasks"], "created": r["created"],
            "skipped": r["skipped"],
            "hint": "created=true = 那天原来没有自报（补录，只写了点数）"}


def report_one(db, actor, *, task_id: int, pct: Any, note: str = "",
               store_count: Any = None) -> dict:
    """单条上报/调整进展（走 `save_progress` 的完整校验：锁定、归属、值域、**店铺数**）。

    ⚠️ 队员把任务报到 100% 时**必须给 `store_count`**（允许 0）——判定在服务层按身份做，
    所以这里不用额外传开关（2026-10-06）。
    """
    from app.services import bd_tasks
    r = bd_tasks.save_progress(db, int(task_id), pct, note,
                               by=getattr(actor, "username", "") or "",
                               actor_user=actor, store_count=store_count)
    return {"task_id": int(task_id), "pct": r.get("pct"), "state": r.get("state"),
            "review_status": r.get("review_status"),
            "store_count": r.get("store_count")}


def team_tasks(db, team_ids: list, *, tab: str = "", kw: str = "",
               line_id: int | None = None) -> dict:
    """**本队任务**（按 tab / 线路 / 关键词筛）+ 待确认队列 + tab 计数。"""
    from app.services import bd_tasks
    ids = [int(x) for x in (team_ids or [])]
    if not ids:
        return {"teams": [], "open": [], "done": [], "pending": [],
                "counts": {"unassigned": 0, "doing": 0, "done": 0, "pending": 0}}
    rows = bd_tasks.team_tasks(db, ids, tab=tab, kw=kw, line_id=line_id)
    all_rows = bd_tasks.team_tasks(db, ids, tab="")
    pending = [r for r in all_rows if r.get("pending_review")]
    return {
        # 队长 tab 口径 = 按有没有分人（与 Web 端一致）
        # ⚠️ 先排除已完成：否则"已完成但没分人"会同时算进未分配和已完成
        "counts": {
            "unassigned": sum(1 for r in all_rows
                              if not r["assignees"] and r["state"] != "done"),
            "doing": sum(1 for r in all_rows
                         if r["assignees"] and r["state"] != "done"),
            "done": sum(1 for r in all_rows if r["state"] == "done"),
            "pending": len(pending),
        },
        "rows": [_task_brief(r) for r in rows],
        "pending": [_task_brief(r) for r in pending],
        "hint": "tab 口径：未分配=本队未完成且没分人；进行中=已分人；done=pct=100",
    }


def assign(db, actor, *, task_id: int, person_codes: list) -> dict:
    """**派工 / 改派 / 回收（空置）**：`person_codes=[]` 就是回收（进展保留）。"""
    from app.services import bd_tasks
    r = bd_tasks.assign_members(db, int(task_id), list(person_codes or []),
                                by=getattr(actor, "username", "") or "",
                                actor_user=actor)
    # assign_members 返回 {"n": 分派人数, "state": 任务状态, "warnings": [...]}
    return {"task_id": int(task_id), "n_assignees": r.get("n", 0),
            "state": r.get("state"), "warnings": r.get("warnings", [])}


def transfer(db, actor, *, task_ids: list, to_team_id: int) -> dict:
    """**转给别的队**（队长之间私下换活，用户 2026-10-06）。

    只能转自己当队长的那个队的任务；未分配/进行中都能转（已完成不转）；
    转出移出原担当但保留已上报进度；给对方队长和被移出的担当各发一条消息。
    """
    from app.services import bd_tasks
    r = bd_tasks.transfer_team_task(db, actor, list(task_ids or []),
                                    int(to_team_id),
                                    by=getattr(actor, "username", "") or "",
                                    actor_user=actor)
    return {"transferred": r["transferred"], "to_team": r["to_team"],
            "from_team": r["from_team"], "cleared": r["cleared"],
            "removed_people": r["removed_people"], "labels": r["labels"]}


def confirm_day(db, actor, *, team_ids: list | None = None,
                task_id: int | None = None, on_date: str | None = None) -> dict:
    """**确认 / 一键全确认**：给了 `task_id` 就确认那一单条，否则确认当天全部。"""
    from app.services import bd_tasks
    d = date.fromisoformat(on_date) if on_date else None
    if task_id:
        t = db.get(bd_tasks.BdTask, int(task_id))
        if t is None:
            raise ValueError("任务不存在")
        r = bd_tasks.save_progress(db, int(task_id), (t.pct or 0), "",
                                   by=getattr(actor, "username", "") or "",
                                   actor_user=actor, confirm=True, on_date=d)
        return {"confirmed": 1, "task_id": int(task_id), "pct": r.get("pct")}
    n = bd_tasks.confirm_day(db, actor,
                             team_ids=([int(x) for x in team_ids]
                                       if team_ids is not None else None),
                             on_date=d)
    return {"confirmed": n}


def reject(db, actor, *, task_id: int, pct: Any, note: str = "") -> dict:
    """**驳回**（把 100% 退回成不到 100%；员工原值保留）。"""
    from app.services import bd_tasks
    r = bd_tasks.reject_progress(db, int(task_id), pct, note,
                                 by=getattr(actor, "username", "") or "",
                                 actor_user=actor)
    return {"task_id": int(task_id), "pct": r.get("pct"), "state": r.get("state")}


def board(db, *, tab: str = "", line_id: int | None = None, kw: str = "",
          stale_only: bool = False, page: int = 1, per: int = 20) -> dict:
    """**管理端任务总表**：tab 数字 + 一行行任务 + 停滞口径 + 按队汇总。"""
    from app.services import bd_tasks
    counts = bd_tasks.tab_counts(db, line_id=line_id, kw=kw)
    d = bd_tasks.task_board(db, tab=tab, line_id=line_id, kw=kw,
                            stale_only=stale_only, limit=per,
                            offset=max(0, page - 1) * per)
    return {
        "counts": counts,
        "stale_count": bd_tasks.stale_count(db),
        "stale_days": d.get("stale_days"),
        "total": d["total"], "page": page, "per": per,
        "rows": [_task_brief(r) for r in d["rows"]],
        "by_team": bd_tasks.team_board_summary(db),
        "hint": "counts: unassigned=车站池 / assigned=已派队未完成 / done=pct=100；"
                "stale=已分到人但 ≥N 天没提交（没分人的不算）",
    }


def return_pool(db, actor, *, task_ids: list) -> dict:
    """**管理员撤回**（回到车站池）：只撤"已派队但没分到人"的（有进展也行）。"""
    from app.services import bd_tasks
    ids = [int(x) for x in (task_ids or [])]
    r = bd_tasks.return_to_pool(db, ids, by=getattr(actor, "username", "") or "",
                                actor_user=actor)
    return {"returned": r.get("n", 0), "skipped": r.get("skipped", [])}
