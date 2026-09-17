# -*- coding: utf-8 -*-
"""巡店文件布局 AI 解析（通用能力，两步式小提示词，避免推理模型长思考挂起）。

第一步：识别表头行 + 各语义列坐标（小 JSON，max_tokens 8000 足够）；
第二步：识别取值语义 + 点数组合规则（只在预览含规则页时问一次）。
返回 {"header_row": 1基, "cols": {...}, "value_map": {...}, "point_rules": [...],
      "source": "ai"}；未配置/失败/不可信 → {}（调用方回退规则解析）。
"""
import re
from typing import Optional

from app.services.ai_chat import (configured as _ai_configured, chat as _chat,
                                  extract_json as _extract_json)
from app.services.recon import _sheet_preview

_VISIT_REQUIRED = ("store_id", "store_name", "modified_time", "submitter",
                   "visible", "deploy")
_VISIT_OPTIONAL = ("record_id",)


def _cols_prompt(preview: str) -> str:
    return (
        "识别巡店表格的列位置，只返回 JSON：\n"
        '{"header_row": 表头行号(0基), "store_id": 列号, "store_name": 列号, '
        '"modified_time": 列号, "submitter": 列号, "visible": 列号, '
        '"deploy": 列号, "record_id": 列号或null}\n'
        "列号从0开始（第一列是0）。visible=巡店有效性列（值可能是 YES/NO 或 "
        "AUDIT_SUCCESS/AUDIT_FAILED/OTHER 等状态）；deploy=投放列（值 YES/NO/空，"
        "列名常含 Deploy/POSM）；modified_time=时间列（YYYY-MM-DD HH:MM:SS）；"
        "submitter=提交人列（姓名(编号)）。无法确定给 null。只输出 JSON，不要解释。\n\n"
        + preview)


def _rules_prompt(preview: str) -> str:
    """仅当预览含『规则/说明』页文本（取含点/等号/竖线的行）时构造。"""
    lines = [ln for ln in preview.splitlines()
             if ("规则" in ln or "说明" in ln or ln.startswith("# 规则"))
             and ("=" in ln or "点" in ln or "|" in ln)]
    if not lines:
        return ""
    return (
        "下面是巡店文件『规则』说明页的文字与数据样例。提取点数规则，只返回 JSON：\n"
        '{"point_rules": [{"visible": [取值...], "deploy": [取值...], '
        '"points": 点数}, ...]}\n'
        "要求：覆盖所有取值组合；未投放/空白的点数也要列（空值写 \"\"）；"
        "按组合不计成绩的写 0。只输出 JSON，不要解释。\n\n"
        + "\n".join(lines[:40]))


def _norm_rules(pr):
    """校验/规范化 point_rules。"""
    out = []
    if not isinstance(pr, list):
        return out
    for it in pr:
        if not isinstance(it, dict):
            continue
        pt = it.get("points")
        if isinstance(pt, bool) or not isinstance(pt, int):
            continue
        if not (0 <= pt <= 9):
            continue
        rule = {"points": pt}
        for k in ("visible", "deploy"):
            v = it.get(k)
            if isinstance(v, list):
                rule[k] = [str(x).strip() for x in v if isinstance(x, str)]
            elif v is None:
                rule[k] = None
        out.append(rule)
    return out


def ai_parse_visit_layout(path: str) -> dict:
    """模型解析巡店文件布局（两步式）；未配置/失败/不可信 → {}。"""
    if not _ai_configured():
        return {}
    preview = _sheet_preview(path)
    if not preview:
        return {}
    try:
        # ① 列识别（小 token → 思考短、稳定）
        text = _chat(_cols_prompt(preview), max_tokens=8000, timeout=300)
        data = _extract_json(text)
        if not isinstance(data, dict):
            return {}
        m = re.findall(r"\[(\d+)\]", preview)
        ncols = max(int(x) for x in m) + 1 if m else None

        def _col(v):
            if isinstance(v, bool) or not isinstance(v, int):
                return None
            if v < 0 or (ncols is not None and v >= ncols):
                return None
            return v + 1            # 0基 → 1基

        hr = data.get("header_row")
        if isinstance(hr, bool) or not isinstance(hr, int) or not (0 <= hr <= 3):
            return {}
        cols = {}
        for f in _VISIT_REQUIRED + _VISIT_OPTIONAL:
            c = _col(data.get(f))
            if c is not None:
                cols[f] = c
        if any(cols.get(f) is None for f in _VISIT_REQUIRED):
            return {}
        out = {"header_row": hr + 1, "cols": cols, "source": "ai"}

        # ② 取值语义 + 点数规则（有规则页才问）
        rp = _rules_prompt(preview)
        if rp:
            try:
                d2 = _extract_json(_chat(rp, max_tokens=8000, timeout=300))
                if isinstance(d2, dict):
                    pr = _norm_rules(d2.get("point_rules"))
                    if pr:
                        out["point_rules"] = pr
            except Exception:  # noqa: BLE001
                pass
        return out
    except Exception:  # noqa: BLE001
        return {}


# 兼容旧调用名：只返回列映射
def ai_parse_visit_cols(path: str) -> dict:
    r = ai_parse_visit_layout(path)
    return r.get("cols", {}) if r else {}


# ---------- 默认口径（来自 .env 配置，非代码硬编码） ----------

def parse_vm_text(text: str) -> Optional[dict]:
    """"KEY=值, ~=空串" → {KEY: 值}；空输入 → None。"""
    text = (text or "").strip()
    if not text:
        return None
    out = {}
    for part in text.split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip(), v.strip()
        out["" if k == "~" else k] = v
    return out or None


def parse_pr_text(text: str) -> Optional[list]:
    """行式文本 → point_rules（每行：可见值|列表 & 投放值|列表 = 点数；~=空、*=任意）。"""
    text = (text or "").strip()
    if not text:
        return None
    out = []
    for line in __import__("re").split(r"[\n,]+", text.replace("\r", "")):
        line = line.strip()
        if not line or "=" not in line:
            continue
        left, pts = line.rsplit("=", 1)
        pts = pts.strip()
        if not pts.isdigit():
            continue
        left = left.strip()
        vis_part = left.split("&")[0] if "&" in left else left
        dep_part = left.split("&")[1] if "&" in left else None

        def _vals(part):
            if part is None:
                return None
            vs = [x.strip() for x in part.split("|") if x.strip() != ""]
            vs = ["" if x == "~" else x for x in vs]
            if "*" in vs:
                return None
            return vs or None

        out.append({"visible": _vals(vis_part), "deploy": _vals(dep_part),
                    "points": int(pts)})
    return out or None


def default_layout() -> dict:
    """从配置（.env）取默认口径布局（供无规则说明时兜底）；未配置 → {}。"""
    from app.config import get_settings as _st
    c = _st()
    vmv = parse_vm_text(c.default_visible_map)
    vmd = parse_vm_text(c.default_deploy_map)
    pr = parse_pr_text(c.default_point_rules)
    if not (vmv or vmd or pr):
        return {}
    out = {"source": "default"}
    vm = {}
    if vmv:
        vm["visible"] = vmv
    if vmd:
        try:
            vm["deploy"] = {k: int(v) for k, v in vmd.items()}
        except (TypeError, ValueError):
            vm.pop("deploy", None)
    if vm:
        out["value_map"] = vm
    if pr:
        out["point_rules"] = pr
    return out


def merge_defaults(layout: Optional[dict]) -> Optional[dict]:
    """AI/规则布局缺 value_map 或 point_rules 时，用配置默认口径补齐。"""
    if not layout:
        return None
    d = default_layout()
    if not d:
        return layout
    out = dict(layout)
    if not out.get("value_map") and d.get("value_map"):
        out["value_map"] = d["value_map"]
    if not out.get("point_rules") and d.get("point_rules"):
        out["point_rules"] = d["point_rules"]
    return out
