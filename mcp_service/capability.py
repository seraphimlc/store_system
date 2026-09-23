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
