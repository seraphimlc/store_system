# -*- coding: utf-8 -*-
"""BD 作业域 · 行政区划基底（`bd_area`）。

数据源：**総務省「全国地方公共団体コード」**（官方 Excel，免费）
  https://www.soumu.go.jp/denshijiti/code.html

设计 `docs/specs-bd-ops-layer.md` §3.3 / §5.1：
片区（bd_zone）由**丁目集合**构成，所以行政区划是作业域的地基。
**只读同步，不自造坐标系。**

层级与编码：
```
pref  都道府県   code = 2 位（如 13 = 東京都）
city  市区町村   code = 6 位（全国地方公共団体コード，如 13113 = 渋谷区）
ward  政令市の区  code = 6 位（横浜市中区等；来自第 2 个工作表）
town  町丁目     code = 11 位（町丁目コード；**本模块暂不装载**，见下）
```

> **关于町丁目（town）**：総務省 code 表只到市区町村级。丁目名单有两个来源：
> ① e-Stat 小地域境界数据（需下载 shapefile，较重）；
> ② **靠门店地址反查累积**（国土地理院 逆地理コーダ直接返回 `muniCd + lv01Nm`＝丁目名，
>    免费、已实测可用）——符合本方案"回流累积"的基调。
> 本模块先做 ①②的前置（pref/city/ward），town 层等门店坐标到位后**增量累积**。
"""
from __future__ import annotations

import re
from typing import Iterable

from sqlalchemy.orm import Session

from app.models import BdArea

# 一都三県（本方案的作业范围）
SCOPE_PREF_NAMES = ("東京都", "埼玉県", "千葉県", "神奈川県")

_PREF_CODE_RE = re.compile(r"^\d{2}$")
_CITY_CODE_RE = re.compile(r"^\d{6}$")


def _norm_kana(s) -> str:
    """全角/半角假名统一成半角（官方表用半角片假名）。"""
    import unicodedata
    t = unicodedata.normalize("NFKC", str(s or "")).strip()
    return t


def parse_soumu_xlsx(path: str,
                     pref_names: Iterable[str] = SCOPE_PREF_NAMES) -> list[dict]:
    """解析総務省「全国地方公共団体コード」Excel → 待落库的行。

    返回每项：{level, code, name, name_kana, parent_code, pref_code, city_code}

    - 第 1 个工作表：都道府県 + 市区町村（`市区町村名（漢字）` 为空 = 都道府県行）
    - 第 2 个工作表：政令指定都市の区（若存在）
    """
    import openpyxl

    want = set(pref_names)
    out: list[dict] = []
    seen: set[tuple] = set()

    def _add(level, code, name, kana, parent, pref, city):
        code = str(code or "").strip()
        name = str(name or "").strip()
        if not code or not name:
            return
        key = (level, code)
        if key in seen:
            return
        seen.add(key)
        out.append(dict(level=level, code=code, name=name,
                        name_kana=_norm_kana(kana), parent_code=parent,
                        pref_code=pref, city_code=city))

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    for row in ws.iter_rows(min_row=2, max_col=5, values_only=True):
        code, pref_kanji, city_kanji, pref_kana, city_kana = (list(row) + [None] * 5)[:5]
        pref_kanji = str(pref_kanji or "").strip()
        if pref_kanji not in want:
            continue
        code = str(code or "").strip()
        if not _CITY_CODE_RE.match(code):
            continue
        pref_code = code[:2]
        city_kanji = str(city_kanji or "").strip()
        if not city_kanji:
            # 都道府県行
            _add("pref", pref_code, pref_kanji, pref_kana, None,
                 pref_code, "")
            continue
        # 都道府県行（先补，保证存在）
        _add("pref", pref_code, pref_kanji, pref_kana, None, pref_code, "")
        _add("city", code, city_kanji, city_kana, pref_code,
             pref_code, code)

    # 第 2 表：政令指定都市の区
    # ⚠️ 该表**每个政令市自己也占一行**（如 `111007 さいたま市`），必须跳过；
    #    区的 `市区町村名` 是**全名**（如 `さいたま市西区`），父级靠「市级名前缀」反推。
    if len(wb.worksheets) > 1:
        city_by_name = {r["name"]: r for r in out if r["level"] == "city"}
        ws2 = wb.worksheets[1]
        for row in ws2.iter_rows(min_row=2, max_col=6, values_only=True):
            vals = [("" if v is None else str(v).strip()) for v in
                    (list(row) + [None] * 6)[:6]]
            code = next((v for v in vals if _CITY_CODE_RE.match(v)), "")
            if not code:
                continue
            full = next((v for v in vals
                         if v not in want and not _CITY_CODE_RE.match(v)
                         and len(v) > 2), "")
            if not full:
                continue
            parent = None
            for cname, crow in city_by_name.items():
                if full == cname:
                    parent = None          # 这一行就是市本身 → 跳过
                    break
                if full.startswith(cname) and crow["pref_code"] == code[:2]:
                    if parent is None or len(cname) > len(parent["name"]):
                        parent = crow
            if parent is None:
                continue
            short = full[len(parent["name"]):] or full
            _add("ward", code, short, "", parent["code"],
                 parent["pref_code"], parent["code"])
    wb.close()
    return out


def sync_areas(db: Session, path: str,
               pref_names: Iterable[str] = SCOPE_PREF_NAMES) -> dict:
    """把官方清单同步进 `bd_area`（幂等：按 (level, code) upsert）。

    返回 {"total": n, "pref": n, "city": n, "ward": n, "updated": n}
    """
    rows = parse_soumu_xlsx(path, pref_names)
    existing = {(a.level, a.code): a for a in db.query(BdArea).all()}
    added = updated = 0
    for r in rows:
        key = (r["level"], r["code"])
        row = existing.get(key)
        if row is None:
            db.add(BdArea(**r))
            added += 1
        else:
            row.name = r["name"]
            row.name_kana = r["name_kana"]
            row.parent_code = r["parent_code"]
            row.pref_code = r["pref_code"]
            row.city_code = r["city_code"]
            updated += 1
    db.commit()
    stat = {"total": len(rows), "added": added, "updated": updated}
    for lv in ("pref", "city", "ward", "town"):
        stat[lv] = sum(1 for r in rows if r["level"] == lv)
    return stat


# ---------------- 查询辅助 ----------------

def pref_of(db: Session, pref_name: str):
    return (db.query(BdArea)
            .filter(BdArea.level == "pref", BdArea.name == pref_name)
            .first())


def cities_of(db: Session, pref_code: str):
    return (db.query(BdArea)
            .filter(BdArea.level == "city", BdArea.pref_code == pref_code)
            .order_by(BdArea.code).all())


def upsert_town(db: Session, code: str, name: str, city_code: str,
                pref_code: str) -> BdArea:
    """按需写入一个町丁目（**增量累积**，供反地理编码回填时调用）。

    町丁目 code（11 位）由国土地理院 逆地理コーダ 的 `muniCd` +
    丁目序号拼出；拿不到 code 时可用 `city_code + name` 的哈希兜底。
    """
    row = (db.query(BdArea)
           .filter(BdArea.level == "town", BdArea.code == code).first())
    if row is None:
        row = BdArea(level="town", code=code, name=name,
                     parent_code=city_code, pref_code=pref_code,
                     city_code=city_code)
        db.add(row)
    else:
        row.name = name
    return row
