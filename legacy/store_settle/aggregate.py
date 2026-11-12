# -*- coding: utf-8 -*-
"""聚合（spec v0.4 §4.7）：person_stats / daily_system_points。

person_stats: 每 (run, submitter_code) 一行：raw/blank/dup/final/1点/2点/总点/精算额。
             code=None 归"未识别"单列一行，计入全局、不并入任何人。
daily_points: 每 (run, japan_date, submitter_code) 一行（仅出现 final 的日期），
             供"每日成绩"与阶段二对账使用。
"""
from datetime import date
from typing import Dict, List

from store_settle.models import Bucket
from store_settle.pipeline import RunResult
from store_settle.rules import point_for, settle_amount

_UNRECOGNIZED_NAME = "未识别"


def _earliest_names(run: RunResult) -> Dict[object, str]:
    """每编号取最早记录（按 import_id, excel_row；含 dup/空白记录）的姓名原文
    （spec §4.4：display_name 取最早 raw 记录，非最早 final）。一趟扫描完成。"""
    best: Dict[object, tuple] = {}
    for rec in run.records:
        code = rec.row.submitter_code
        key = (rec.row.import_id, rec.row.excel_row)
        cur = best.get(code)
        if cur is None or key < cur[0]:
            best[code] = (key, rec.row.submitter_raw)
    names: Dict[object, str] = {}
    for code, (_, raw) in best.items():
        nm = raw.split("(")[0].strip() if raw else ""
        names[code] = nm or _UNRECOGNIZED_NAME
    return names


def person_stats(run: RunResult,
                 boundary_date: date = date(2026, 7, 9)) -> List[dict]:
    names = _earliest_names(run)
    rows: Dict[object, dict] = {}
    for rec in run.records:
        code = rec.row.submitter_code
        d = rows.get(code)
        if d is None:
            d = {
                "submitter_code": code,
                "submitter_name": names.get(code, _UNRECOGNIZED_NAME),
                "raw_submitted": 0, "visible_blank": 0, "dup_count": 0,
                "manual_void": 0, "final_valid": 0, "points_1": 0,
                "points_2": 0, "total_points": 0, "settle_amount": 0,
            }
            rows[code] = d
        d["raw_submitted"] += 1
        b = rec.bucket
        if b == Bucket.VISIBLE_BLANK:
            d["visible_blank"] += 1
        elif b in (Bucket.DUP_BY_ID, Bucket.DUP_BY_NAME):
            d["dup_count"] += 1
        elif b == Bucket.MANUAL_VOID:
            d["manual_void"] += 1
        elif b == Bucket.FINAL:
            d["final_valid"] += 1
            p = point_for(rec.japan_date, rec.row.deploy_raw, boundary_date)
            d[f"points_{p}"] += 1
            d["total_points"] += p
    out = list(rows.values())
    for d in out:
        d["settle_amount"] = settle_amount(d["total_points"])
        if d["submitter_code"] is None:
            d["submitter_name"] = _UNRECOGNIZED_NAME
    return sorted(out, key=lambda d: (d["submitter_code"] is None,
                                      str(d["submitter_code"])))


def daily_points(run: RunResult,
                 boundary_date: date = date(2026, 7, 9)) -> List[dict]:
    agg: Dict[tuple, dict] = {}
    for rec in run.records:
        if rec.bucket != Bucket.FINAL:
            continue
        key = (rec.japan_date, rec.row.submitter_code)
        d = agg.setdefault(key, {"japan_date": rec.japan_date,
                                 "submitter_code": rec.row.submitter_code,
                                 "final_stores": 0, "points_1": 0,
                                 "points_2": 0, "total_points": 0})
        d["final_stores"] += 1
        p = point_for(rec.japan_date, rec.row.deploy_raw, boundary_date)
        d[f"points_{p}"] += 1
        d["total_points"] += p
    return sorted(agg.values(), key=lambda d: (str(d["japan_date"]),
                                               str(d["submitter_code"])))
