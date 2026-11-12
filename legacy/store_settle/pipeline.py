# -*- coding: utf-8 -*-
"""清洗流水线：合并 → Visible 过滤 → 排序 → ID 去重 → 店名去重（spec v0.4 §5.1）。

- 候选 = Visible 非空（YES/NO）；空白记录单独归桶 visible_blank，不判重。
- 平局全序 (modified_at, import_id, sheet_name, excel_row)。
- 空白 ID/空白店名之间永不互判重。
- dup_of 指向“直接淘汰者”的 CleanRecord 序号（允许 A 链指向非 final 行）。
"""
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Tuple

from store_settle.models import ParsedRow, CleanRecord, Bucket
from store_settle.rules import parse_modified_jst


@dataclass
class RunResult:
    records: List[CleanRecord]                 # 全量：候选行按全序排序在前，空白桶行追加在后
    summary: Dict[str, int] = field(default_factory=dict)
    tie_groups: int = 0
    empty_key_final: int = 0                   # 同时 ID/店名空白却进入 final 的行数（spec §5.1-7 warnings）
    params: dict = field(default_factory=dict)

    @property
    def finals(self) -> List[CleanRecord]:
        return [r for r in self.records if r.bucket == Bucket.FINAL]


def run_pipeline(rows: List[ParsedRow],
                 name_mode: str = "trim",
                 boundary_date: date = date(2026, 7, 9)) -> RunResult:
    """spec §5.1 固定顺序的完整实现（无外部依赖）。"""
    cand: List[Tuple[ParsedRow, object]] = []
    blanks: List[CleanRecord] = []
    for r in rows:
        dt = parse_modified_jst(r.modified_raw)
        if r.visible_raw not in ("YES", "NO"):
            blanks.append(CleanRecord(row=r, store_id=r.store_id_raw.strip(),
                                      store_name=r.store_name_local_raw.strip(),
                                      modified_at=dt,
                                      japan_date=dt.date() if dt else None,
                                      bucket=Bucket.VISIBLE_BLANK))
            continue
        if dt is None:  # loader 已挡住；这里防御，绝不猜测
            raise ValueError(f"unparseable modified time: imp={r.import_id} "
                             f"sheet={r.sheet_name!r} row={r.excel_row} raw={r.modified_raw!r}")
        cand.append((r, dt))

    # 平局审计：相同 modified_at 的候选行组数（组员>1 计 1 组）。
    # 保留先后由排序全序决定，此处仅为 run 审计指标（真实数据同刻罕见）。
    groups: Dict[object, int] = {}
    for r, dt in cand:
        groups[dt] = groups.get(dt, 0) + 1
    tie_groups = sum(1 for c in groups.values() if c > 1)

    cand.sort(key=lambda t: (t[1], t[0].import_id, t[0].sheet_name, t[0].excel_row))

    # A 列去重：先全部按序落位（默认 FINAL），同 trim 后非空 ID 的后到者标 dup_by_id
    records: List[CleanRecord] = []
    keeper_by_id: Dict[str, int] = {}
    for r, dt in cand:
        sid = r.store_id_raw.strip()
        idx = len(records)
        rec = CleanRecord(row=r, store_id=sid,
                          store_name=r.store_name_local_raw.strip(),
                          modified_at=dt, japan_date=dt.date())
        records.append(rec)
        if sid == "":
            continue                     # 空白 ID 之间永不互判重
        prev = keeper_by_id.get(sid)
        if prev is None:
            keeper_by_id[sid] = idx
        else:
            rec.bucket = Bucket.DUP_BY_ID
            rec.dup_of_idx = prev

    # B 列去重：只在 A 幸存者（当前仍为 FINAL）上执行；空白店名跳过
    keeper_by_name: Dict[str, int] = {}
    for i, rec in enumerate(records):
        if rec.bucket != Bucket.FINAL:
            continue
        key = rec.store_name if name_mode == "trim" else rec.row.store_name_local_raw
        if key == "" or (name_mode == "raw" and key.strip() == ""):  # 纯空白店名也视为空白

            continue                     # 空白店名之间永不互判重
        prev = keeper_by_name.get(key)
        if prev is None:
            keeper_by_name[key] = i
        else:
            rec.bucket = Bucket.DUP_BY_NAME
            rec.dup_of_idx = prev        # dup_of 必为 final（该名单最早幸存者）

    all_records = records + blanks
    summary = {b.value: 0 for b in Bucket}
    for rec in all_records:
        summary[rec.bucket.value] += 1
    empty_key_final = sum(1 for rec in records
                          if rec.bucket == Bucket.FINAL
                          and rec.store_id == "" and rec.store_name == "")
    return RunResult(records=all_records, summary=summary, tie_groups=tie_groups,
                     empty_key_final=empty_key_final,
                     params={"name_mode": name_mode,
                             "boundary_date": str(boundary_date)})
