# -*- coding: utf-8 -*-
"""从上传 Excel 的明确地址列，安全补充店铺实体地址。

规则：只认表头明确标注的地址列；按 Store ID 精确匹配；只补空、不覆盖；
同一文件中同一 Store ID 出现多个不同地址时跳过；不按内容遍历整行猜地址。
"""
from __future__ import annotations

import re
from collections import defaultdict

from openpyxl import load_workbook

from app.models import StoreEntity

_ADDRESS_HEADERS = (
    "store address-local", "store address local", "store address-english",
    "store address english", "store address", "shop address",
    "店舗住所", "店铺地址", "店舖地址", "地址", "住所",
)
_STORE_ID_HEADERS = (
    "store basic id", "store id", "store code", "storecode",
    "店舗id", "店铺id", "店舖id",
)
_STORE_NAME_HEADERS = (
    "store name-local", "store name local", "shop name", "store name",
    "店舗名", "店铺名", "店舖名", "店名",
)
_PREF_ONLY = re.compile(r"^(東京都|北海道|大阪府|京都府|.{2,3}県)$")
_PREF = re.compile(r"(東京都|北海道|大阪府|京都府|.{2,3}県)")
_JP_PLACE = re.compile(r"(市|区|郡|町|村|丁目)")
_NUMBER = re.compile(r"[0-9０-９一二三四五六七八九十百]+")
_EN_PLACE = re.compile(r"\b(tokyo|osaka|kyoto|hokkaido|japan|city|ward|prefecture)\b", re.I)


def _header(value) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _find_col(row, candidates):
    normalized = [_header(v) for v in row]
    for candidate in candidates:
        for i, value in enumerate(normalized):
            if value == candidate:
                return i
    return None


def _find_address_cols(row):
    normalized = [_header(v) for v in row]
    result = []
    for i, value in enumerate(normalized):
        if not value or "email" in value:
            continue
        if value in _ADDRESS_HEADERS:
            # 本地/日文地址优先于英文地址。
            rank = 0 if ("local" in value or value in ("住所", "地址", "店舗住所", "店铺地址", "店舖地址")) else 1
            result.append((rank, i))
    return [i for _, i in sorted(result)]


def clean_address(value) -> str:
    """仅做无损格式清洗，不翻译或改写地址主体。"""
    text = str(value or "").replace("\r\n", " ").replace("\n", " ").replace("\r", " ").strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+NULL\s*$", "", text, flags=re.I).strip()
    text = re.sub(r"^日本[、,]\s*", "", text)
    return text


def _compact(value) -> str:
    return re.sub(r"[\s　,，、]+", "", str(value or "")).casefold()


def valid_address(address: str, store_name: str = "") -> bool:
    """保守校验：拒绝店名、仅都道府县及缺乏门牌结构的普通文本。"""
    value = clean_address(address)
    if len(value) < 6 or _PREF_ONLY.fullmatch(value):
        return False
    if store_name and _compact(value) == _compact(store_name):
        return False
    if value.lower().startswith(("http://", "https://")):
        return False
    has_number = bool(_NUMBER.search(value))
    japanese = bool(_PREF.search(value) and _JP_PLACE.search(value) and has_number)
    english = bool(_EN_PLACE.search(value) and has_number)
    return japanese or english


def collect_workbook_addresses(path: str, max_header_rows: int = 20) -> dict:
    """读取所有 Sheet 的明确地址列，返回唯一地址及冲突统计。"""
    candidates = defaultdict(set)
    names = defaultdict(set)
    rows_with_address = invalid = sheets = 0
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        for ws in wb.worksheets:
            found = None
            for row_no, row in enumerate(ws.iter_rows(min_row=1, max_row=max_header_rows,
                                                       values_only=True), 1):
                id_col = _find_col(row, _STORE_ID_HEADERS)
                address_cols = _find_address_cols(row)
                if id_col is not None and address_cols:
                    found = (row_no, id_col, _find_col(row, _STORE_NAME_HEADERS), address_cols)
                    break
            if found is None:
                continue
            sheets += 1
            header_row, id_col, name_col, address_cols = found
            for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
                sid = str(row[id_col] if id_col < len(row) and row[id_col] is not None else "").strip()
                if not sid:
                    continue
                name = str(row[name_col] if name_col is not None and name_col < len(row) and row[name_col] is not None else "").strip()
                if name:
                    names[sid].add(name)
                chosen = ""
                for col in address_cols:
                    value = clean_address(row[col] if col < len(row) else "")
                    if not value:
                        continue
                    if valid_address(value, name):
                        chosen = value
                        break
                    invalid += 1
                if chosen:
                    candidates[sid].add(chosen)
                    rows_with_address += 1
    finally:
        wb.close()
    unique = {sid: next(iter(values)) for sid, values in candidates.items() if len(values) == 1}
    conflicts = {sid: sorted(values) for sid, values in candidates.items() if len(values) > 1}
    return {"addresses": unique, "conflicts": conflicts, "names": names,
            "sheets": sheets, "rows_with_address": rows_with_address,
            "invalid": invalid}


def backfill_from_workbook(db, path: str, *, commit: bool = True) -> dict:
    """按 Store ID 给已有 StoreEntity 补空地址；绝不覆盖已有值。"""
    data = collect_workbook_addresses(path)
    addresses = data["addresses"]
    entities = {}
    if addresses:
        entities = {e.store_id_raw: e for e in db.query(StoreEntity).filter(
            StoreEntity.store_id_raw.in_(list(addresses))).all()}
    filled = already = unmatched = 0
    for sid, address in addresses.items():
        entity = entities.get(sid)
        if entity is None:
            unmatched += 1
        elif str(entity.address_local or "").strip():
            already += 1
        else:
            entity.address_local = address
            filled += 1
    if commit:
        db.commit()
    else:
        db.flush()
    return {"filled": filled, "already": already, "unmatched": unmatched,
            "conflicts": len(data["conflicts"]), "invalid": data["invalid"],
            "valid_store_ids": len(addresses), "sheets": data["sheets"],
            "rows_with_address": data["rows_with_address"]}
