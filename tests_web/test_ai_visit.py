# -*- coding: utf-8 -*-
"""AI 布局解析（巡店表头通用能力）与 loader 坐标模式测试。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from openpyxl import Workbook


def _make(path, rows):
    wb = Workbook()
    ws = wb.active
    ws.title = "任意sheet名"
    for r in rows:
        ws.append(r)
    wb.save(path)


@pytest.fixture()
def variant_file(tmp_path):
    """变体表头：标题行 + 日文列名乱序 + 非模板 sheet。"""
    p = str(tmp_path / "variant.xlsx")
    _make(p, [
        ["2026年9月 巡店数据", "", "", "", "", "", ""],
        ["POP展開", "店名", "店舗ID", "担当者", "巡店日時", "審査ステータス", "メモ"],
        ["YES", "A店", "01010470920260915001", "山田(111)",
         "2026-09-03 09:15:00", "AUDIT_SUCCESS", ""],
        ["", "B店", "01010470920260915002", "李四(222)",
         "2026-09-05 10:00:00", "OTHER", ""],
        ["YES", "C店", "01010470920260915003", "王五(333)",
         "2026-09-08 11:30:00", "AUDIT_FAILED", ""],
    ])
    return p


def test_loader_ai_layout_mode(variant_file):
    """loader AI 坐标模式：不要求表头含 'Store ID'，按 AI 行列号 + 值语义解析。"""
    from store_settle.loader import load_workbook
    layout = {"header_row": 2,
              "cols": {"store_id": 3, "store_name": 2, "modified_time": 5,
                       "submitter": 4, "visible": 6, "deploy": 1},
              "value_map": {"visible": {"AUDIT_SUCCESS": "candidate",
                                        "OTHER": "candidate",
                                        "AUDIT_FAILED": "blank"},
                            "deploy": {"YES": 2, "": 1}}}
    res = load_workbook(variant_file, col_override=layout)
    assert not res.failed and res.parsed_rows == 3
    r0 = res.rows[0]
    assert r0.store_id_raw == "01010470920260915001"
    assert r0.store_name_local_raw == "A店"
    assert r0.modified_raw == "2026-09-03 09:15:00"
    assert r0.submitter_code == "111"
    assert r0.visible_raw == "YES"      # AUDIT_SUCCESS → candidate
    assert r0.deploy_raw == "YES"
    assert res.rows[1].visible_raw == "YES"    # OTHER → candidate
    assert res.rows[2].visible_raw == ""       # AUDIT_FAILED → blank（不计）


def test_loader_value_map_controls_visibility(variant_file):
    """值语义完全由 value_map 决定（不写死）：改成 candidate 则 FAILED 也算有效。"""
    from store_settle.loader import load_workbook
    layout = {"header_row": 2,
              "cols": {"store_id": 3, "store_name": 2, "modified_time": 5,
                       "submitter": 4, "visible": 6, "deploy": 1},
              "value_map": {"visible": {"AUDIT_SUCCESS": "candidate",
                                        "OTHER": "candidate",
                                        "AUDIT_FAILED": "candidate"},
                            "deploy": {"YES": 2, "": 1}}}
    res = load_workbook(variant_file, col_override=layout)
    assert res.rows[2].visible_raw == "YES"   # 口径由配置控制（页面可纠正）


def test_ai_parse_visit_layout_monkeypatched(variant_file, monkeypatch):
    """ai_parse_visit_layout：模型返回布局 → 结构校验/可信校验；失败回退 {}。"""
    import app.services.ai_visit as av
    from app.services.ai_visit import ai_parse_visit_layout

    monkeypatch.setattr(av, "_ai_configured", lambda: True)
    monkeypatch.setattr(av, "_chat", lambda prompt: (
        '{"header_row": 1, "store_id": 2, "store_name": 1, '
        '"modified_time": 4, "submitter": 3, "visible": 5, "deploy": 0, '
        '"record_id": null}'))
    out = ai_parse_visit_layout(variant_file)
    assert out["header_row"] == 2
    assert out["cols"] == {"store_id": 3, "store_name": 2, "modified_time": 5,
                           "submitter": 4, "visible": 6, "deploy": 1}
    assert out["source"] == "ai"

    # 模型漏报必需字段 → 不可信 → {}
    monkeypatch.setattr(av, "_chat", lambda prompt: (
        '{"header_row": 1, "store_id": 2, "store_name": 1, '
        '"modified_time": null, "submitter": null, "visible": 5, '
        '"deploy": 0}'))
    assert ai_parse_visit_layout(variant_file) == {}

    # 模型返回乱码 → {}
    monkeypatch.setattr(av, "_chat", lambda prompt: "not json")
    assert ai_parse_visit_layout(variant_file) == {}

    # 未配置 AI → {}
    monkeypatch.setattr(av, "_ai_configured", lambda: False)
    assert ai_parse_visit_layout(variant_file) == {}


def test_rule_fallback_still_works(tmp_path):
    """规则兜底（无 AI）：既有 8 月表头 + Review status 变体仍可解析。"""
    from store_settle.loader import load_workbook
    p = str(tmp_path / "review.xlsx")
    _make(p, [
        ["Store ID", "Store Name-Local", "Modified Time", "Submitter",
         "Review status", "Deploy New A+POSM"],
        ["01010470920260915001", "店A", "2026-09-03 09:15:00", "甲(111)",
         "AUDIT_SUCCESS", "YES"],
    ])
    res = load_workbook(p)
    assert not res.failed and res.parsed_rows == 1
    assert res.rows[0].visible_raw == "YES"
