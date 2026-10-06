# -*- coding: utf-8 -*-
"""车站数据资产服务（物理车站层 `bd_station_place`）。

资产结构（2026-10-05 整理）：

    线路 bd_line ──1:N──> 站×线 bd_station ──N:1──> 物理车站 bd_station_place
                              └──1:1──> 任务 bd_task（现在挂在这一层）

- **物理车站** = 人真正走到的那个地方（1,568 个）；**站×线** = "某条线路上的这个站"（1,920 条）
- 分层键 = N02_005g 駅グループコード（官方给的同一车站分组，**不用猜**）
- 任务是"输入源"驱动的：现在来源 = 站×线；将来来源 = 片区（也由物理车站长出来）

本模块提供：建/修 place（`rebuild_places`）、资产统计（`asset_stats`）、
资产体检（`integrity_issues`，`scripts/check_station_asset.py` 用它）、显示用标签。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.models import BdLine, BdStation, BdStationPlace
from app.services import bd_lines, bd_tasks


def place_label(place: Optional[BdStationPlace]) -> str:
    """界面显示：`站名（N 条线）`，单线时不显示括号。"""
    if place is None:
        return ""
    n = place.n_line or 0
    return "%s（%d %s）" % (place.name, n, "线") if n > 1 else place.name


def rebuild_places(db: Session, dry: bool = False) -> dict:
    """按 `group_code` 重建物理车站层并回填 `bd_station.place_id`。

    **幂等**：每次按当前 `bd_station` 重算（分组键没变就不会动数据）。
    没有 `group_code` 的站（手工建的）→ 按 `(name_norm, pref)` 归组，group_code 留空。
    """
    stations = db.query(BdStation).order_by(BdStation.id.asc()).all()
    groups: Dict[str, List[BdStation]] = {}
    for st in stations:
        key = st.group_code or ("manual:%s|%s" % (st.name_norm, st.pref or ""))
        groups.setdefault(key, []).append(st)

    allp = db.query(BdStationPlace).all()
    have = {p.group_code: p for p in allp if p.group_code}
    # ⚠️ 手工站（没有 group_code）的复用键必须是**站名+县**，不能拿 place.id 去比 station.id
    #    （2026-10-05 踩到：键用错 → 每次重建都新建一个 place，旧的变孤儿）
    manual = {(p.name_norm, p.pref): p for p in allp if not p.group_code}
    rep = {"places": 0, "created": 0, "updated": 0, "linked": 0, "stations": len(stations)}

    lines = {l.id: l for l in db.query(BdLine).all()}
    for key, rows in groups.items():
        gc = rows[0].group_code or ""
        name = rows[0].name
        pref = rows[0].pref or ""
        lon = sum(r.lon for r in rows if r.lon is not None) / max(
            1, len([r for r in rows if r.lon is not None]))
        lat = sum(r.lat for r in rows if r.lat is not None) / max(
            1, len([r for r in rows if r.lat is not None]))
        line_ids = [r.line_id for r in rows if r.line_id]
        ops = sorted({(lines[i].operator_short or lines[i].operator)
                      for i in line_ids if i in lines})
        lns = sorted({lines[i].name for i in line_ids if i in lines})
        place = have.get(gc) if gc else None
        if place is None and not gc:
            place = manual.get((bd_tasks.norm_name(name), pref))   # 手工站按 站名+县 复用
        vals = dict(name=name, name_norm=bd_tasks.norm_name(name), pref=pref,
                    lon=(lon or None), lat=(lat or None), group_code=gc,
                    n_line=len(line_ids), n_operator=len(ops),
                    operators="、".join(ops), lines_text="、".join(lns),
                    source=("mlit" if gc else "manual"))
        if place is None:
            rep["created"] += 1
            if not dry:
                place = BdStationPlace(**vals)
                db.add(place)
                db.flush()
                if gc:
                    have[gc] = place
                else:
                    manual[(place.name_norm, place.pref)] = place
        else:
            rep["updated"] += 1
            if not dry:
                changed = any(getattr(place, k) != v for k, v in vals.items())
                if changed:
                    for k, v in vals.items():
                        setattr(place, k, v)
        rep["places"] += 1
        if not dry:
            for r in rows:
                if r.place_id != place.id:
                    r.place_id = place.id
                    rep["linked"] += 1
    if not dry:
        db.flush()
        # 清理孤儿 place（没有任何站×线指向它）—— 派生数据，重建即可再生
        from sqlalchemy import text as _text
        ids = [i for (i,) in db.query(BdStationPlace.id).all()
               if not db.query(BdStation.id).filter(
                   BdStation.place_id == BdStationPlace.id).first()
               and not db.query(BdStation.id).filter(
                   BdStation.place_id == BdStationPlace.id).count()]
        orphans = [i for (i,) in db.execute(_text(
            "SELECT p.id FROM bd_station_place p WHERE NOT EXISTS "
            "(SELECT 1 FROM bd_station s WHERE s.place_id = p.id)")).all()]
        for oid in orphans:
            db.delete(db.get(BdStationPlace, oid))
        rep["orphans_removed"] = len(orphans)
        db.flush()
    else:
        rep["orphans_removed"] = 0
    return rep


def asset_stats(db: Session) -> dict:
    """资产规模（页面统计卡 / 体检报告都用这一份）。"""
    n_place = db.query(BdStationPlace).count()
    n_station = db.query(BdStation).count()
    n_line = db.query(BdLine).count()
    multi = db.query(BdStationPlace).filter(BdStationPlace.n_line > 1).count()
    no_pref = db.query(BdStation).filter(BdStation.pref == "").count()
    no_geo = db.query(BdStation).filter(BdStation.lon.is_(None)).count()
    kg = db.query(BdLine.kind, func.count(BdLine.id)).group_by(BdLine.kind).all()
    pf = (db.query(BdStationPlace.pref, func.count(BdStationPlace.id))
          .group_by(BdStationPlace.pref).all())
    return {
        "n_place": n_place, "n_station": n_station, "n_line": n_line,
        "n_multi_line": multi, "n_no_pref": no_pref, "n_no_geo": no_geo,
        "avg_line_per_place": round(n_station / n_place, 2) if n_place else 0,
        "kinds": {k: v for k, v in kg}, "prefs": {k: v for k, v in pf},
        "pref_labels": dict(bd_lines.PREF_LABELS),
    }


def integrity_issues(db: Session) -> List[dict]:
    """资产体检：返回问题清单（空 = 健康）。`scripts/check_station_asset.py` 用它。"""
    out: List[dict] = []

    def add(kind: str, detail: str, n: int = 0):
        out.append({"kind": kind, "detail": detail, "n": n})

    # ① 每个站×线都要有线路、物理车站、坐标、县
    add("缺少线路", "bd_station.line_id 为空", db.query(BdStation)
        .filter(BdStation.line_id.is_(None)).count())
    add("缺少物理车站", "bd_station.place_id 为空", db.query(BdStation)
        .filter(BdStation.place_id.is_(None)).count())
    add("缺少县", "bd_station.pref 为空", db.query(BdStation)
        .filter(BdStation.pref == "").count())
    add("缺少坐标", "bd_station.lon 为空", db.query(BdStation)
        .filter(BdStation.lon.is_(None)).count())

    # ② 同组必须同名同县（N02 保证了，这里防我们自己写坏）
    dup = db.execute(text(
        "SELECT group_code, COUNT(DISTINCT name_norm) c FROM bd_station "
        "WHERE group_code != '' GROUP BY group_code HAVING c > 1")).all()
    add("同组站名不一致", "同一物理车站出现两种站名：%s" % (dup[:3],), len(dup))

    # ③ place.n_line 必须等于成员数（冗余字段不能漂）
    # n_line 的语义 = **不同线路数**（手工站可能 0）→ 对照 COUNT(DISTINCT line_id)
    bad_n = db.execute(text(
        "SELECT p.id, p.n_line, COUNT(DISTINCT s.line_id) c FROM bd_station_place p "
        "LEFT JOIN bd_station s ON s.place_id = p.id GROUP BY p.id "
        "HAVING p.n_line != COUNT(DISTINCT s.line_id)")).all()
    add("线路数与成员数不符", "bd_station_place.n_line 与不同线路数不一致：%s"
        % (bad_n[:3],), len(bad_n))

    # ④ 线路的 n_station 冗余也要对
    bad_line = db.execute(text(
        "SELECT l.id, l.n_station, COUNT(s.id) c FROM bd_line l "
        "LEFT JOIN bd_station s ON s.line_id = l.id GROUP BY l.id "
        "HAVING l.n_station != COUNT(s.id)")).all()
    add("线路站数与实际不符", "bd_line.n_station 与 bd_station 行数不一致：%s"
        % (bad_line[:3],), len(bad_line))

    # ⑤ 重复的 (线路, 站名)
    dup2 = db.execute(text(
        "SELECT line_id, name_norm, COUNT(*) c FROM bd_station "
        "GROUP BY line_id, name_norm HAVING c > 1")).all()
    add("同线重名", "(line_id, name_norm) 重复：%s" % (dup2[:3],), len(dup2))

    # ⑥ 孤儿 place（没有任何站指过来）
    orphan = db.execute(text(
        "SELECT COUNT(*) FROM bd_station_place p WHERE NOT EXISTS "
        "(SELECT 1 FROM bd_station s WHERE s.place_id = p.id)")).scalar()
    add("孤儿物理车站", "没有任何站×线指向它", orphan)

    return [i for i in out if i["n"]]
