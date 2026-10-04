# -*- coding: utf-8 -*-
"""把 OSM 的「运行系统线路 → 有序站列表」落到 `bd_station.seq / along_km / seq_src`。

匹配方式 = **按坐标重叠**（不靠线路名/站名，避免"同名异地"和命名差异）：
1. 对每条我们的线路（`bd_line`）：拿它所有车站的坐标，去每条 OSM 线路里数"有多少站在 300m 内"；
2. 覆盖率最高、且达到门槛的那条 OSM 线路 = 这条线的顺序来源；
3. 逐个车站取它在 OSM 有序列表里的位置 → `seq`（1 起）；`along_km` = 从该线起点累加的里程。

⚠️ 为什么 seq 可能不连续：OSM 是**运行系统口径**（山手線 = 30 站一圈），
   N02 是**官方口径**（山手环被拆成 山手線17 + 東北線 + 東海道線）→ 同一条 N02 线路内的
   seq 可能有跳号，但**相对顺序是对的**（排序只看相对大小）。

用法：
    ./.venv/bin/python scripts/bd_fill_seq.py            # 试算（默认）
    ./.venv/bin/python scripts/bd_fill_seq.py --apply    # 落库
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
SRC = os.path.join(ROOT, "scripts", "bd_osm_routes.json")
#: 几何兜底（`bd_seq_from_geometry.py` 产出；只覆盖 OSM 拿不到顺序的线路）
SRC_GEOM = os.path.join(ROOT, "scripts", "bd_line_seq_geom.json")
NEAR_M = 300.0          # 车站与 OSM 站点的匹配阈值
MIN_RATIO = 0.75        # 召回门槛：本线车站要有 75% 能在该 OSM 线路里找到
MIN_F1 = 0.70           # 精确+召回的综合门槛（防"别的线的班次"误配）


def hav(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(x))


def load_routes():
    d = json.load(open(SRC, encoding="utf-8"))
    rs = [r for r in d["routes"] if r["n_stop"] >= 2]
    # 每条路线预计算里程（从首站累加）
    for r in rs:
        acc, prev = 0.0, None
        for s in r["stops"]:
            if prev is not None:
                acc += hav(prev[0], prev[1], s["lat"], s["lon"])
            s["km"] = acc / 1000.0
            prev = (s["lat"], s["lon"])
    return d, rs


def _norm_station(name: str) -> str:
    """站名归一（NFKC + 去空白 + 去"駅" + ケ/ヶ 统一），用于同名兜底。"""
    import re
    import unicodedata
    t = unicodedata.normalize("NFKC", name or "").strip()
    t = re.sub(r"\s+", "", t).replace("駅", "")
    return t.replace("ケ", "ヶ")


def _core(name: str) -> str:
    """取线路名核心词（用于"名字是否相关"判断，只服务子集/部分档）。

    ⚠️ 不要用 `.*→.*` 这类贪婪式：实测它会把 `都営大江戸線 : 都庁前→光が丘`
    整串吃掉 → 核心词变空 → 所有线路都被"名字不相关"挡掉。
    """
    import re
    t = re.sub(r"[（(].*?[)）]", "", name or "").strip()
    t = re.sub(r"\s*[:：].*$", "", t)                  # 去掉 " : 都庁前→光が丘"
    t = re.sub(r"\s*[-–—].*$", "", t)                  # 去掉 " - 東急田園都市線直通運転"
    t = re.sub(r"\s*(各駅停車|各停|快速|急行|特急|下り|上り|北行|南行|直通運転|直通)$", "", t)
    t = re.sub(r"^(JR|都営|東京メトロ|東京地下鉄)\s*", "", t)
    return re.sub(r"^\d+号線", "", t).strip()


def best_route_for_line(stations, routes, near=None):
    """给一条我们的线路挑顺序来源线路。三档（都必须"名字相关"，否则宁可不给）：

    ① **F1 强匹配**（F1 ≥ MIN_F1）：基本是同一批站 —— 绝大多数线路走这一档
    ② **子集匹配**（召回 ≥ 0.85 且名字相关）：**我们是 OSM 那条线的一段** —— 典型是
       `山手線`：N02 官方只有西北弧 17 站，OSM 是整圈 30 站（召回 0.88 / 精确 0.50，
       F1 只有 0.64 会被①挡掉）。赋值时 seq = 在整圈里的位置（可以不连续，但顺序对）
    ③ **部分匹配**（召回 ≥ 0.5 且名字相关）：只能给一部分站排序 —— 记为 weak，如实报告
    """
    import re
    near = near or NEAR_M
    A = [st for st in stations if st["lat"] is not None]
    if not A:
        return None
    core = _core(stations[0].get("line_name", "") or "")
    cands = []
    for r in routes:
        B = r["stops"]
        if len(B) < 2:
            continue
        hit_a = sum(1 for st in A if min(
            hav(st["lat"], st["lon"], s["lat"], s["lon"]) for s in B) <= near)
        hit_b = sum(1 for s in B if min(
            hav(s["lat"], s["lon"], st["lat"], st["lon"]) for st in A) <= near)
        recall = hit_a / float(len(A))
        precision = hit_b / float(len(B))
        if recall < 0.5 or (len(A) <= 3 and hit_a < len(A)):
            continue
        f1 = 0.0 if (recall + precision) == 0 else 2 * recall * precision / (recall + precision)
        name_rel = len(core) >= 3 and core in _core(r["name"])
        if len(B) > 3 * len(A) + 10:
            continue                       # 长得离谱的不认
        if f1 >= MIN_F1:
            tier = 1                       # ① 站点高度重合，不必看名字（F1 已经是强证据）
        elif name_rel and recall >= 0.85:
            tier = 2                       # ② 我们是它的一段（山手線型）
        elif name_rel and recall >= 0.5:
            tier = 3                       # ③ 只能对上一部分
        else:
            continue
        # 排序键：档位 → **覆盖我们站数（越多越好）** → F1 → 站数接近 → 名字稳定
        cands.append((tier, -hit_a, -round(f1, 4), abs(len(B) - len(A)),
                      r["name"] or "", r, hit_a, len(A), f1, precision))
    if not cands:
        return None
    best = min(cands, key=lambda z: (z[0], z[1], z[2], z[3], z[4]))
    return (best[0], best[5], best[6], best[7], best[8], best[9])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--near", type=float, default=NEAR_M)
    args = ap.parse_args()

    from sqlalchemy import text

    from app.db import SessionLocal

    meta, routes = load_routes()
    geom = {}
    if os.path.exists(SRC_GEOM):
        for L in json.load(open(SRC_GEOM, encoding="utf-8"))["lines"]:
            geom[L["line_id"]] = L
        print("几何兜底可用: %d 条线路（%s）" % (len(geom), os.path.basename(SRC_GEOM)))
    print("顺序源: %s（%d 条线路 / %d 个站次）" % (meta.get("source"), len(routes),
          sum(r["n_stop"] for r in routes)))
    db = SessionLocal()
    lines = db.execute(text(
        "SELECT id, operator_short, name FROM bd_line ORDER BY id")).all()
    ok, weak, fail = [], [], []
    plan = {}          # station_id -> (seq, along_km, src)
    for lid, op, nm in lines:
        sts = [{"id": r[0], "name": r[1], "lat": r[2], "lon": r[3], "line_name": nm}
               for r in db.execute(text(
                   "SELECT id, name, lat, lon FROM bd_station WHERE line_id=:i"),
                   {"i": lid}).all()]
        pick = best_route_for_line(sts, routes)
        # OSM 没匹配上、或只覆盖不到一半 → 用**几何兜底**（不混用两个源，否则 seq 不可比）
        if (not pick or pick[2] < 0.5 * len(sts)) and lid in geom:
            g = geom[lid]
            for x in g["stops"]:
                plan[x["id"]] = (x["seq"], x["km"], "geom:N02")
            ok.append((nm, "几何法(投影误差%.0fm)" % g["max_proj_m"], len(g["stops"]),
                       len(g["stops"])))
            continue
        if not pick:
            fail.append("%s %s" % (op, nm))
            continue
        tier, r, hit, n, f1, prec = pick
        src = "osm:%s" % (r["name"] or ("rel%d" % r["rel_id"]))
        got = 0
        for st in sts:
            if st["lat"] is None:
                continue
            cand = min(r["stops"], key=lambda s: hav(st["lat"], st["lon"], s["lat"], s["lon"]))
            d = hav(st["lat"], st["lon"], cand["lat"], cand["lon"])
            if d > args.near:
                # 同名兜底：坐标差得多但站名一致，且不超过 2km（防同名异地）
                same = [s for s in r["stops"]
                        if _norm_station(s.get("name")) == _norm_station(st["name"])]
                if not same:
                    continue
                c2 = min(same, key=lambda s: hav(st["lat"], st["lon"], s["lat"], s["lon"]))
                if hav(st["lat"], st["lon"], c2["lat"], c2["lon"]) > 2000:
                    continue
                cand = c2
            plan[st["id"]] = (r["stops"].index(cand) + 1, round(cand["km"], 3), src)
            got += 1
        if got == n and tier == 1:
            ok.append((nm, r["name"], n, r["n_stop"]))
        else:
            weak.append((nm, r["name"], got, n, tier))
    print("\n=== 结果 ===")
    print("① 整条线全部站都取到顺序: %d 条" % len(ok))
    print("② 部分取到: %d 条" % len(weak))
    for nm, rn, got, n, tier in weak[:14]:
        print("      %-20s %d/%d 站  [第%d档] ← %s" % (nm, got, n, tier, rn))
    print("③ 完全对不上（没顺序）: %d 条" % len(fail))
    print("      %s" % ("、".join(fail[:20]) or "无"))
    print("\n将写入 %d 个车站的 seq（占全部车站 %.0f%%）"
          % (len(plan), 100.0 * len(plan) / max(
              db.execute(text("SELECT COUNT(*) FROM bd_station")).scalar(), 1)))
    if not args.apply:
        print("（dry-run；加 --apply 才写）")
        db.close()
        return 0
    for sid, (seq, km, src) in plan.items():
        db.execute(text("UPDATE bd_station SET seq=:s, along_km=:k, seq_src=:src WHERE id=:i"),
                   {"s": seq, "k": km, "src": src, "i": sid})
    db.execute(text("UPDATE bd_station SET seq=NULL, along_km=NULL, seq_src='' "
                    "WHERE id NOT IN (%s)"
                    % ",".join(str(i) for i in plan) if plan else
                    "UPDATE bd_station SET seq=NULL, along_km=NULL, seq_src=''"))
    db.commit()
    print("已落库。")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
