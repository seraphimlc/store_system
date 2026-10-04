# -*- coding: utf-8 -*-
"""列表分页（**全站唯一实现**，别各页面自己写一套）。

2026-10-03 用户："你就不能做个分页吗？你查查有多少地方可以做成分页的"
→ 审计发现：车站 515 行、员工管理 55 行、店铺实体 42,140 行（`limit(500)` 硬砍）都没分页。
本模块提供 `paginate()`（SQL 层 limit/offset + total 计数）与 `qs()`（保留筛选条件的查询串，
给上一页/下一页链接用），模板端用 `_pager.html` 统一渲染。
"""
from typing import Optional

from sqlalchemy.orm import Query

#: 各页面默认每页条数（管理员看的是列表，50 行左右一屏比较舒服）
PER_DEFAULT = 50
PER_MAX = 200


class Pager(dict):
    """模板里用的分页对象（就是个 dict，`_pager.html` 直接取字段）。"""

    @property
    def show(self) -> bool:
        return self["pages"] > 1


def paginate(query: Query, page: int = 1, per: int = PER_DEFAULT,
             count: Optional[int] = None) -> dict:
    """对 SQLAlchemy 查询分页：返回 `{rows, total, page, per, pages, start, end, ...}`。

    - `page` 从 1 开始；越界自动夹到 `[1, pages]`
    - `count` 可传入**已知总数**避免重复 count（page 越界夹取时需要 total）
    """
    per = max(1, min(int(per or PER_DEFAULT), PER_MAX))
    total = int(count if count is not None else query.order_by(None).count())
    pages = max(1, (total + per - 1) // per)
    p = max(1, min(int(page or 1), pages))
    rows = query.limit(per).offset((p - 1) * per).all()
    return Pager({
        "rows": rows, "total": total, "page": p, "per": per, "pages": pages,
        "has_prev": p > 1, "has_next": p < pages,
        "start": (p - 1) * per + 1 if total else 0,
        "end": min(p * per, total),
    })


def qs(params, drop: str = "page") -> str:
    """把当前查询参数拼成字符串（去掉 `drop`），给分页链接保留筛选条件用。

    `params` 传 `request.query_params`；空值参数丢掉，避免 `?kw=&page=2` 这种噪音。
    """
    from urllib.parse import urlencode
    try:
        items = params.multi_items()
    except AttributeError:
        items = list(dict(params or {}).items())
    kv = [(k, v) for k, v in items if k != drop and v not in ("", None)]
    s = urlencode(kv)
    return (s + "&") if s else ""
