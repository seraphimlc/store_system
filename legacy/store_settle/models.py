# -*- coding: utf-8 -*-
"""引擎数据模型（与 spec v0.4 §4.3/§4.6 字段对齐，后续可直接落表）。"""
from dataclasses import dataclass, field
from datetime import datetime, date
from enum import Enum
from typing import Optional, List


class Bucket(Enum):
    FINAL = "final"
    DUP_BY_ID = "dup_by_id"
    DUP_BY_NAME = "dup_by_name"
    VISIBLE_BLANK = "visible_blank"
    MANUAL_VOID = "manual_void"   # 正式行被人工裁定为无效（不参与计点）


@dataclass
class ParsedRow:
    """loader 输出的一行原始记录（与 spec raw_records 对齐）。"""
    import_id: int
    sheet_name: str
    excel_row: int
    store_id_raw: str = ""
    store_name_local_raw: str = ""
    store_name_en_raw: str = ""
    modified_raw: str = ""
    submitter_raw: str = ""
    submitter_code: Optional[str] = None
    record_id_raw: str = ""
    visible_raw: str = ""          # "", "YES", "NO"
    deploy_raw: str = ""           # "", "YES", "NO"
    original_row: List[object] = field(default_factory=list)


@dataclass
class CleanRecord:
    """pipeline 输出：逐行判定（与 spec clean_records 对齐）。"""
    row: ParsedRow
    store_id: str
    store_name: str                # trim 后展示快照
    modified_at: Optional[datetime] = None   # 候选行必有值；空白桶可为 None（防御）
    japan_date: Optional[date] = None
    bucket: Bucket = Bucket.FINAL
    dup_of_idx: Optional[int] = None   # 指向本 run 内直接淘汰者（CleanRecord 序号）
