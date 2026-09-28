# -*- coding: utf-8 -*-
"""员工填报 / 对比结果的 Excel 导出（openpyxl，规格 §9）。

两个函数都返回 (xlsx 字节, 建议文件名)，由路由包进 StreamingResponse。
**用 `Workbook(write_only=True)`**：逐行 append，不在内存里保留整份工作簿
（评审：原先普通模式会把所有行同时留在内存，多月份导出时占用翻倍）。
注意：write_only 模式下不能随机访问单元格，只能在 sheet 创建时设列宽。
"""
import io

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font

from app.models import Person


def _write_sheet(wb, title: str, header: list, rows) -> None:
    """流式写一张表：首行加粗 + 固定列宽。`rows` 可以是生成器（逐行取，不预载）。"""
    ws = wb.create_sheet(title[:31])
    head = []
    for i, h in enumerate(header, 1):
        c = WriteOnlyCell(ws, value=h)
        c.font = Font(bold=True)
        head.append(c)
        ws.column_dimensions[c.column_letter].width = 16
    ws.append(head)
    for r in rows:
        ws.append(r)


def _save(wb) -> bytes:
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


def _names(db) -> dict:
    return dict(db.query(Person.code, Person.display_name).all())


def reports_xlsx(db, start, end, person_code: str = ""):
    """自报明细导出。"""
    from app.services import report_compare
    names = _names(db)
    data = report_compare.list_reports(db, start=start, end=end,
                                       person_code=person_code, page=1,
                                       per=20000)
    wb = Workbook(write_only=True)
    _write_sheet(wb, "自报明细",
                 ["日期", "员工编号", "姓名", "担当区域", "1点店铺数", "2点店铺数",
                  "合计", "提交时间(UTC)", "来源"],
                 ([str(r["date"]), r["person_code"],
                   names.get(r["person_code"], ""), r["area"], r["p1"], r["p2"],
                   r["total"], str(r["submitted_at"] or ""), r["source"]]
                  for r in data["rows"]))
    return _save(wb), "staff_reports_%s_%s.xlsx" % (start, end)


def compare_xlsx(db, start, end, person_code: str = ""):
    """对比结果导出（逐人：系统 vs 自报 + 偏差 + 准确率）。"""
    from app.services import report_compare
    res = report_compare.compare(db, start, end, person_code)
    wb = Workbook(write_only=True)
    _write_sheet(wb, "对比(按人)",
                 ["员工编号", "姓名", "系统1点", "系统2点", "系统合计",
                  "自报1点", "自报2点", "自报合计", "Δ1点", "Δ2点", "Δ合计",
                  "准确率%", "已报天数", "系统天数", "应填未填"],
                 ([p["person_code"], p["name"], p["sys_p1"], p["sys_p2"],
                   p["sys_total"], p["rep_p1"], p["rep_p2"], p["rep_total"],
                   p["d1"], p["d2"], p["d_total"],
                   "—" if p["acc"] is None else round(p["acc"] * 100, 1),
                   p["days_filled"], p["days_system"], p["gaps"]]
                  for p in res["persons"]))
    kind_label = {"both": "两侧都有", "missing_report": "系统有/未报",
                  "missing_system": "自报有/系统无"}
    _write_sheet(wb, "对比(逐日)",
                 ["日期", "员工编号", "姓名", "类型", "系统1点", "系统2点",
                  "系统合计", "自报1点", "自报2点", "自报合计", "Δ合计", "单日准确率%"],
                 ([str(r["date"]), r["person_code"], r["name"],
                   kind_label.get(r["kind"], r["kind"]),
                   r["sys_p1"], r["sys_p2"], r["sys_total"],
                   r["rep_p1"], r["rep_p2"], r["rep_total"], r["dt"],
                   "—" if r.get("acc") is None else round(r["acc"] * 100, 1)]
                  for r in res["daily"]))
    s = res["summary"]
    _write_sheet(wb, "汇总", ["项", "值"],
                 [["区间", "%s ~ %s" % (start, end)],
                  ["自报条数", s["checkin_cnt"]],
                  ["系统条数", s["formal_cnt"]],
                  ["两侧都有(可对照)", s["matched_cnt"]],
                  ["其中完全一致", s["consistent_cnt"]],
                  ["一致率", "—" if s["consistent_rate"] is None
                   else round(s["consistent_rate"] * 100, 1)],
                  ["系统有/员工未报", res["counts"]["missing_report"]],
                  ["自报有/系统无", res["counts"]["missing_system"]]])
    return _save(wb), "staff_compare_%s_%s.xlsx" % (start, end)
