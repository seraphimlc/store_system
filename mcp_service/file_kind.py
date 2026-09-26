# -*- coding: utf-8 -*-
"""上传文件类型自动识别：巡店记录 / 对账明细 / 手工结算对照件 / 未知。

**为什么要识别**：在 WorkBuddy 里用户只是"把文件丢进来"，不该由用户或模型
决定走哪条通道。两条通道的处理链完全不同：

| 类型 | 通道 | 处理 |
|---|---|---|
| 巡店记录（MarsNavi STORE VISIT RECORD，`daily_records`） | `visit_upload` | 解析 → 判定 → 入正式表 → 工资/找平/看板 |
| 对账明细（如 Alipay 结算数据，`recon`） | `visit_upload` | 解析 → 与系统人日统计比对 → 差异行 |
| 手工结算对照件（巡回最终结算，`manual`） | 不入库 | 系统无对应通道，返回指引 |
| 其他（`unknown`） | 询问用户 | 返回 NEED_FILE_KIND，让用户/模型带 kind 重传 |

**kind 统一枚举（P1-7）**：`daily_records`（巡店）| `recon`（对账）| `manual` | `unknown`。
`visit` 是 `daily_records` 的历史别名（`visit_upload` 的 `kind` 参数仍兼容，见
`normalize_kind`），对外输出一律用规范枚举。

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

# 规范枚举（P1-7 统一口径；对账响应 task.kind 也用这套值）
KIND_DAILY_RECORDS = "daily_records"     # 巡店记录
KIND_RECON = "recon"                     # 对账明细
KIND_MANUAL = "manual"                   # 手工结算对照件（不入库）
KIND_UNKNOWN = "unknown"
CANONICAL_KINDS = (KIND_DAILY_RECORDS, KIND_RECON, KIND_MANUAL, KIND_UNKNOWN)
# 历史别名 → 规范值（`visit_upload.kind` 兼容旧值 visit）
KIND_ALIASES = {"visit": KIND_DAILY_RECORDS}

# 对账文件专有列（命中 ≥2 个即认定）
_RECON_MARKERS = ("statement date", "agent name", "iso pid", "iso name",
                  "action type", "shop name", "store basic id")
# 巡店记录专有列（辅助判据，sheet 名缺失时兜底）
_VISIT_MARKERS = ("modified time", "submitter", "deploy new a+posm",
                  "review status", "store name-local")
# 手工结算对照件 sheet 名
_MANUAL_SHEETS = ("月度总览", "人员汇总", "最终有效数据", "全部重复数据")

# 预估用：人的 key 列名（按列名 lower 匹配；巡店=提交人/对账=agent）
_PERSON_KEY_COLS = {
    KIND_DAILY_RECORDS: ("submitter", "提交人"),
    KIND_RECON: ("agent name", "agent", "担当", "姓名"),
}
_ESTIMATE_CAP = 20000          # 预估行数上限（防止超大文件拖慢 dry_run）
_MAX_HEADER_SCAN = 30          # 找表头的最大扫描行


def _norm(s: Any) -> str:
    return str(s or "").strip()


def normalize_kind(kind: str | None) -> str | None:
    """把用户/模型传入的 kind 归一为规范枚举；None → None；非法值原样返回（由调用方校验）。

    兼容旧值：`visit` → `daily_records`；`recon` 不变。
    """
    if kind is None:
        return None
    k = str(kind).strip().lower()
    if k in KIND_ALIASES:
        return KIND_ALIASES[k]
    return k


def detect_kind(content: bytes) -> dict[str, Any]:
    """返回 {kind, reason, sheets, header}；kind ∈ daily_records/recon/manual/unknown。"""
    try:
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(content), read_only=True)
    except Exception as exc:  # noqa: BLE001
        return {"kind": KIND_UNKNOWN, "reason": f"无法打开为 Excel：{exc!r}",
                "sheets": [], "header": []}

    try:
        sheets = [_norm(s) for s in wb.sheetnames]
        if any(s == VISIT_SHEET for s in sheets):
            return {"kind": KIND_DAILY_RECORDS,
                    "reason": f"sheet 名为 {VISIT_SHEET}（巡店记录模板）",
                    "sheets": sheets, "header": []}

        if any(s in _MANUAL_SHEETS for s in sheets):
            return {"kind": KIND_MANUAL,
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
            return {"kind": KIND_RECON,
                    "reason": f"表头命中对账专有列 {recon_hits} 个",
                    "sheets": sheets, "header": header}
        if visit_hits >= 2:
            return {"kind": KIND_DAILY_RECORDS,
                    "reason": f"表头命中巡店记录列 {visit_hits} 个（无模板 sheet 名）",
                    "sheets": sheets, "header": header}
        if recon_hits >= 2:
            return {"kind": KIND_RECON,
                    "reason": f"表头命中对账列 {recon_hits} 个",
                    "sheets": sheets, "header": header}
        return {"kind": KIND_UNKNOWN,
                "reason": "未识别出巡店记录或对账明细的表头特征",
                "sheets": sheets, "header": header}
    finally:
        try:
            wb.close()
        except Exception:  # noqa: BLE001
            pass


def infer_month(content: bytes, kind: str) -> str | None:
    """从文件内容推断结算月（YYYY-MM）：取数据行日期列的众数月份。

    仅用于对账文件（`kind=recon`；巡店记录的月份由其原始数据自身推导）。
    """
    if normalize_kind(kind) != KIND_RECON:
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


def estimate(content: bytes, kind: str) -> dict[str, int]:
    """轻量影响预估（P2-12 dry_run 用）：数据行数 + 去重人数。

    只扫第一个 sheet（或巡店模板 sheet）：找表头 → 数数据行 → 按人 key 列
    数去重人数。**不做任何判定/写入**；上限 `_ESTIMATE_CAP` 行防超大文件拖慢。
    """
    k = normalize_kind(kind)
    keys = _PERSON_KEY_COLS.get(k, ())
    out = {"estimated_rows": 0, "estimated_persons": 0}
    try:
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(content), read_only=True)
    except Exception:  # noqa: BLE001
        return out
    try:
        sheet = wb[VISIT_SHEET] if VISIT_SHEET in wb.sheetnames \
            else wb[wb.sheetnames[0]]
        header = None
        rows = sheet.iter_rows(values_only=True)
        for _ in range(_MAX_HEADER_SCAN):
            try:
                r = next(rows)
            except StopIteration:
                return out
            vals = [_norm(c) for c in r]
            if any(vals):
                header = [c.lower() for c in vals]
                break
        if header is None:
            return out
        key_idx = next((i for i, h in enumerate(header)
                        if h in keys), None)
        seen: set[str] = set()
        n = 0
        for r in rows:
            vals = [_norm(c) for c in r]
            if not any(vals):
                continue
            n += 1
            if key_idx is not None and key_idx < len(vals) and vals[key_idx]:
                seen.add(vals[key_idx])
            if n >= _ESTIMATE_CAP:
                break
        out["estimated_rows"] = n
        out["estimated_persons"] = len(seen)
        return out
    finally:
        try:
            wb.close()
        except Exception:  # noqa: BLE001
            pass
