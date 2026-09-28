# -*- coding: utf-8 -*-
"""员工填报 / 对比结果的 Excel 导出（openpyxl，规格 §9）。

两个函数都返回 (xlsx 字节, 建议文件名)，由路由包进 StreamingResponse。
"""
from app.models import Person


def _names(db) -> dict:
    return dict(db.query(Person.code, Person.display_name).all())


def _sheet(wb, title: str, header: list, rows: list):
    ws = wb.active
    ws.title = title[:31]
    ws.append(header)
    for r in rows:
        ws.append(r)
    for col in range(1, len(header) + 1):      # 列宽按表头粗估
        ws.column_dimensions[ws.cell(row=1, column=col).column_letter].width = 16
    return ws


def reports_xlsx(db, start, end, person_code: str = ""):
    """自报明细导出。"""
    from openpyxl import Workbook

    from app.services import daily_report
    names = _names(db)
    data = daily_report.list_reports(db, start=start, end=end,
                                     person_code=person_code, page=1, per=100000)
    wb = Workbook()
    _sheet(wb, "自报明细",
           ["日期", "员工编号", "姓名", "担当区域", "1点店铺数", "2点店铺数",
            "合计", "提交时间(UTC)", "来源"],
           [[str(r["date"]), r["person_code"], names.get(r["person_code"], ""),
             r["area"], r["p1"], r["p2"], r["total"],
             str(r["submitted_at"] or ""), "web"] for r in data["rows"]])
    import io as _io
    bio = _io.BytesIO()
    wb.save(bio)
    return bio.getvalue(), "staff_reports_%s_%s.xlsx" % (start, end)


def compare_xlsx(db, start, end, person_code: str = ""):
    """对比结果导出（逐人：系统 vs 自报 + 偏差 + 准确率）。"""
    from openpyxl import Workbook

    from app.services import daily_report
    res = daily_report.compare(db, start, end, person_code)
    wb = Workbook()
    per_rows = [[p["person_code"], p["name"], p["sys_p1"], p["sys_p2"], p["sys_total"],
                 p["rep_p1"], p["rep_p2"], p["rep_total"],
                 p["d1"], p["d2"], p["d_total"],
                 "—" if p["acc"] is None else round(p["acc"] * 100, 1),
                 p["days_filled"], p["days_system"], p["gaps"]]
                for p in res["persons"]]
    _sheet(wb, "对比(按人)",
           ["员工编号", "姓名", "系统1点", "系统2点", "系统合计",
            "自报1点", "自报2点", "自报合计", "Δ1点", "Δ2点", "Δ合计",
            "准确率%", "已报天数", "系统天数", "应填未填"],
           per_rows)
    kind_label = {"both": "两侧都有", "missing_report": "系统有/未报",
                  "missing_system": "自报有/系统无"}
    ws2 = wb.create_sheet("对比(逐日)")
    ws2.append(["日期", "员工编号", "姓名", "类型", "系统1点", "系统2点", "系统合计",
                "自报1点", "自报2点", "自报合计", "Δ合计"])
    for r in res["daily"]:
        ws2.append([str(r["date"]), r["person_code"], r["name"],
                    kind_label.get(r["kind"], r["kind"]),
                    r["sys_p1"], r["sys_p2"], r["sys_total"],
                    r["rep_p1"], r["rep_p2"], r["rep_total"], r["dt"]])
    s = res["summary"]
    ws3 = wb.create_sheet("汇总")
    ws3.append(["区间", "%s ~ %s" % (start, end)])
    ws3.append(["自报条数", s["checkin_cnt"]])
    ws3.append(["系统条数", s["formal_cnt"]])
    ws3.append(["两侧都有(可对照)", s["matched_cnt"]])
    ws3.append(["其中完全一致", s["consistent_cnt"]])
    ws3.append(["一致率", "—" if s["consistent_rate"] is None
                else round(s["consistent_rate"] * 100, 1)])
    ws3.append(["系统有/员工未报", res["counts"]["missing_report"]])
    ws3.append(["自报有/系统无", res["counts"]["missing_system"]])
    import io as _io
    bio = _io.BytesIO()
    wb.save(bio)
    return bio.getvalue(), "staff_compare_%s_%s.xlsx" % (start, end)
