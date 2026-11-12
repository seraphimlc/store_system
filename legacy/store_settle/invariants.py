# -*- coding: utf-8 -*-
"""不变量核对（spec v0.4 §5.1）：
全局 原始总数 = visible_blank + dup_by_id + dup_by_name + final；
人员级 该人原始提交 = 其 visible_blank + dup + final。
人员级不覆盖未识别（code=None）行——调用方需把未识别单独计数。
失败 raise AssertionError（消息含差异明细）。
"""
from typing import Dict, List


def check_invariants(rows_total: int, summary: Dict[str, int],
                     person_breakdown: List[dict]) -> None:
    blank = summary.get("visible_blank", 0)
    dup_id = summary.get("dup_by_id", 0)
    dup_name = summary.get("dup_by_name", 0)
    manual_void = summary.get("manual_void", 0)
    final = summary.get("final", 0)
    if blank + dup_id + dup_name + manual_void + final != rows_total:
        raise AssertionError(
            f"global invariant failed: raw={rows_total} != blank={blank} + "
            f"dup_by_id={dup_id} + dup_by_name={dup_name} + "
            f"manual_void={manual_void} + final={final}")
    for p in person_breakdown:
        if p.get("submitter_code") is None:
            continue  # 未识别行：全局已计，人员级不覆盖
        raw = p["raw_submitted"]
        part = (p["visible_blank"] + p["dup_count"] + p.get("manual_void", 0)
                + p["final_valid"])
        if raw != part:
            raise AssertionError(
                f"person invariant failed: code={p.get('submitter_code')!r} "
                f"raw={raw} != blank={p['visible_blank']}+dup={p['dup_count']}"
                f"+manual_void={p.get('manual_void', 0)}+final={p['final_valid']}")
