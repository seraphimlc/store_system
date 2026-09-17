# -*- coding: utf-8 -*-
"""Excel loader（spec v0.4 §5.2/§5.2.1/§4.3 注）。

- 兼容：宽50 双表头 / 简9 单表头（列序可变，一律按表头名识别）/ 闫总简表
  / sheet 名尾随空格 / 坏 <dimension> 元数据（普通模式打开，不受 dimension 误导）。
- 只解析模板 sheet（名称去空白后 == STORE_TASK_EXCEL_SHEET）；其余 sheet 记 ignored_sheets。
- 必需列缺失 / Modified 非 19 位定宽 / visible·deploy 值域异常（不做大小写折叠）→ 整文件 failed。
- Submitter 无括号编号 → 警告 + 入库 submitter_code=None（未识别）。
"""
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from openpyxl import load_workbook as _xlsx_load

from store_settle.models import ParsedRow
from store_settle.rules import YES_NO_BLANK, parse_modified_jst, parse_submitter

_TEMPLATE = "STORE_TASK_EXCEL_SHEET"
_DEPLOY_ALIASES = ("Deploy New A+POSM", "NEW A+ POSM")
# Visible 语义列：8 月用 "A+ POSM Visible"(YES/NO/空)；9 月起部分文件用 "Review status"
# （AUDIT_SUCCESS→YES、AUDIT_FAILED→NO，均参与判重，与 8 月 YES/NO 同构）
_VISIBLE_ALIASES = ("A+ POSM Visible", "Review status")
_REQUIRED = ("Store ID", "Store Name-Local", "Modified Time", "Submitter")
_OPTIONAL_MAP = {
    "Store Name-English": "store_name_en_raw",
    "Record ID": "record_id_raw",
}


def _norm_visible(v: str):
    """Visible 值规范化：YES/NO/空原样；AUDIT_SUCCESS→YES、AUDIT_FAILED→NO；
    其它非空（OTHER/NOT_REQUEST 等审核状态）→ YES（视为巡店有效候选，与 8 月
    「非空白即候选」同构；如需对 OTHER 单独口径再调整）。"""
    if v in ("YES", "NO", ""):
        return v
    if v == "AUDIT_SUCCESS":
        return "YES"
    if v == "AUDIT_FAILED":
        return "NO"
    return "YES"


@dataclass
class LoadResult:
    format: str = "other"
    parsed_sheets: List[str] = field(default_factory=list)
    ignored_sheets: List[dict] = field(default_factory=list)
    header_row: int = 0
    data_start_row: int = 0
    total_rows: int = 0
    parsed_rows: int = 0
    failed: bool = False
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    rows: List[ParsedRow] = field(default_factory=list)


def _count_nonempty(ws, cap=200000):
    """近似非空行计数：数据区连续，连续 5 空行即停。"""
    n = 0
    empty_run = 0
    for row in ws.iter_rows(values_only=True):
        if any(v is not None and str(v).strip() != "" for v in row):
            n += 1
            empty_run = 0
        else:
            empty_run += 1
            if empty_run >= 5:
                break
        if n >= cap:
            break
    return n


def _find_header(ws) -> Optional[tuple]:
    """前 3 行内定位含 'Store ID' 的表头行 -> (1based 表头行号, {去空白列名: 列号})。

    同名列（wide50/flat9 导出文件常含右侧镜像重复表头，如第二组
    Store Name-Local/English）一律取**最左侧首现**——镜像区多无主数据，
    后列覆盖会让名称/行业等字段错位到空白列。
    """
    for r in range(1, 4):
        mapping: Dict[str, int] = {}
        found = False
        for c in range(1, ws.max_column + 1):
            v = ws.cell(r, c).value
            if v is None:
                continue
            s = str(v).strip()
            if s and s not in mapping:
                mapping[s] = c
            if s == "Store ID":
                found = True
        if found:
            return r, mapping
    return None


def load_workbook(path: str, filename: Optional[str] = None, import_id: int = 0,
                  col_override: Optional[dict] = None) -> LoadResult:
    """解析 Excel。col_override：AI 表头识别给出的列映射
    {semantic: 1基列号}（如 {"store_id":1,"visible":6,"deploy":7}），
    提供时优先于按表头名匹配；缺项回退规则匹配。"""
    name = filename or os.path.basename(path)
    res = LoadResult()
    wb = _xlsx_load(path, read_only=False, data_only=True)
    try:
        templates = [ws for ws in wb.worksheets
                     if ws.title.strip().upper() == _TEMPLATE]
        if len(templates) > 1:
            res.warnings.append(f"发现 {len(templates)} 个模板 sheet，将全部并入候选池")
        if templates:
            for ws in wb.worksheets:
                if ws.title.strip().upper() == _TEMPLATE:
                    if _parse_sheet(ws, name, import_id, res, col_override):
                        return res
                else:
                    res.ignored_sheets.append({"name": ws.title,
                                               "rows": _count_nonempty(ws),
                                               "reason": "非模板 sheet，忽略"})
        else:
            # 无模板 sheet：回退到"首个含全部必需列的 sheet"，其余全部忽略
            def has_required(found):
                if found is None:
                    return False
                _, colmap = found
                return all(req in colmap for req in _REQUIRED) and any(
                    a in colmap for a in _DEPLOY_ALIASES)
            parsed_any = False
            for ws in wb.worksheets:
                found = _find_header(ws)
                if not parsed_any and has_required(found):
                    parsed_any = True
                    if _parse_sheet(ws, name, import_id, res, col_override):
                        return res
                else:
                    res.ignored_sheets.append({"name": ws.title,
                                               "rows": _count_nonempty(ws),
                                               "reason": "非模板/缺必需列，忽略"})
            if not parsed_any and not res.errors:
                res.failed = True
                res.errors.append("未找到模板 sheet STORE_TASK_EXCEL_SHEET，"
                                  "且无含必需列的可用 sheet")
            elif parsed_any:
                res.warnings.append("未找到模板 sheet，已回退使用含必需列的 sheet")
        return res
    finally:
        wb.close()


def _parse_sheet(ws, filename: str, import_id: int, res: LoadResult,
                   col_override: Optional[dict] = None) -> bool:
    """解析一个模板 sheet；出错返回 True（文件失败，终止后续）。"""
    found = _find_header(ws)
    if found is None:
        res.errors.append(f"{filename}（sheet {ws.title!r}）: 前 3 行未找到 'Store ID' 表头")
        res.failed = True
        return True
    header_row, colmap = found
    res.parsed_sheets.append(ws.title)
    res.header_row = header_row
    res.data_start_row = header_row + 1

    deploy_col = (col_override or {}).get("deploy") or \
        next((colmap[a] for a in _DEPLOY_ALIASES if a in colmap), None)
    visible_col = (col_override or {}).get("visible") or \
        next((colmap[a] for a in _VISIBLE_ALIASES if a in colmap), None)
    # AI 列映射优先；缺项回退表头名匹配
    _SEM = {"store_id": "Store ID", "store_name": "Store Name-Local",
            "modified_time": "Modified Time", "submitter": "Submitter"}
    col_ids = {}
    for sem, req in _SEM.items():
        col_ids[req] = (col_override or {}).get(sem) or colmap.get(req)
    missing = [req for req in _REQUIRED if col_ids.get(req) is None]
    if deploy_col is None:
        missing.append(_DEPLOY_ALIASES[0])
    if visible_col is None:
        missing.append("Visible 列（" + " / ".join(_VISIBLE_ALIASES) + "）")
    if missing:
        res.errors.append(f"{filename}（sheet {ws.title!r}）缺必需列: {', '.join(missing)}")
        res.failed = True
        return True

    opt_cols = {k: colmap.get(k) for k in _OPTIONAL_MAP}
    ncols = ws.max_column

    def cell_raw(row, c):
        """原文（不 trim）：去重/比对前保留原始文本（spec §4.3 raw 层不可变）。"""
        if c is None:
            return ""
        v = row[c - 1] if row is not None else None
        return "" if v is None else str(v)

    def cell(row, c):
        return cell_raw(row, c).strip()

    data_rows = 0
    for r_i, row in enumerate(ws.iter_rows(min_row=header_row + 1,
                                           values_only=True),
                              start=header_row + 1):
        if row is None or all(v is None or str(v).strip() == "" for v in row):
            continue
        data_rows += 1
        sid_raw = cell_raw(row, col_ids["Store ID"])          # 保留原文
        name_raw = cell_raw(row, col_ids["Store Name-Local"])  # 保留原文（raw 判重模式依赖）
        mt_raw = cell_raw(row, col_ids["Modified Time"])
        vis_raw = _norm_visible(cell(row, visible_col))      # 规范化：trim + AUDIT 映射
        dep_raw = cell(row, deploy_col)                        # 规范化：trim
        sub_raw = cell_raw(row, col_ids["Submitter"])          # 保留原文（parse_submitter 自理）

        if vis_raw is None:
            res.errors.append(f"{filename} 行{r_i}: Visible 值域异常 "
                              f"{cell(row, visible_col)!r}"
                              "（仅接受 YES/NO/空白，或 AUDIT_SUCCESS/AUDIT_FAILED）")
            res.failed = True
            return True
        if dep_raw not in YES_NO_BLANK:
            res.errors.append(f"{filename} 行{r_i}: Deploy 值域异常 {dep_raw!r}"
                              "（仅接受精确 YES/NO/空白）")
            res.failed = True
            return True
        if parse_modified_jst(mt_raw) is None:
            res.errors.append(f"{filename} 行{r_i}: Modified Time 非 19 位定宽 {mt_raw!r}")
            res.failed = True
            return True

        parsed = parse_submitter(sub_raw)
        submitter_code = parsed[1] if parsed else None
        if submitter_code is None and sub_raw != "":
            res.warnings.append(f"{filename} 行{r_i}: Submitter 无法解析编号"
                                f"（{sub_raw!r}），按未识别处理")

        pr = ParsedRow(
            import_id=import_id, sheet_name=ws.title, excel_row=r_i,
            store_id_raw=sid_raw, store_name_local_raw=name_raw,
            store_name_en_raw=cell_raw(row, opt_cols["Store Name-English"]),
            modified_raw=mt_raw,
            submitter_raw=sub_raw,
            submitter_code=submitter_code,
            record_id_raw=cell_raw(row, opt_cols["Record ID"]),
            visible_raw=vis_raw, deploy_raw=dep_raw,
            original_row=list(row)[:ncols],
        )
        res.rows.append(pr)

    res.format = "wide50" if header_row == 2 else "flat9"
    res.total_rows += data_rows
    res.parsed_rows = len(res.rows)
    return False
