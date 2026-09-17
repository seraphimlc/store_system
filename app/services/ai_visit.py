# -*- coding: utf-8 -*-
"""巡店文件布局 AI 解析（通用能力，不依赖每格式 hard code）。

模型返回：表头所在行 + 各语义列列号 → {"header_row": 1基, "cols": {字段: 1基列号}}。
loader 完全按此坐标解析（不再要求表头含 'Store ID' 字面）；
AI 未配置 / 解析失败 / 不可信 → {}（调用方回退规则解析，仅兼容既有已知格式）。
"""
import json
import re

from app.services.ai_chat import configured as _ai_configured, chat as _chat
from app.services.recon import _sheet_preview

# 巡店文件必需语义字段（可信校验：店/店名/时间/提交人/有效性/投放 六项缺一不可）
_VISIT_REQUIRED = ("store_id", "store_name", "modified_time", "submitter",
                   "visible", "deploy")
# 可选字段（Record ID 等）
_VISIT_OPTIONAL = ("record_id",)


def ai_parse_visit_layout(path: str) -> dict:
    """模型解析巡店文件布局 → {"header_row": 1基行号, "cols": {字段: 1基列号}}。

    未配置 / 解析失败 / 结果不可信 → {}（调用方回退规则）。
    """
    if not _ai_configured():
        return {}
    preview = _sheet_preview(path)
    if not preview:
        return {}
    prompt = (
        "你是表格解析助手。下面是一个巡店 Excel 前几行（行号从0开始写为 R0/R1/…，"
        "列号从0开始，每格形如 [列号]值；可能有标题行、多 sheet）。任务：找出**表头行**"
        "（列名所在行，通常 R0 或 R1）并识别各列语义，只返回 JSON：\n"
        '{"header_row": 表头行号或null, "store_id": 列号或null, '
        '"store_name": 列号或null, "modified_time": 列号或null, '
        '"submitter": 列号或null, "visible": 列号或null, '
        '"deploy": 列号或null, "record_id": 列号或null}\n'
        "字段含义：store_id=店铺标识列（列名常含 Store ID / Store Basic ID / 店舗ID）；"
        "store_name=店铺名称列（常含 Store Name-Local / 店名）；"
        "modified_time=巡店时间列（常含 Modified Time，值为 YYYY-MM-DD HH:MM:SS）；"
        "submitter=巡店人列（常含 Submitter / Agent，值为 '姓名(编号)'）；"
        "visible=有效性标记列：列名可能是 A+ POSM Visible(值 YES/NO)，"
        "或 Review status(值 AUDIT_SUCCESS/AUDIT_FAILED/OTHER/NOT_REQUEST)，"
        "或包含 Visible/Status 的其它名字——**按列的值形态判断**，不要只看列名；"
        "deploy=投放标记列（常含 Deploy New A+POSM / NEW A+ POSM，值为 YES/NO/空）；"
        "record_id=记录编号列（常含 Record ID，可选）。"
        "列号从0开始（第一列是0）。无法确定给 null。除 JSON 外不要输出任何文字。\n\n"
        + preview)
    try:
        text = _chat(prompt)
        data = json.loads(text)
        if not isinstance(data, dict):
            return {}
        m = re.findall(r"\[(\d+)\]", preview)
        ncols = max(int(x) for x in m) + 1 if m else None

        def _col(v):
            if isinstance(v, bool) or not isinstance(v, int):
                return None
            if v < 0 or (ncols is not None and v >= ncols):
                return None
            return v + 1            # 0基 → loader 用 1基

        hr = data.get("header_row")
        if isinstance(hr, bool) or not isinstance(hr, int) or not (0 <= hr <= 3):
            return {}
        cols = {}
        for f in _VISIT_REQUIRED + _VISIT_OPTIONAL:
            c = _col(data.get(f))
            if c is not None:
                cols[f] = c
        # 可信校验：六项必需字段全部定位到
        if any(cols.get(f) is None for f in _VISIT_REQUIRED):
            return {}
        return {"header_row": hr + 1, "cols": cols}
    except Exception:  # noqa: BLE001
        return {}


# 兼容旧调用名（如已引用 ai_parse_visit_cols）
def ai_parse_visit_cols(path: str) -> dict:
    """旧接口：只返回列映射 {字段: 1基列号}（表头行取 AI 识别值）。"""
    r = ai_parse_visit_layout(path)
    return r.get("cols", {}) if r else {}