# -*- coding: utf-8 -*-
"""**任务域 AI 总结**（近 7 天 + 环比上一个 7 天）。

用户 2026-10-06：
    "任务AI总结。过去7天的总结。加一个按钮给管理员。管理员点击AI总结后针对过去七天的数据
     做一个总结，包括环比上一个七天。"
    → 口径确认：**只任务域**、**固定中文**、**生成一次缓存当天（点开即看，可重新生成）**

分工（与"AI 派工建议"同一套路，见 `bd_assign_ai.py`）：
- **数字全部由代码算**（`metrics()`）—— 模型只负责"读数字写话"
- 模型的输出要过一遍清洗（`_clean_out`）：字段缺失/类型不对/没返回 JSON 都能兜住
- 一天的总结存 `bd_ai_summary`（**作业域自己的表**，不碰结算域 `ai_runs`）
"""
import json
from datetime import date, timedelta
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.models import BdAiSummary, BdTask, BdTaskProgress
from app.services import bd_tasks


class SummaryError(Exception):
    """生成失败（AI 未配置 / 调用失败 / 没有数据 / 模型输出不可用）。"""


METRIC_KEYS = ("done", "dispatched", "reported", "confirmed", "rejected",
               "stale", "assignees", "teams_active")


def _window(today: date, days: int):
    """近 N 天（含今天）与它的上一窗口：`(start, end, prev_start, prev_end)`。"""
    end = today
    start = today - timedelta(days=days - 1)
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days - 1)
    return start, end, prev_start, prev_end


def metrics(db: Session, days: int = 7, today: Optional[date] = None) -> Dict:
    """窗口内的任务域指标（**全部 SQL 算**，模型不许自己数数）。"""
    from sqlalchemy import func
    from app.models import BdTeam, BdTaskAssign
    d = today or bd_tasks._today()
    start, end, p_start, p_end = _window(d, days)

    def _count(q):
        return int(q.scalar() or 0)

    def pack(s, e):
        done = _count(db.query(func.count(BdTask.id)).filter(
            BdTask.done_date.isnot(None), BdTask.done_date >= s,
            BdTask.done_date <= e))
        dispatched = _count(db.query(func.count(BdTask.id)).filter(
            BdTask.assign_date.isnot(None), BdTask.assign_date >= s,
            BdTask.assign_date <= e))
        reported = _count(db.query(func.count(BdTaskProgress.id)).filter(
            BdTaskProgress.progress_date >= s, BdTaskProgress.progress_date <= e))
        confirmed = _count(db.query(func.count(BdTaskProgress.id)).filter(
            BdTaskProgress.progress_date >= s, BdTaskProgress.progress_date <= e,
            BdTaskProgress.review_status == "confirmed"))
        rejected = _count(db.query(func.count(BdTaskProgress.id)).filter(
            BdTaskProgress.progress_date >= s, BdTaskProgress.progress_date <= e,
            BdTaskProgress.review_status == "rejected"))
        assignees = _count(db.query(func.count(func.distinct(BdTaskAssign.person_code))))
        teams_active = _count(db.query(func.count(BdTeam.id)).filter(
            BdTeam.status == "active"))
        return {"done": done, "dispatched": dispatched, "reported": reported,
                "confirmed": confirmed, "rejected": rejected,
                "assignees": assignees, "teams_active": teams_active}

    now = pack(start, end)
    prev = pack(p_start, p_end)
    now["stale"] = int(bd_tasks.stale_count(db))          # 当前快照（非区间量）
    prev["stale"] = now["stale"]                          # 环比对它无意义 → 并列展示
    # 结构分布（当前快照）：按队 + 按线路 Top
    by_team = []
    for t in bd_tasks.team_board_summary(db):
        by_team.append({"team": t["team_name"], "total": t["total"],
                        "unassigned": t["unassigned"], "doing": t["doing"],
                        "done": t["done"], "stale": t["stale"]})
    rows = bd_tasks.task_board(db, tab="", limit=100000)["rows"]
    line_agg: Dict[str, int] = {}
    for r in rows:
        for ln in (r.get("line") or "").split("、"):
            ln = ln.strip()
            if ln:
                line_agg[ln] = line_agg.get(ln, 0) + 1
    top_lines = sorted(line_agg.items(), key=lambda x: -x[1])[:5]
    return {
        "days": days,
        "today": d.isoformat(),
        "now_window": [start.isoformat(), end.isoformat()],
        "prev_window": [p_start.isoformat(), p_end.isoformat()],
        "now": now,
        "prev": prev,
        "delta": {k: int(now.get(k, 0)) - int(prev.get(k, 0)) for k in METRIC_KEYS},
        "by_team": by_team,
        "top_lines": [{"line": k, "n": v} for k, v in top_lines],
        "totals": {"tasks": _count(db.query(func.count(BdTask.id)))},
    }


def build_prompt(m: Dict) -> str:
    """把指标交给模型，要求**固定中文**的 JSON 输出。"""
    return (
        "你是巡店任务的运营分析助手。下面是任务管理系统的统计数字（JSON），"
        "请**只依据这些数字**写一份简体中文总结，不要编造数字、不要提数字里没有的东西。\n"
        "要求：\n"
        "1. headline：一句话结论（≤40 字），点出最值得注意的变化；\n"
        "2. bullets：3~6 条要点，每条 ≤60 字，**带上数字和环比**（例如「完成 12 个，"
        "比上一个 7 天 +4」）；\n"
        "3. risks：1~3 条需要管理员关注的风险或建议动作（没有就给空数组）；\n"
        "4. 只输出 JSON：{\"headline\": \"…\", \"bullets\": [\"…\"], \"risks\": [\"…\"]}\n\n"
        "统计数字（now_window = 近 %d 天，prev_window = 上一个 %d 天）：\n%s"
        % (m["days"], m["days"], json.dumps(m, ensure_ascii=False))
    )


def _clean_out(data) -> Dict:
    """模型输出清洗：缺字段/类型不对/多给东西都不怕（界面只认这三种）。"""
    if not isinstance(data, dict):
        raise SummaryError("模型没返回可解析的 JSON")
    headline = str(data.get("headline") or "").strip()[:120]
    def _list(key, n):
        out = []
        for x in (data.get(key) or []):
            s = str(x).strip()
            if s:
                out.append(s[:200])
            if len(out) >= n:
                break
        return out
    bullets = _list("bullets", 8)
    risks = _list("risks", 5)
    if not headline and not bullets:
        raise SummaryError("模型输出为空")
    return {"headline": headline, "bullets": bullets, "risks": risks}


def cached(db: Session, today: Optional[date] = None) -> Optional[BdAiSummary]:
    """今天的缓存（没有就不生成 —— 页面只显示按钮）。"""
    d = today or bd_tasks._today()
    return (db.query(BdAiSummary)
            .filter(BdAiSummary.summary_date == d).first())


def generate(db: Session, *, by: str = "", days: int = 7, force: bool = False,
             timeout: int = 60) -> Dict:
    """生成（或取当天缓存）。**失败抛 SummaryError**，路由给人话提示。"""
    from app.services import ai_chat
    d = bd_tasks._today()
    if not force:
        row = cached(db, d)
        if row is not None:
            return {"cached": True, "date": d.isoformat(),
                    "summary": json.loads(row.summary_json or "{}"),
                    "metrics": json.loads(row.metrics_json or "{}")}
    if not (ai_chat.configured() if hasattr(ai_chat, "configured") else True):
        raise SummaryError("AI 未配置（.env 里没有 AI_API_KEY / AI_BASE_URL）")
    m = metrics(db, days=days, today=d)
    try:
        res = ai_chat.chat(build_prompt(m), timeout=timeout, return_usage=True)
    except Exception as e:                                   # noqa: BLE001
        raise SummaryError("AI 调用失败：%s" % str(e)[:120])
    if isinstance(res, tuple):
        text, usage = res
    else:
        text, usage = res, {}
    try:
        out = _clean_out(ai_chat.extract_json(text) if text else None)
    except SummaryError:
        raise
    except Exception as e:                                   # noqa: BLE001
        raise SummaryError("模型输出无法解析：%s" % str(e)[:80])
    usage = usage or {}
    row = cached(db, d)
    if row is None:
        row = BdAiSummary(summary_date=d, days=days)
        db.add(row)
    row.days = days
    row.metrics_json = json.dumps(m, ensure_ascii=False)
    row.summary_json = json.dumps(out, ensure_ascii=False)
    row.model = str(usage.get("model") or "")[:64]
    row.tokens = int(usage.get("total_tokens") or 0)
    row.created_by = (by or "")[:64]
    db.commit()
    return {"cached": False, "date": d.isoformat(), "summary": out, "metrics": m}


def view(db: Session, today: Optional[date] = None) -> Optional[Dict]:
    """页面用：当天缓存的总结 + 指标（没有 → None）。"""
    row = cached(db, today)
    if row is None:
        return None
    try:
        summ = json.loads(row.summary_json or "{}")
        met = json.loads(row.metrics_json or "{}")
    except Exception:                                        # noqa: BLE001
        return None
    return {"summary": summ, "metrics": met, "date": row.summary_date,
            "created_at": row.created_at, "created_by": row.created_by,
            "model": row.model, "days": row.days}
