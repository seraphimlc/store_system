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


def point_for(japan_date: date, deploy: str, boundary_date: date,
              visible: str = "", point_rules=None) -> int:
    """1点/2点规则。

    - 有 point_rules（布局里的组合规则，AI 从文件"规则"说明提取/人工纠正）：
      按 (visible, deploy) 组合匹配取点数；未匹配 → 0（不计成绩）。
      规则形如 [{"visible": ["OTHER","AUDIT_SUCCESS"], "deploy": ["YES"], "points": 2}, ...]
    - 无 point_rules（默认/历史口径）：boundary 起（含当天）Deploy=YES 记 2 点，否则 1 点。
    """
    if point_rules:
        vis = visible or ""
        dep = deploy or ""
        for r in point_rules:
            if not isinstance(r, dict):
                continue
            vs = r.get("visible")
            ds = r.get("deploy")
            if isinstance(vs, list) and vis not in vs:
                continue
            if isinstance(ds, list) and dep not in ds:
                continue
            p = r.get("points")
            if isinstance(p, int) and not isinstance(p, bool) and 0 <= p <= 9:
                return p
        return 0
    if japan_date < boundary_date:
        return 1
    return 2 if deploy == "YES" else 1


def settle_amount(total_points: int) -> int:
    """每满 68 点 20,000円，余数每点 250円（spec §5.6）。"""
    return (total_points // 68) * 20000 + (total_points % 68) * 250


def visible_is_candidate(v: str, vm=None) -> bool:
    """可见性候选判定：有 value_map（布局）→ 值标记 candidate 才算候选；
    无 → 非空即候选（8 月 YES/NO/…同构口径）。"""
    v = (v or "").strip()
    if vm:
        return vm.get(v) == "candidate"
    return v != ""
