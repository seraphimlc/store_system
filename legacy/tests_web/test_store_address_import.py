# -*- coding: utf-8 -*-
"""巡店/对账 Excel 地址列 → 店铺实体的安全补空。"""
from io import BytesIO

from openpyxl import Workbook

from app.models import StoreEntity
from app.services import store_address


def _xlsx(rows, headers=None, sheet="Data"):
    wb = Workbook(); ws = wb.active; ws.title = sheet
    ws.append(headers or ["Store ID", "Store Name-Local", "Store Address-Local"])
    for row in rows: ws.append(row)
    out = BytesIO(); wb.save(out); return out.getvalue()


def _path(tmp_path, content):
    p = tmp_path / "addresses.xlsx"; p.write_bytes(content); return str(p)


def _entity(db, sid, name, address=None):
    e = StoreEntity(store_id_raw=sid, name_local=name, name_norm=name,
                    address_local=address, master_id=0)
    db.add(e); db.flush(); e.master_id = e.id; db.commit(); return e


def test_valid_address_rejects_names_and_incomplete_values():
    assert store_address.valid_address("東京都渋谷区渋谷１丁目１４−６", "某店")
    assert store_address.valid_address("3-chōme-31-6 Shinjuku, Tokyo 160-0022, Japan", "某店")
    assert not store_address.valid_address("東京都", "某店")
    assert not store_address.valid_address("北海道ラーメン ひむろ 亀戸店",
                                           "北海道ラーメン ひむろ 亀戸店")
    assert not store_address.valid_address("福井県アンテナショップ", "別名")
    assert not store_address.valid_address("https://example.com", "某店")


def test_backfill_from_explicit_address_column_only_fills_empty(db, tmp_path):
    a = _entity(db, "S1", "一号店")
    b = _entity(db, "S2", "二号店", "既存住所2丁目3-4")
    content = _xlsx([
        ["S1", "一号店", "日本、〒150-0002 東京都渋谷区渋谷１丁目１４−６"],
        ["S2", "二号店", "東京都新宿区新宿３丁目１−１"],
        ["S3", "未建档", "東京都台東区浅草２丁目１１−５"],
    ])
    stats = store_address.backfill_from_workbook(db, _path(tmp_path, content))
    db.refresh(a); db.refresh(b)
    assert a.address_local == "〒150-0002 東京都渋谷区渋谷１丁目１４−６"
    assert b.address_local == "既存住所2丁目3-4"
    assert stats == {"filled": 1, "already": 1, "unmatched": 1,
                     "conflicts": 0, "invalid": 0, "valid_store_ids": 3,
                     "sheets": 1, "rows_with_address": 3}


def test_conflict_and_invalid_address_are_not_written(db, tmp_path):
    a = _entity(db, "S1", "一号店")
    b = _entity(db, "S2", "北海道ラーメン ひむろ 亀戸店")
    content = _xlsx([
        ["S1", "一号店", "東京都渋谷区渋谷１丁目１４−６"],
        ["S1", "一号店", "大阪府大阪市北区梅田１丁目１−１"],
        ["S2", "北海道ラーメン ひむろ 亀戸店", "北海道ラーメン ひむろ 亀戸店"],
    ])
    stats = store_address.backfill_from_workbook(db, _path(tmp_path, content))
    db.refresh(a); db.refresh(b)
    assert a.address_local is None and b.address_local is None
    assert stats["conflicts"] == 1
    assert stats["invalid"] == 1
    assert stats["filled"] == 0


def test_does_not_guess_address_from_arbitrary_cells(db, tmp_path):
    e = _entity(db, "S1", "一号店")
    content = _xlsx(
        [["S1", "一号店", "東京都渋谷区渋谷１丁目１４−６"]],
        headers=["Store ID", "Store Name-Local", "备注"])
    stats = store_address.backfill_from_workbook(db, _path(tmp_path, content))
    db.refresh(e)
    assert e.address_local is None
    assert stats["sheets"] == 0
