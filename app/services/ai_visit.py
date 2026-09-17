# -*- coding: utf-8 -*-
"""巡店文件表头 AI 解析（方案：系统内调用模型直接识别列语义，规则解析兜底）。

用法：ai_parse_visit_cols(path) -> {字段: 1基列号}；未配置/失败/不可信 → {}
调用方（importer）成功时把结果作为 loader 的 col_override 传入。
"""
import json
import re

from app.services.ai_chat import configured as _ai_configured, chat as _chat
from app.services.recon import _sheet_preview

# 巡店文件必需语义字段
_VISIT_FIELDS = ("store_id", "store_name", "modified_time", "submitter",
                 "visible", "deploy", "record_id")


def ai_parse_visit_cols(path: str) -> dict:
    """模型解析巡店表头 → {字段: 1基列号}；不可用 → {}。"""
    if not _ai_configured():
        return {}
    preview = _sheet_preview(path)
    if not preview:
        return {}
    prompt = (
        "你是表格解析助手。下面是一个巡店 Excel 前几行（R0 通常是表头，列号从0开始，"
        "每格形如 [列号]值；可能有标题行）。请识别这些列的语义，只返回 JSON：\n"
        '{"store_id": 列号或null, "store_name": 列号或null, '
        '"modified_time": 列号或null, "submitter": 列号或null, '
        '"visible": 列号或null, "deploy": 列号或null, "record_id": 列号或null}\n'
        "字段含义：store_id=店铺标识（如 Store ID/Store Basic ID）；"
        "store_name=店铺名称（Store Name-Local）；modified_time=巡店时间（Modified Time，"
        "形如 YYYY-MM-DD HH:MM:SS）；submitter=巡店人（如 '姓名(编号)' 或 Agent Name）；"
        "visible=巡店有效性标记（列名可能是 A+ POSM Visible(YES/NO) 或 Review status"
        "(AUDIT_SUCCESS/AUDIT_FAILED/OTHER)）；deploy=投放标记（Deploy New A+POSM/NEW A+ POSM，"
        "值为 YES/NO/空）；record_id=记录编号（Record ID，可选）。无法确定的给 null。"
        "除 JSON 外不要输出任何文字。\n\n"
        + preview)
    try:
        text = _chat(prompt)
        data = json.loads(text)
        if not isinstance(data, dict):
            return {}
        m = re.findall(r"\[(\d+)\]", preview)
        ncols = max(int(x) for x in m) + 1 if m else None
        out = {}
        for f in _VISIT_FIELDS:
            v = data.get(f)
            if isinstance(v, bool) or not isinstance(v, int):
                continue
            if v < 0 or (ncols is not None and v >= ncols):
                continue
            out[f] = v + 1          # 0基列号 → loader 用 1基
        # 可信校验：店 + 店名 + 时间 + 提交人 必须定位到
        if not (out.get("store_id") and out.get("store_name")
                and out.get("modified_time") and out.get("submitter")):
            return {}
        return out
    except Exception:  # noqa: BLE001
        return {}
