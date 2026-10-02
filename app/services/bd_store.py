# -*- coding: utf-8 -*-
"""BD 作业域 · 门店宇宙（`bd_store`）。

**来源 = 既有 `raw_records`（只读）+ 其 `original_row` 原始行**，
把解析层当初丢掉的地理/业态列提取出来，落成作业域的门店主档。

设计 `docs/specs-bd-ops-layer.md` §1.3 / §5.3。

两条关键设计：
1. **`store_key` 取 `raw_records.store_id_raw`** —— 实测是稳定门店主键
   （一个 id → 一个店名，基本 1:1；格式 `010104709` + 注册日 + 序号）。
2. **地址列用「内容识别」而非固定下标** —— 实测源文件有 50 / 39 / 34 / 7
   四种列布局，按固定下标会取错列（§11.4，P0 第一号风险）。

⚠️ 本模块**只读** `raw_records`，不写结算域任何表。
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import date

from sqlalchemy.orm import Session

from app.models import BdStore

# 日本地址的形态特征（用于内容识别，不依赖列下标）
_ADDR_STRONG = re.compile(r"丁目")
_ADDR_WEAK = re.compile(r"(東京都|北海道|大阪府|京都府|.{2,3}県)")
_POSTAL = re.compile(r"〒\s*\d{3}-?\d{4}")


def norm_name(s: str) -> str:
    """店名归一化：NFKC + 去空白 + 小写（与 `flow._norm_name` 同口径）。"""
    t = unicodedata.normalize("NFKC", s or "")
    t = t.replace(" ", "").replace("\u3000", "").lower()
    return t.strip()


def clean_address(raw: str) -> str:
    """清洗地址：去掉 `日本、〒NNN-NNNN ` 前缀。"""
    t = str(raw or "").strip()
    t = re.sub(r"^日本[、,]\s*", "", t)
    t = re.sub(r"^〒\s*\d{3}-?\d{4}\s*", "", t)
    return t.strip()


def find_address(row) -> str:
    """在原始行里**按内容**找日文地址列（兼容 50/39/34/7 四种布局）。

    优先含「丁目」的值；否则取含都道府県名的值。
    """
    if not isinstance(row, (list, tuple)):
        return ""
    weak = ""
    for v in row:
        if v is None:
            continue
        s = str(v).strip()
        if len(s) < 6:
            continue
        if _ADDR_STRONG.search(s) and _ADDR_WEAK.search(s):
            return clean_address(s)
        if not weak and _ADDR_WEAK.search(s) and len(s) >= 8:
            weak = clean_address(s)
    return weak


def find_gyotai(row) -> str:
    """业态（如 Others / Dining / Recreational services）——从常见值里认。"""
    KNOWN = {"Others", "Dining", "Recreational services", "Retail",
             "Beauty", "Service", "Food", "Education", "Medical"}
    if not isinstance(row, (list, tuple)):
        return ""
    for v in row:
        s = str(v or "").strip()
        if s in KNOWN:
            return s
    return ""


def _d(s: str):
    """'YYYY-MM-DD ...' → date"""
    t = str(s or "")[:10]
    try:
        y, m, d = (int(x) for x in t.split("-"))
        return date(y, m, d)
    except Exception:  # noqa: BLE001
        return None


def build_stores(db: Session, limit: int | None = None,
                 batch: int = 500) -> dict:
    """从 `raw_records` 汇总出门店宇宙，upsert 进 `bd_store`。

    幂等：按 `store_key` upsert；`visit_count` / `last_visit_date` 每次重算。

    返回统计：{raw_rows, stores, with_address, inserted, updated}
    """
    from app.models import RawRecord

    q = db.query(RawRecord.store_id_raw, RawRecord.store_name_local_raw,
                 RawRecord.modified_raw, RawRecord.submitter_code,
                 RawRecord.original_row)
    if limit:
        q = q.limit(limit)

    agg: dict[str, dict] = {}
    raw_rows = 0
    for sid, name, mod, who, orig in q.yield_per(2000):
        raw_rows += 1
        key = str(sid or "").strip()
        if not key:
            continue
        try:
            row = json.loads(orig) if isinstance(orig, str) else orig
        except Exception:  # noqa: BLE001
            row = None
        d = _d(mod)
        rec = agg.get(key)
        if rec is None:
            rec = agg[key] = {
                "store_key": key, "name_raw": "", "name_norm": "",
                "address": "", "gyotai": "",
                "first_seen_person_code": who or None,
                "first_seen_date": d, "last_visit_date": d,
                "visit_count": 0,
            }
        rec["visit_count"] += 1
        nm = str(name or "").strip()
        if nm and not rec["name_raw"]:
            rec["name_raw"] = nm
            rec["name_norm"] = norm_name(nm)
        if not rec["address"]:
            a = find_address(row)
            if a:
                rec["address"] = a
        if not rec["gyotai"]:
            rec["gyotai"] = find_gyotai(row)
        if d:
            if not rec["first_seen_date"] or d < rec["first_seen_date"]:
                rec["first_seen_date"] = d
            if not rec["last_visit_date"] or d > rec["last_visit_date"]:
                rec["last_visit_date"] = d

    existing = {s.store_key: s for s in db.query(BdStore).all()}
    inserted = updated = 0
    for i, rec in enumerate(agg.values(), 1):
        row = existing.get(rec["store_key"])
        if row is None:
            db.add(BdStore(**rec))
            inserted += 1
        else:
            for k, v in rec.items():
                setattr(row, k, v)
            updated += 1
        if i % batch == 0:
            db.flush()
    db.commit()
    return {
        "raw_rows": raw_rows,
        "stores": len(agg),
        "with_address": sum(1 for r in agg.values() if r["address"]),
        "inserted": inserted,
        "updated": updated,
    }


def zones_ready(db: Session) -> dict:
    """门店宇宙的就绪度（供片区划分前自检）。"""
    total = db.query(BdStore).count()
    addr = db.query(BdStore).filter(BdStore.address != "").count()
    coord = db.query(BdStore).filter(BdStore.lat.isnot(None)).count()
    areas = db.query(BdStore).filter(BdStore.area_code.isnot(None)).count()
    return {"stores": total, "with_address": addr,
            "with_coord": coord, "with_area_code": areas}
