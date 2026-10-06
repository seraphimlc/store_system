# -*- coding: utf-8 -*-
"""**员工每日自报的跨域编排层**（点数 + 当天任务进度，一次提交）。

用户 2026-10-06 口径：
    "每天队员自报的时候，一是报点数，二是报任务完成的进度情况，**一起提交自报**"

为什么要单独一个模块（而不是塞进某个域的服务里）：
- 「点数」属于**员工填报域**（`daily_report` / `staff_daily_reports`）
- 「任务进度」属于**作业域**（`bd_tasks` / `bd_*`）
- 一次提交要写两边，**必须同一个事务**（要么都成、要么都不成）

所以这里做**唯一一处**跨域编排：两个域的服务各自保持干净（互不引用），
只有本模块同时 import 两边。铁律仍然成立：
**结算域四表（formal_records / person_daily_stats / month_perf_records /
payroll_period_rows）一张都不碰，也从不读 `bd_*`；作业域代码不 import 本模块。**
"""
from datetime import date
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.services import bd_tasks, daily_report
from app.services.date_plan import jst_today


def today_code(user) -> str:
    return (getattr(user, "person_code", None) or "").strip()


def view(db: Session, user, on_date: Optional[date] = None) -> dict:
    """自报页要渲染的东西：当天要报的任务 + 今天的点数（已填过就带出来）。"""
    code = today_code(user)
    d = on_date or jst_today()
    rows: List[dict] = []
    perms: Dict[int, dict] = {}
    if code:
        rows = [r for r in bd_tasks.member_tasks(db, code, today=d)
                if r["state"] != bd_tasks.STATE_DONE]
        if rows:
            perms = bd_tasks.can_reject_maps(db, user, [r["task"] for r in rows],
                                             rows=rows)
    report = daily_report.today_report(db, code) if code else None
    return {
        "rows": rows,
        "perm": perms,
        "report": report,
        "reportable": [r for r in rows if perms.get(r["task"].id, {}).get("report")],
        "date": d,
    }


def submit(db: Session, user, *, area: str = "", p1_cnt=0, p2_cnt=0,
           items: Optional[List[dict]] = None,
           on_date: Optional[date] = None) -> dict:
    """**一页一次提交**：点数 + N 个任务的当天进度（同一事务）。

    `items` = `[{"task_id": int, "pct": int, "note": str, "store_count": int|str}, ...]`
    （只有真填了的才传；**做到 100% 时必须给 `store_count`**，允许 0）
    任一条失败（没权限 / 被队长锁定 / 值非法）→ 抛异常，**调用方负责 rollback**，
    数据库里不会留半截数据。
    """
    code = today_code(user)
    if not code:
        raise ValueError("账号未绑定员工编号，无法自报")
    d = on_date or jst_today()
    # ① 点数（不 commit，等进度也写完一起提交）
    report = daily_report.upsert_today_core(db, user, area=area,
                                            p1_cnt=p1_cnt, p2_cnt=p2_cnt)
    # ② 当天任务进度
    #    ⚠️ **无变化就跳过**：统一表单会把所有滑块都提交上来，其中没动过的
    #    （值 = 该任务当前生效进度）不该写成一条新的"待确认"（2026-10-06 浏览器实测发现：
    #    5 个滑块只动了 2 个，却报了"5 个任务进度"，还会给队长塞一堆 0% 待确认）
    from app.models import BdTask as _BdTask
    done = 0
    skipped = 0
    for it in (items or []):
        tid = int(it.get("task_id") or 0)
        if not tid:
            continue
        pct = it.get("pct")
        if pct is None or str(pct).strip() == "":
            continue                      # 没填的任务跳过（不算失败）
        t = db.get(_BdTask, tid)
        if t is None:
            continue
        try:
            no_change = (t.pct is not None and int(t.pct) == int(pct))
        except (TypeError, ValueError):
            no_change = False
        if no_change and not (it.get("note") or "").strip():
            skipped += 1                  # 进度没变、也没写备注 → 无事发生
            continue
        bd_tasks.save_progress(db, tid, pct, (it.get("note") or "").strip(),
                               by=getattr(user, "username", "") or "",
                               actor_user=user, on_date=d,
                               # 完成（100%）必填店铺数（用户 2026-10-06；允许 0）
                               store_count=it.get("store_count"),
                               require_store_count=True)
        done += 1
    # ③ 一次提交
    db.commit()
    daily_report.after_today_write(db, code, report.report_date)
    return {"p1": report.p1_cnt, "p2": report.p2_cnt, "tasks": done,
            "skipped": skipped, "report_date": report.report_date}
