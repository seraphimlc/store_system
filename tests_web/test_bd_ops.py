# -*- coding: utf-8 -*-
"""BD 作业域 P0 测试：行政区划基底（`bd_area`）+ 门店宇宙（`bd_store`）。

设计 `docs/specs-bd-ops-layer.md` §3.3 / §5.1 / §5.3。
P0 的目标：把解析层当初丢掉的地址/业态列提出来 + 装官方行政区划。
**硬边界**：只新增 bd_* 表，绝不碰结算域。
"""
import json
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.db as appdb
from app.db import Base
from app.models import BdArea, BdStore, RawRecord
from app.services import bd_area, bd_store

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_XLSX = os.path.join(ROOT, "data", "bd_soumu_codes.xlsx")


@pytest.fixture
def db():
    engine = create_engine("sqlite://",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    appdb._ENGINE = engine
    appdb.SessionLocal = sessionmaker(bind=engine, future=True,
                                      expire_on_commit=False)
    Base.metadata.create_all(engine)
    s = appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()
        Base.metadata.drop_all(engine)


def _make_xlsx(path):
    """造一份与総務省结构一致的小样本（含 1 政令市 + 2 区）。"""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["団体コード", "都道府県名（漢字）", "市区町村名（漢字）",
               "都道府県名（カナ）", "市区町村名（カナ）"])
    ws.append(["110001", "埼玉県", None, "ｻｲﾀﾏｹﾝ", None])
    ws.append(["111007", "埼玉県", "さいたま市", "ｻｲﾀﾏｹﾝ", "ｻｲﾀﾏｼ"])
    ws.append(["112011", "埼玉県", "川越市", "ｻｲﾀﾏｹﾝ", "ｶﾜｺﾞｴｼ"])
    ws.append(["130001", "東京都", None, "ﾄｳｷｮｳﾄ", None])
    ws.append(["131016", "東京都", "千代田区", "ﾄｳｷｮｳﾄ", "ﾁﾖﾀﾞｸ"])
    ws.append(["140001", "神奈川県", None, "ｶﾅｶﾞﾜｹﾝ", None])
    ws.append(["141003", "神奈川県", "横浜市", "ｶﾅｶﾞﾜｹﾝ", "ﾖｺﾊﾏｼ"])
    ws2 = wb.create_sheet("政令指定都市")
    ws2.append(["団体コード", "都道府県名（漢字）", "市区町村名（漢字）",
                "都道府県名（ｶﾅ）", "市区町村名（ｶﾅ）"])
    # 政令市自己也占一行 —— 必须被跳过
    ws2.append(["111007", "埼玉県", "さいたま市", "ｻｲﾀﾏｹﾝ", "ｻｲﾀﾏｼ"])
    ws2.append(["111015", "埼玉県", "さいたま市西区", "ｻｲﾀﾏｹﾝ", "ｻｲﾀﾏｼﾆｼｸ"])
    ws2.append(["111023", "埼玉県", "さいたま市北区", "ｻｲﾀﾏｹﾝ", "ｻｲﾀﾏｼｷﾀｸ"])
    ws2.append(["141003", "神奈川県", "横浜市", "ｶﾅｶﾞﾜｹﾝ", "ﾖｺﾊﾏｼ"])
    ws2.append(["141011", "神奈川県", "横浜市鶴見区", "ｶﾅｶﾞﾜｹﾝ", "ﾖｺﾊﾏｼﾂﾙﾐｸ"])
    wb.save(path)


# ---------------- 行政区划 ----------------

def test_parse_soumu_basic(db, tmp_path):
    p = str(tmp_path / "c.xlsx")
    _make_xlsx(p)
    rows = bd_area.parse_soumu_xlsx(p, ("埼玉県", "東京都", "神奈川県"))
    by = {(r["level"], r["code"]): r for r in rows}

    # 都道府県：code 取 2 位
    assert by[("pref", "11")]["name"] == "埼玉県"
    assert by[("pref", "13")]["name"] == "東京都"
    # 市区町村：code 6 位，pref_code 取前 2 位
    assert by[("city", "111007")]["name"] == "さいたま市"
    assert by[("city", "111007")]["pref_code"] == "11"
    assert by[("city", "131016")]["name"] == "千代田区"


def test_parse_soumu_skips_city_row_and_resolves_ward_parent(db, tmp_path):
    """⚠️ 刻意回归：政令市自己那一行不能变成 ward；区的父级要指到市。"""
    p = str(tmp_path / "c.xlsx")
    _make_xlsx(p)
    rows = bd_area.parse_soumu_xlsx(p, ("埼玉県", "東京都", "神奈川県"))
    wards = [r for r in rows if r["level"] == "ward"]
    codes = {r["code"] for r in wards}
    # 政令市自己的 code 不得出现在 ward 里
    assert "111007" not in codes, "政令市自身被误当成 ward"
    assert "141003" not in codes, "政令市自身被误当成 ward"
    # 区名应是去掉市名的短名，父级指向市
    by = {r["code"]: r for r in wards}
    assert by["111015"]["name"] == "西区"
    assert by["111015"]["parent_code"] == "111007"
    assert by["111015"]["city_code"] == "111007"
    assert by["141011"]["name"] == "鶴見区"
    assert by["141011"]["parent_code"] == "141003"


def test_sync_areas_idempotent(db, tmp_path):
    p = str(tmp_path / "c.xlsx")
    _make_xlsx(p)
    s1 = bd_area.sync_areas(db, p, ("埼玉県", "東京都", "神奈川県"))
    assert s1["added"] == s1["total"] > 0
    n1 = db.query(BdArea).count()
    s2 = bd_area.sync_areas(db, p, ("埼玉県", "東京都", "神奈川県"))
    assert s2["added"] == 0 and s2["updated"] == s1["total"]
    assert db.query(BdArea).count() == n1, "重复同步不应产生新行"


@pytest.mark.skipif(not os.path.exists(REAL_XLSX),
                    reason="需要 data/bd_soumu_codes.xlsx（跑 scripts/bd_init.py 生成）")
def test_real_official_counts(db):
    """用真实官方表校验一都三県数量（官方：东京62/埼玉63/千叶54/神奈川33）。"""
    st = bd_area.sync_areas(db, REAL_XLSX)
    assert st["pref"] == 4
    assert st["city"] == 212, st
    assert st["ward"] == 44, st
    for name, want in (("東京都", 62), ("埼玉県", 63),
                       ("千葉県", 54), ("神奈川県", 33)):
        pref = bd_area.pref_of(db, name)
        assert pref is not None, name
        assert len(bd_area.cities_of(db, pref.code)) == want, name
    # 每个 ward 的父级都必须能指到一个 city
    city_codes = {c.code for c in db.query(BdArea)
                  .filter(BdArea.level == "city").all()}
    bad = [w.code for w in db.query(BdArea)
           .filter(BdArea.level == "ward").all()
           if w.parent_code not in city_codes]
    assert bad == [], f"父级缺失的区: {bad}"


# ---------------- 地址内容识别（四种列布局） ----------------

def test_find_address_content_detection():
    # 50 列布局：地址在第 21 列
    row50 = [None] * 30
    row50[1] = "TASTING MARKET"
    row50[12] = "Dining"
    row50[21] = "日本、〒104-0061 東京都中央区銀座３丁目６−１ 松屋銀座 8階"
    assert bd_store.find_address(row50) == "東京都中央区銀座３丁目６−１ 松屋銀座 8階"
    assert bd_store.find_gyotai(row50) == "Dining"

    # 7 列窄表：没有地址 → 返回空
    row7 = ["0101047092026091503825241", "Honegori ", "2026-09-16 07:54:44",
            "陳偉鋒(2188240606730380)", "Store Information Confirmation",
            "AUDIT_FAILED", None]
    assert bd_store.find_address(row7) == ""

    # 位置换了也要认出来（不依赖下标）
    row_shift = [None, "どこか商店", None, None, None, None, None, None,
                 None, None, None, None, None, None, None, None, None, None,
                 None, "東京都杉並区高円寺北２丁目３−１３ 磯部ビル 101"]
    assert bd_store.find_address(row_shift) == \
        "東京都杉並区高円寺北２丁目３−１３ 磯部ビル 101"


def test_clean_address_strips_prefix():
    assert bd_store.clean_address("日本、〒166-0002 東京都杉並区高円寺北２丁目３−１３") \
        == "東京都杉並区高円寺北２丁目３−１３"
    assert bd_store.clean_address("〒104-0041 東京都中央区新富２丁目４−８") \
        == "東京都中央区新富２丁目４−８"
    assert bd_store.clean_address("  ") == ""


def test_norm_name_matches_flow_convention():
    from app.services.flow import _norm_name
    for s in ("ざくろ銀座店", "ざくろ 銀座店", "Ｗｉｎｅ　Ｓｈｏｐ"):
        assert bd_store.norm_name(s) == _norm_name(s)


# ---------------- 门店宇宙 ----------------

_SEQ = {"n": 0}


def _raw(db, sid, name, mod, who, orig):
    _SEQ["n"] += 1
    db.add(RawRecord(import_id=1, sheet_name="S", excel_row=_SEQ["n"],
                     store_id_raw=sid, store_name_local_raw=name,
                     modified_raw=mod, submitter_code=who,
                     original_row=orig))
    db.commit()


def test_build_stores_groups_by_store_key_and_is_idempotent(db):
    row = [None] * 30
    row[1] = "すしざんまい 巣鴨店"
    row[12] = "Dining"
    row[21] = "東京都豊島区巣鴨２丁目２−２ NY巣鴨ビル"
    _raw(db, "K1", "すしざんまい 巣鴨店", "2026-09-01 10:00:00", "P1", row)
    _raw(db, "K1", "すしざんまい 巣鴨店", "2026-09-20 11:00:00", "P1", row)
    _raw(db, "K2", "某店", "2026-09-05 09:00:00", "P2", ["x"] * 7)

    st = bd_store.build_stores(db)
    assert st["stores"] == 2
    assert st["with_address"] == 1

    k1 = db.query(BdStore).filter_by(store_key="K1").one()
    assert k1.visit_count == 2
    assert str(k1.first_seen_date) == "2026-09-01"
    assert str(k1.last_visit_date) == "2026-09-20"      # 冷却判定依据
    assert k1.name_norm == bd_store.norm_name("すしざんまい 巣鴨店")
    assert k1.gyotai == "Dining"

    # 幂等
    st2 = bd_store.build_stores(db)
    assert st2["inserted"] == 0 and st2["updated"] == 2
    assert db.query(BdStore).count() == 2


def test_zones_ready_shape(db):
    assert set(bd_store.zones_ready(db)) == {
        "stores", "with_address", "with_coord", "with_area_code"}


def test_p0_does_not_touch_settlement_tables():
    """硬边界回归：P0 只新增 bd_* 表，结算域四张表不得出现 bd 字段。"""
    settlement = ("formal_records", "person_daily_stats",
                  "month_perf_records", "payroll_period_rows")
    for t in settlement:
        cols = {c.name for c in Base.metadata.tables[t].columns}
        assert not any(c.startswith("bd_") for c in cols), t
    # bd_* 表必须存在
    for t in ("bd_area", "bd_store"):
        assert t in Base.metadata.tables
