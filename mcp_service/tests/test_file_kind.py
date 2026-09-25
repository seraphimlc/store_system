# -*- coding: utf-8 -*-
"""上传文件类型自动识别（spec：用户只丢文件，系统自己认）。

判据优先级：sheet 名（巡店模板）> 对账专有列 > 手工对照件 sheet 名 > unknown。
巡店记录与对账文件都有店/人/日期列，故 sheet 名必须优先。
"""
import io

import pytest
from openpyxl import Workbook

from mcp_service import file_kind


def _xlsx(sheets: dict[str, list[list]]) -> bytes:
    """造一个 xlsx：{sheet名: 行列表}。"""
    wb = Workbook()
    first = True
    for name, rows in sheets.items():
        ws = wb.active if first else wb.create_sheet()
        ws.title = name
        first = False
        for r in rows:
            ws.append(r)
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


def test_visit_detected_by_sheet_name():
    """巡店记录：sheet 名（含尾随空格）为 STORE_TASK_EXCEL_SHEET。"""
    content = _xlsx({"STORE_TASK_EXCEL_SHEET ": [
        ["Store Information", "", ""],
        ["Store ID", "Store Name-Local", "Modified Time"],
        ["010104709202609150", "Honegori", "2026-09-16 07:54:44"],
    ]})
    info = file_kind.detect_kind(content)
    assert info["kind"] == "visit"
    assert "STORE_TASK_EXCEL_SHEET" in info["reason"]


def test_recon_detected_by_headers():
    """对账明细：Alipay 风格表头（Statement Date / Agent Name / ISO PID …）。"""
    content = _xlsx({"ISO Billing Export": [
        ["Statement Date", "ISO PID", "ISO Name", "Store Basic ID",
         "Shop Name", "Agent Name", "Action Type"],
        ["2026-08-01", "2108125407013701", "MarsNavi", "0101047",
         "大碗", "梁志铖", "POSM"],
    ]})
    info = file_kind.detect_kind(content)
    assert info["kind"] == "recon"


def test_manual_settlement_detected():
    """手工结算对照件：含「月度总览/人员汇总」等 sheet。"""
    content = _xlsx({"月度总览": [["指标", "数量"], ["原始记录数", 26498]],
                     "人员汇总": [["姓名", "点数"]]})
    info = file_kind.detect_kind(content)
    assert info["kind"] == "manual"


def test_unknown_headers_reported():
    content = _xlsx({"Sheet1": [["foo", "bar"], [1, 2]]})
    info = file_kind.detect_kind(content)
    assert info["kind"] == "unknown"


def test_non_excel_bytes_reported():
    info = file_kind.detect_kind(b"not an xlsx at all")
    assert info["kind"] == "unknown"
    assert "无法打开为 Excel" in info["reason"]


def test_sheet_name_wins_over_overlapping_headers():
    """巡店记录同时含店/人/日期（与对账列重叠）→ 仍判 visit（sheet 名优先）。"""
    content = _xlsx({"STORE_TASK_EXCEL_SHEET": [
        ["Store ID", "Modified Time", "Submitter", "Store Basic ID",
         "Agent Name", "Statement Date"],
        ["0101", "2026-09-01 10:00:00", "甲(2188)", "0101", "甲", "2026-09-01"],
    ]})
    assert file_kind.detect_kind(content)["kind"] == "visit"


def test_infer_month_from_datetime_cells():
    content = _xlsx({"ISO Billing Export": [
        ["Statement Date", "Agent Name", "Store Basic ID"],
        ["2026-08-01", "甲", "0101"],
        ["2026-08-31", "乙", "0102"],
        ["2026-09-01", "丙", "0103"],      # 少数派 → 众数仍为 2026-08
    ]})
    assert file_kind.infer_month(content, "recon") == "2026-08"


def test_infer_month_ignores_visit_files():
    assert file_kind.infer_month(b"whatever", "visit") is None
