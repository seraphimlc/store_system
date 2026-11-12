# -*- coding: utf-8 -*-
"""能力层：一个业务能力一个函数，签名 f(db, ...) -> dict。

本层是"唯一真相"：MCP 适配层、未来 HTTP API、定时任务都调这里，
不在适配层写业务逻辑（spec §1.2 的可复用范式要求）。

注意：P0 为只读，函数签名暂为 f(db, ...)（无 actor）。spec §5.2 的
`f(db, actor, **params)` 接缝在 P1 引入 actor——此处是有意的偏差，已在
计划评审记录，P1 不得静默继承。
"""
import re
from typing import Any

from sqlalchemy import case, func, select

MONTH_PATTERN = r"^[0-9]{4}-(0[1-9]|1[0-2])$"
"""月份唯一关口：不用 \\d（会放行全角「２０２６-08」，静默返回 0 行，spec §5.4）。"""


class BadMonth(ValueError):
    pass


class BadParam(ValueError):
    """参数非法（如 sort_by/limit 取值错误）→ 适配层转 BAD_PARAM 信封。"""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint


class CapabilityError(RuntimeError):
    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint


def _next_month(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{y + 1}-01" if m == 12 else f"{y}-{m + 1:02d}"


def month_summary(db, month: str) -> dict[str, Any]:
    """某结算月正式表汇总。口径见 spec §5.4。

    数据来源 = 已结算的 formal_records。不得读 persons 表（那是全量人员，
    54 行 ≠ 本月人数），不得重新推导判重/锚点规则（那是 flow.judge_import 的职责）。
    月份过滤用范围比较，跨 SQLite/PG 方言安全（不用 substr/to_char）。
    """
    if not re.fullmatch(MONTH_PATTERN, month or ""):
        raise BadMonth(f"月份格式非法：{month!r}，应为 YYYY-MM")

    from app.models import FormalRecord

    start, end = f"{month}-01", f"{_next_month(month)}-01"
    stmt = select(
        func.count().label("formal_rows"),
        func.coalesce(func.sum(FormalRecord.points), 0).label("points_total"),
        func.coalesce(
            func.sum(case((FormalRecord.points == 1, 1), else_=0)), 0
        ).label("p1_count"),
        func.coalesce(
            func.sum(case((FormalRecord.points == 2, 1), else_=0)), 0
        ).label("p2_count"),
        func.count(func.distinct(FormalRecord.person_code)).label("persons"),
    ).where(FormalRecord.japan_date >= start, FormalRecord.japan_date < end)

    row = db.execute(stmt).one()
    return {
        "month": month,
        "formal_rows": row.formal_rows or 0,
        "points_total": row.points_total or 0,
        "p1_count": row.p1_count or 0,
        "p2_count": row.p2_count or 0,
        "persons": row.persons or 0,
    }


def month_salary(db, month: str, person: str | None = None,
                 sort_by: str = "points", limit: int = 0) -> dict[str, Any]:
    """某结算月薪资（**读已物化的 month_perf_records，不重算**）。

    为什么绝不重算：工资 = 每点单价 + 每满门槛点奖金，而这两项按月可配。
    本地库实测：2026-09 的工资是按「250/点 + 每满75点奖1250」物化的，
    但 sys_configs 里 2026-09 写着 68/3000 —— 若按当前配置重算会得到不同的钱
    （正是设计文档警告的"静默写错钱"）。故一律读物化值。

    person：按 person_code 精确或姓名包含匹配（可选）。
    sort_by：rows 排序字段，points（总点数，默认）或 amount（工资金额），降序。
    limit：截断条数，默认 0=全部；>0 只返回前 N 名（配合 sort_by 做排行）。
    """
    if not re.fullmatch(MONTH_PATTERN, month or ""):
        raise BadMonth(f"月份格式非法：{month!r}，应为 YYYY-MM")
    if sort_by not in ("points", "amount"):
        raise BadParam(f"sort_by 必须是 points 或 amount：{sort_by!r}",
                       "points=按总点数降序（默认）；amount=按工资金额降序")
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise BadParam(f"limit 必须是非负整数：{limit!r}",
                       "limit 默认 0=全部；传正整数只返回前 N 名")
    if limit < 0:
        raise BadParam(f"limit 必须是非负整数：{limit!r}",
                       "limit 默认 0=全部；传正整数只返回前 N 名")

    from app.services import perf

    rows = perf.month_perf(db, month)
    if person:
        key = person.strip()
        rows = [r for r in rows
                if r["code"] == key or key in (r["name"] or "")]
    if not rows:
        return {"month": month, "persons": 0, "total_points": 0,
                "total_salary": 0, "currency": "JPY", "per_point": None,
                "rows": [], "hint": "该月无薪资数据（合法结果，不是错误）"}

    # 排序（默认 points 降序，与 perf_ranking 同口径）+ 可选截断
    rows = sorted(rows, key=lambda r: r[sort_by] or 0, reverse=True)
    if limit > 0:
        rows = rows[:limit]

    per_point = next((r["per_point"] for r in rows if r.get("per_point")), None)
    return {
        "month": month,
        "persons": len(rows),
        "total_points": sum(r["points"] or 0 for r in rows),
        "total_salary": sum(r["amount"] or 0 for r in rows),
        "currency": "JPY",          # 金额单位为日元（円）；勿写成人民币元
        "per_point": per_point,
        "rows": [{
            "person_code": r["code"], "name": r["name"],
            "points": r["points"], "p1": r["p1"], "p2": r["p2"],
            "salary": r["amount"],
            "settle_amount": r.get("settle_amount"),
            "diff_amount": r.get("diff_amount"),
        } for r in rows],
    }
