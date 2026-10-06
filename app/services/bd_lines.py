# -*- coding: utf-8 -*-
"""线路主档服务（`bd_line`）。

数据来自 国土数値情報 N02（见 `scripts/bd_fetch_rail.py` / `bd_import_rail.py`）。
**线路显示一律用 `line_label()`**（`operator_short + name`）—— MLIT 的官方线路名单看会重名：
「本線」有京急/京成/西武/東武…，都営叫「12号線大江戸線」，直接显示会让人认不出是哪条线。
"""
from __future__ import annotations

import unicodedata
from typing import Iterable, List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import BdLine, BdStation

#: JIS 都道府県码（一都三県）
PREF_LABELS = {"13": "東京都", "11": "埼玉県", "12": "千葉県", "14": "神奈川県"}

#: 运营公司简称（MLIT 全名 → 界面上好认的短名）。没收录的用全名。
OPERATOR_SHORT = {
    "東日本旅客鉄道": "JR東日本",
    "東海旅客鉄道": "JR東海",
    "東京地下鉄": "東京メトロ",
    "東京都": "都営",
    "京浜急行電鉄": "京急",
    "京王電鉄": "京王",
    "小田急電鉄": "小田急",
    "東急電鉄": "東急",
    "西武鉄道": "西武",
    "東武鉄道": "東武",
    "京成電鉄": "京成",
    "相模鉄道": "相鉄",
    "新京成電鉄": "新京成",
    "横浜市": "横浜市営",
    "埼玉高速鉄道": "埼玉高速",
    "首都圏新都市鉄道": "つくばEX",
    "北総鉄道": "北総",
    "東葉高速鉄道": "東葉高速",
    "ゆりかもめ": "ゆりかもめ",
    "東京モノレール": "東京モノレール",
    "多摩都市モノレール": "多摩モノレール",
    "千葉都市モノレール": "千葉モノレール",
    "湘南モノレール": "湘南モノレール",
    "横浜シーサイドライン": "シーサイドライン",
    "埼玉新都市交通": "ニューシャトル",
    "舞浜リゾートライン": "ディズニーライン",
    "山万": "山万",
    "秩父鉄道": "秩父鉄道",
    "箱根登山鉄道": "箱根登山",
    "御岳登山鉄道": "御岳登山",
    "高尾登山電鉄": "高尾登山",
    "大山観光電鉄": "大山観光",
    "十国峠": "十国峠",
    "江ノ島電鉄": "江ノ電",
    "銚子電気鉄道": "銚子電鉄",
    "関東鉄道": "関東鉄道",
    "流鉄": "流鉄",
    "小湊鐵道": "小湊",
    "いすみ鉄道": "いすみ",
}

#: 线路类型粗分类 → 界面标签（数值来自 N02_001 × N02_002，实测归纳）
KIND_LABELS = {
    "jr": "JR在来線", "shinkansen": "新幹線", "private": "私鉄",
    "public": "公営", "third": "第三セクター", "monorail": "モノレール",
    "agt": "新交通", "tram": "路面電車", "cable": "鋼索鉄道",
}

#: 线路名别名（老数据 / 俗称 → N02 官方名）。用于把历史 `bd_station.line` 对到线路主档。
LINE_ALIAS = {
    "東京さくらトラム": "荒川線",
    "さくらトラム": "荒川線",
    "都電荒川線": "荒川線",
    "多摩モノレール": "多摩都市モノレール線",
    "多摩都市モノレール": "多摩都市モノレール線",
    "ゆりかもめ": "東京臨海新交通臨海線",
    "りんかい線": "臨海副都心線",
    "東京メトロ": "",
    "京急本線": "本線",
    "京成本線": "本線",
    "西武新宿線": "新宿線",
    "京王新宿線": "新宿線",
    "中央線沿線": "中央線",
    "東急東横線": "東横線",
}


def norm_line(s: Optional[str]) -> str:
    """线路名归一（比对用）：NFKC + 去空白 + 去掉「(区间)」「沿線」等修饰。"""
    t = unicodedata.normalize("NFKC", str(s or "")).strip()
    for junk in ("沿線", "沿線(東京~高尾)"):
        t = t.replace(junk, "")
    # 去括号区间：南武線(川崎~立川) → 南武線
    for op, cl in (("(", ")"), ("（", "）")):
        while op in t and cl in t:
            i, j = t.find(op), t.find(cl)
            if i > j:
                break
            t = (t[:i] + t[j + 1:]).strip()
    t = "".join(t.split())
    t = LINE_ALIAS.get(t, t)
    return t


def operator_short(op: str) -> str:
    return OPERATOR_SHORT.get(op or "", op or "")


def line_label(line: Optional[BdLine]) -> str:
    """界面显示用：`运营商简称 线路名`（本線 → 京急 本線）。"""
    if line is None:
        return ""
    return "%s %s" % (operator_short(line.operator), line.name)


def list_lines(db: Session, kw: str = "", kind: str = "",
               pref: str = "") -> List[BdLine]:
    q = db.query(BdLine)
    if kw:
        like = "%%%s%%" % kw.strip()
        q = q.filter(or_(BdLine.name.like(like), BdLine.operator.like(like),
                         BdLine.operator_short.like(like)))
    if kind:
        q = q.filter(BdLine.kind == kind)
    if pref:
        q = q.filter(BdLine.prefs.like("%%%s%%" % pref))
    return q.order_by(BdLine.operator.asc(), BdLine.name.asc()).all()


def kind_order(lines: Iterable[BdLine]) -> List[str]:
    """下拉里线路的排序（JR → 地下鉄 → 私鉄 → 新交通…，跟用户认知一致）。"""
    keys = ["jr", "shinkansen", "public", "private", "third", "monorail",
            "agt", "tram", "cable"]
    have = {l.kind for l in lines}
    return [k for k in keys if k in have] + sorted(have - set(keys))


def line_options(db: Session, pref: str = "") -> List[BdLine]:
    """线路下拉（按运营商 + 线路名排序）。"""
    return list_lines(db, pref=pref)


def station_counts(db: Session) -> dict:
    """按线路统计车站数（列表/下拉里显示）。"""
    from sqlalchemy import func
    rows = (db.query(BdStation.line_id, func.count(BdStation.id))
            .filter(BdStation.line_id.isnot(None)).group_by(BdStation.line_id).all())
    return {lid: n for lid, n in rows}
