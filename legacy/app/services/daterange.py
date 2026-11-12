# -*- coding: utf-8 -*-
"""日期范围小工具（单一来源）。

原先"月份边界"在 daily_report / perf / period 里各写一遍，
"正式表日期范围"在三处各查一遍 —— 收敛到这里，避免口径漂移。
"""
from datetime import date
from typing import Optional, Tuple


def month_bounds(month: str) -> Tuple[date, date]:
    """'YYYY-MM' → (本月 1 日, 下月 1 日)；用于 `>= start AND < next` 的区间查询。"""
    y, m = int(month[:4]), int(month[5:7])
    if m == 12:
        return date(y, 12, 1), date(y + 1, 1, 1)
    return date(y, m, 1), date(y, m + 1, 1)


def formal_date_range(db, import_id: Optional[int] = None) -> Tuple[Optional[date], Optional[date]]:
    """正式表的日期范围（全局，或指定文件）；无数据 → (None, None)。

    走 `ix_formal_japan_date` 索引做 min/max（评审实测：无索引时 3 万行全表扫描，
    而它出现在每个报告/对比/导出请求里）。
    """
    from sqlalchemy import func

    from app.models import FormalRecord
    q = db.query(func.min(FormalRecord.japan_date),
                 func.max(FormalRecord.japan_date))
    if import_id is not None:
        q = q.filter(FormalRecord.import_id == import_id)
    row = q.first()
    if row and row[0] and row[1]:
        return row[0], row[1]
    return None, None
