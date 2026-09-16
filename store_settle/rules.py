# -*- coding: utf-8 -*-
"""纯规则：提交人/时间解析、1点2点、68 点精算（spec v0.4 §5.3/§5.6、§4.3 注）。"""
import re
from datetime import datetime, date
from typing import Optional, Tuple

_MT = re.compile(r"^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})$")
_SUB = re.compile(r"^(.+)\((\d+)\)$")

YES_NO_BLANK = {"YES", "NO", ""}


def parse_submitter(raw) -> Optional[Tuple[str, str]]:
    """'姓名(编号)' -> (姓名, 编号)；无法解析返回 None（=未识别，spec §9）。"""
    if raw is None:
        return None
    s = str(raw).strip()
    m = _SUB.match(s)
    if not m:
        return None
    return m.group(1).strip(), m.group(2)


def parse_modified_jst(text) -> Optional[datetime]:
    """仅认 19 位定宽 'YYYY-MM-DD HH:MM:SS'（日本时间 naive），否则 None。"""
    if text is None:
        return None
    m = _MT.match(str(text).strip())
    if not m:
        return None
    y, mo, d, h, mi, s = map(int, m.groups())
    return datetime(y, mo, d, h, mi, s)


def point_for(japan_date: date, deploy: str, boundary_date: date) -> int:
    """1点/2点规则（spec §5.3）：boundary 起（含当天）Deploy=YES 记 2 点。"""
    if japan_date < boundary_date:
        return 1
    return 2 if deploy == "YES" else 1


def settle_amount(total_points: int) -> int:
    """每满 68 点 20,000円，余数每点 250円（spec §5.6）。"""
    return (total_points // 68) * 20000 + (total_points % 68) * 250
