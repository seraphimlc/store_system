# -*- coding: utf-8 -*-
"""导入一都三県「线路 + 车站」并**自动为每个车站建任务**（车站即任务）。

用户口径（2026-10-05）：
- 「把一都三县所有的地铁线和车站都收集进来」「每个车站都是一个任务，自动创建就行」
- 「不用强调车站和任务的区别，车站即任务」→ 界面就是一个任务列表
- 「但后台存储肯定是要分开的」→ `bd_station`（来源）+ `bd_task`（任务）分开，`source_type` 判别
- 「同名车站…先分哪个线的就按哪个线的来，后面再定规则」→ 唯一键 `(line_id, name_norm)`

⚠️ **不能重复建任务**：现有 515 个车站（来自 `万总/team_task/*.xlsx`）已经带着队伍与担当，
必须尽量**认领**它们（enrich 而不是新插一行）。认领顺序：
1. `(line_id, name_norm)` 已存在 → 直接用
2. 同名车站（`name_norm` 相同）且 `line_id IS NULL` 的历史行：
   - 只有一个 → 认领
   - 多个 → 用历史 `line` 字段归一后跟候选线路名比对，唯一命中才认领；否则**不认领**（记账报告）
3. 其余 → 新插一行（并自动建任务）

数据源：`scripts/bd_kanto_rail.json`（由 `scripts/bd_fetch_rail.py` 从 国土数値情報 N02 生成）。
"""
from __future__ import annotations

import io
import json
import os
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models import BdLine, BdStation, BdTask
from app.services import bd_lines, bd_log, bd_tasks

#: 数据文件（随仓库走；运行时不需要联网）
DEFAULT_JSON = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "scripts", "bd_kanto_rail.json")


def load_payload(path: Optional[str] = None) -> dict:
    with io.open(path or DEFAULT_JSON, encoding="utf-8") as f:
        return json.load(f)


def _line_key(operator: str, name: str) -> Tuple[str, str]:
    return (operator or "", name or "")


def import_rail(db: Session, payload: Optional[dict] = None, dry: bool = False,
                actor: str = "import:rail", make_tasks: bool = True) -> dict:
    """导入线路 + 车站（并给每个车站建任务）。`dry=True` 只算不写。

    ⚠️ dry-run 必须**按真实逻辑预演**：线路用临时负 id 建立索引、认领过的历史行
    立刻从候选里摘掉 —— 否则"是否有线路 id"会改变匹配结果，试算与落库不一致
    （2026-10-05 第一版就踩了：试算显示"认领 0 个"，实际会认领 500 个）。

    返回报告：lines_created / lines_existing / stations_new / stations_adopted /
    stations_existing / tasks_created / unmatched（同名多候选认领不了的历史站）
    """
    data = payload or load_payload()
    rep = {"lines_created": 0, "lines_existing": 0,
           "stations_new": 0, "stations_adopted": 0, "stations_existing": 0,
           "tasks_created": 0, "unmatched": [], "prefs": {}, "renorm": 0}

    # ---------- ① 线路（dry-run 用临时负 id，保证后续匹配与真实一致） ----------
    lines: Dict[Tuple[str, str], BdLine] = {
        (l.operator, l.name): l for l in db.query(BdLine).all()}
    tmp_id = 0
    for item in data["lines"]:
        key = _line_key(item["operator"], item["name"])
        row = lines.get(key)
        prefs = ",".join(item.get("prefs") or [])
        if row is None:
            rep["lines_created"] += 1
            row = BdLine(name=item["name"],
                         name_norm=bd_lines.norm_line(item["name"]),
                         operator=item["operator"],
                         operator_short=bd_lines.operator_short(item["operator"]),
                         kind=item.get("kind", ""), prefs=prefs,
                         n_station=item.get("n", 0), source="mlit")
            if dry:
                # ⚠️ dry-run 才给临时负 id（让后续匹配逻辑与真实一致）；
                # 真实导入必须让数据库自己发号 —— 否则线上会存进负数 id（2026-10-05 踩过）
                tmp_id -= 1
                row.id = tmp_id
            else:
                db.add(row)
                db.flush()
            lines[key] = row
        else:
            rep["lines_existing"] += 1
            if not dry and (row.n_station != item.get("n", 0)
                            or row.prefs != prefs or not row.operator_short):
                row.operator_short = bd_lines.operator_short(item["operator"])
                row.kind = item.get("kind", "") or row.kind
                row.prefs = prefs
                row.n_station = item.get("n", 0)

    # ---------- ② 现有车站索引 ----------
    claimed: set = set()                       # (line_id, name_norm) 已占
    by_name: Dict[str, List[BdStation]] = {}   # 历史无线路的行（候选认领池）
    adopted_ids: set = set()
    # ⚠️ 老行的 name_norm 可能是**旧口径**写的（如 ケ 没统一成 ヶ：库里"箱根ケ崎"、
    # 新口径是"箱根ヶ崎"）→ 不改写就会"同名却认领不到"而重复插一行（2026-10-05 实测 3 个）。
    holders = {st.name_norm for st in db.query(BdStation).all() if st.line_id is None}
    for st in db.query(BdStation).all():
        key = bd_tasks.norm_name(st.name)
        if key != st.name_norm and st.line_id is None and key not in holders:
            rep["renorm"] += 1
            if not dry:
                st.name_norm = key
            holders.discard(st.name_norm)
            holders.add(key)
        else:
            key = st.name_norm
        claimed.add((st.line_id, key))
        if st.line_id is None:
            by_name.setdefault(key, []).append(st)

    # ---------- ③ 车站 ----------
    for item in data["stations"]:
        line = lines.get(_line_key(item["operator"], item["line"]))
        line_id = line.id if line else None
        name_norm = bd_tasks.norm_name(item["name"])
        rep["prefs"][item["pref"]] = rep["prefs"].get(item["pref"], 0) + 1
        want_line = bd_lines.norm_line(line.name if line else "")

        if (line_id, name_norm) in claimed:
            st = db.query(BdStation).filter(
                BdStation.name_norm == name_norm,
                BdStation.line_id == line_id).first() if not dry else None
            if st is not None and st.source != "mlit":
                if not dry:
                    _enrich(st, item, line)      # 历史行补齐 MLIT 属性
            rep["stations_existing"] += 1
            continue

        # 历史行认领：同名 + 没线路（多候选时用**各自**的 line 串比对）
        cands = [x for x in by_name.get(name_norm, [])
                 if x.id not in adopted_ids and x.line_id is None]
        adopted = None
        if len(cands) == 1:
            adopted = cands[0]
        elif len(cands) > 1:
            hit = [x for x in cands
                   if want_line and bd_lines.norm_line(x.line) == want_line]
            adopted = hit[0] if len(hit) == 1 else None
            if adopted is None:
                rep["unmatched"].append({
                    "name": item["name"], "line": item["line"],
                    "operator": item["operator"],
                    "legacy": [(x.id, x.line) for x in cands]})

        if adopted is not None:
            rep["stations_adopted"] += 1
            adopted_ids.add(adopted.id)
            claimed.add((line_id, name_norm))
            if not dry:
                _enrich(adopted, item, line)
            continue

        rep["stations_new"] += 1
        claimed.add((line_id, name_norm))
        if not dry:
            st = BdStation(name=item["name"], name_norm=name_norm,
                           line=(item["line"] or ""), line_id=line_id,
                           operator=item["operator"], pref=item["pref"],
                           lon=item.get("lon"), lat=item.get("lat"),
                           ekicode=item.get("code", ""),
                           group_code=item.get("group", ""), source="mlit")
            db.add(st)
            db.flush()
            bd_log.log(db, "station", "create", ref_id=st.id, ref_label=st.name,
                       field="车站", new=st.name, actor=actor, actor_name=actor)

    if not dry:
        db.flush()

    # ---------- ④ 每个车站自动建任务（车站即任务） ----------
    if make_tasks:
        have = {sid for (sid,) in db.query(BdTask.station_id).all()}
        if dry:
            # 预演：现有任务 + 本次新增车站 - 认领（认领的行已经有任务了）
            rep["tasks_created"] = rep["stations_new"]
        else:
            todo = [s.id for s in db.query(BdStation).all() if s.id not in have]
            if todo:
                r = bd_tasks.create_tasks(db, todo, by=actor)
                rep["tasks_created"] = r.get("created", len(todo))
    if not dry:
        db.flush()
    return rep


def _legacy_line_of(cands: List[BdStation]) -> str:
    """多候选时用来比对的线路串：取候选里非空的那个（历史行是同一个站名多行）。"""
    for s in cands:
        if (s.line or "").strip():
            return s.line
    return ""


def _enrich(st: BdStation, item: dict, line: Optional[BdLine]) -> None:
    """把 MLIT 属性补到已有车站行上（**不动**其已有的站名/备注/状态/任务）。"""
    if line is not None:
        st.line_id = line.id
        if not (st.line or "").strip():
            st.line = line.name
    st.operator = item["operator"] or st.operator
    st.pref = item["pref"] or st.pref
    if item.get("lon") is not None:
        st.lon = item["lon"]
        st.lat = item["lat"]
    st.ekicode = item.get("code", "") or st.ekicode
    st.group_code = item.get("group", "") or st.group_code
