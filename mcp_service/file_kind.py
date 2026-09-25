# -*- coding: utf-8 -*-
"""上传文件类型自动识别：巡店记录 / 对账明细 / 手工结算对照件 / 未知。

**为什么要识别**：在 WorkBuddy 里用户只是"把文件丢进来"，不该由用户或模型
决定走哪条通道。两条通道的处理链完全不同：

| 类型 | 通道 | 处理 |
|---|---|---|
| 巡店记录（MarsNavi STORE VISIT RECORD） | `visit_upload_file` | 解析 → 判定 → 入正式表 → 工资/找平/看板 |
| 对账明细（如 Alipay 结算数据） | `visit_upload_recon` | 解析 → 与系统人日统计比对 → 差异行 |
| 手工结算对照件（巡回最终结算） | 不入库 | 系统无对应通道，返回指引 |

**判据优先级**（实测于真实文件）：
1. sheet 名（去空白）== `STORE_TASK_EXCEL_SHEET` → 巡店记录（该模板名由 loader 固定，最可靠）
2. 表头含对账专有列（statement date / agent name / iso pid / shop name + action type）→ 对账明细
3. sheet 名含「月度总览 / 人员汇总 / 最终有效数据 / 全部重复数据」→ 手工对照件
4. 否则 → unknown

注意：巡店记录与对账文件**都有店/人/日期列**，故不能只按列名判断——sheet 名优先。
"""
import io
from typing import Any

VISIT_SHEET = "STORE_TASK_EXCEL_SHEET"

# 对账文件专有列（命中 ≥2 个即认定）
_RECON_MARKERS = ("statement date", "agent name", "iso pid", "iso name",
                  "action type", "shop name", "store basic id")
# 巡店记录专有列（辅助判据，sheet 名缺失时兜底）
_VISIT_MARKERS = ("modified time", "submitter", "deploy new a+posm",
                  "review status", "store name-local")
# 手工结算对照件 sheet 名
_MANUAL_SHEETS = ("月度总览", "人员汇总", "最终有效数据", "全部重复数据")


def _norm(s: Any) -> str:
    return str(s or "").strip()


def detect_kind(content: bytes) -> dict[str, Any]:
    """返回 {kind, reason, sheets, header}；kind ∈ visit/recon/manual/unknown。"""
    try:
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(content), read_only=True)
    except Exception as exc:  # noqa: BLE001
        return {"kind": "unknown", "reason": f"无法打开为 Excel：{exc!r}",
                "sheets": [], "header": []}

    try:
        sheets = [_norm(s) for s in wb.sheetnames]
        if any(s == VISIT_SHEET for s in sheets):
            return {"kind": "visit",
                    "reason": f"sheet 名为 {VISIT_SHEET}（巡店记录模板）",
                    "sheets": sheets, "header": []}

        if any(s in _MANUAL_SHEETS for s in sheets):
            return {"kind": "manual",
                    "reason": "sheet 含「月度总览/人员汇总/最终有效数据」等"
                              "（手工结算对照件，系统无对应通道）",
                    "sheets": sheets, "header": []}

        ws = wb[wb.sheetnames[0]]
        header = []
        for row in ws.iter_rows(max_row=3, values_only=True):
            header = [_norm(c) for c in row]
            if any(header):
                break
        low = [c.lower() for c in header]
        joined = "|".join(low)

        recon_hits = sum(1 for m in _RECON_MARKERS if m in joined)
        visit_hits = sum(1 for m in _VISIT_MARKERS if m in joined)
        if recon_hits >= 2 and visit_hits == 0:
            return {"kind": "recon",
                    "reason": f"表头命中对账专有列 {recon_hits} 个",
                    "sheets": sheets, "header": header}
        if visit_hits >= 2:
            return {"kind": "visit",
                    "reason": f"表头命中巡店记录列 {visit_hits} 个（无模板 sheet 名）",
                    "sheets": sheets, "header": header}
        if recon_hits >= 2:
            return {"kind": "recon",
                    "reason": f"表头命中对账列 {recon_hits} 个",
                    "sheets": sheets, "header": header}
        return {"kind": "unknown",
                "reason": "未识别出巡店记录或对账明细的表头特征",
                "sheets": sheets, "header": header}
    finally:
        try:
            wb.close()
        except Exception:  # noqa: BLE001
            pass


def infer_month(content: bytes, kind: str) -> str | None:
    """从文件内容推断结算月（YYYY-MM）：取数据行日期列的众数月份。

    仅用于对账文件（巡店记录的月份由其原始数据自身推导）。
    """
    if kind != "recon":
        return None
    import collections
    import datetime as _dt
    from openpyxl import load_workbook
    try:
        wb = load_workbook(io.BytesIO(content), read_only=True)
    except Exception:  # noqa: BLE001
        return None
    try:
        ws = wb[wb.sheetnames[0]]
        rows = ws.iter_rows(max_row=300, values_only=True)
        header = None
        for r in rows:
            vals = [_norm(c) for c in r]
            if any(vals):
                header = [v.lower() for v in vals]
                break
        if not header:
            return None
        idx = next((i for i, h in enumerate(header)
                    if h in ("statement date", "modified time", "日期",
                             "记录日期")), None)
        if idx is None:
            return None
        counter: collections.Counter = collections.Counter()
        for r in rows:
            v = r[idx] if idx < len(r) else None
            if isinstance(v, (_dt.datetime, _dt.date)):
                counter[f"{v.year:04d}-{v.month:02d}"] += 1
            elif isinstance(v, str) and len(v) >= 7 and v[:4].isdigit():
                counter[v[:7]] += 1
        return counter.most_common(1)[0][0] if counter else None
    finally:
        try:
            wb.close()
        except Exception:  # noqa: BLE001
            pass
