# -*- coding: utf-8 -*-
"""几何法兜底求沿線顺序：把 N02 的**区间几何**串成一条线，再把车站投影上去。

**只用于 OSM 拿不到顺序的线路**（用户 2026-10-05："补上也行…不过别太勉强"）。
主源仍是 OSM（`bd_fill_seq.py`），这里产出 `scripts/bd_line_seq_geom.json` 作为兜底：
记录 `seq` / `along_km` / 质量指标（投影误差、是否闭环、有无分叉），
**算不干净的线路宁可不产出**（由 QUALITY 门槛决定）。

输入是 N02 的 `N02-22_RailroadSection.geojson`（几何），不是随仓库走的文件 → 需要时重新取：
    ./.venv/bin/python scripts/bd_fetch_rail.py            # 下载 N02-22 并解压到 /tmp/n02
默认路径 `/tmp/n02/gml/UTF-8/N02-22_RailroadSection.geojson`，可用 `--geojson` 指定。

用法：
    ./.venv/bin/python scripts/bd_seq_from_geometry.py --apply     # 只算"缺顺序"的线路
    ./.venv/bin/python scripts/bd_seq_from_geometry.py --apply --all
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "scripts", "bd_line_seq_geom.json")
DEF_GEOJSON = "/tmp/n02/gml/UTF-8/N02-22_RailroadSection.geojson"

# 质量门槛（"别太勉强"）：不达标就不给这条线出顺序
MAX_PROJ_M = 250.0        # 车站到轨道的最大投影误差
MIN_SECTION_RATIO = 0.9   # 串起来的区间段数占该线总段数的比例
MIN_COVER_RELAX = 0.5     # 串不全时放宽到这条线：但要求**每个站都贴得上**（否则仍放弃）


def hav(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(x))


def dist_xy(a, b, lat):
    """平面近似（够用：区间尺度小）。"""
    dx = (a[0] - b[0]) * 111320.0 * math.cos(math.radians(lat))
    dy = (a[1] - b[1]) * 110540.0
    return math.hypot(dx, dy)


def proj_to_seg(p, a, b, lat):
    """p 到线段 ab 的最近点：返回 (t, 距离m, 最近点)。"""
    kx = (b[0] - a[0]) * 111320.0 * math.cos(math.radians(lat))
    ky = (b[1] - a[1]) * 110540.0
    dx = (p[0] - a[0]) * 111320.0 * math.cos(math.radians(lat))
    dy = (p[1] - a[1]) * 110540.0
    L2 = kx * kx + ky * ky
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, (dx * kx + dy * ky) / L2))
    c = (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
    return t, dist_xy(p, c, lat), c


def build_path(secs, prefer=None):
    """把区间段串成一条有序路径（返回 (路径点列, 覆盖段数, 是否闭环, 有无分叉)）。

    - 有端点（度 1）的线：从端点走，岔路口**尽量走直**（方向点积最大）
    - 闭环（没有端点）：从第一条段的一端走，回到起点为止
    """
    key = lambda c: (round(c[0], 4), round(c[1], 4))
    adj = {}
    for i, cs in enumerate(secs):
        a, b = key(cs[0]), key(cs[-1])
        adj.setdefault(a, []).append((b, i))
        adj.setdefault(b, []).append((a, i))
    ends = [n for n, v in adj.items() if len(v) == 1]
    junc = any(len(v) >= 3 for v in adj.values())
    used, path = set(), []

    def walk(start, first_edge=None):
        cur, prev_dir, chunks = start, None, 0
        while True:
            opts = [(nb, i) for nb, i in adj[cur] if i not in used]
            if not opts:
                break
            if len(opts) > 1 and prev_dir is not None:
                def straight(o):
                    cs = secs[o[1]]
                    cand = cs if key(cs[0]) == cur else list(reversed(cs))
                    v = (cand[min(3, len(cand) - 1)][0] - cand[0][0],
                         cand[min(3, len(cand) - 1)][1] - cand[0][1])
                    return -(v[0] * prev_dir[0] + v[1] * prev_dir[1])
                opts.sort(key=straight)
            nb, i = opts[0]
            cs = secs[i]
            if key(cs[0]) != cur:
                cs = list(reversed(cs))
            path.append(cs)
            used.add(i)
            chunks += 1
            if len(cs) >= 2:
                prev_dir = (cs[-1][0] - cs[max(0, len(cs) - 2)][0],
                            cs[-1][1] - cs[max(0, len(cs) - 2)][1])
            cur = nb
            if cur == start:
                break
        return chunks

    if ends:
        # 起点优先选**离我们的站最近的端点**（长线尤其重要：新幹線全长 515km，
        # 从新大阪那端起算的话我们的站会显示 480km+，没有意义）
        start = ends[0]
        if prefer:
            def d_to_stations(n):
                return min(dist_xy(n, (p["lon"], p["lat"]), p["lat"]) for p in prefer)
            start = min(ends, key=d_to_stations)
        walk(start)
    elif secs:
        walk(key(secs[0][0]))
    return path, len(used), (not ends), junc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--geojson", default=DEF_GEOJSON)
    ap.add_argument("--apply", action="store_true", help="写文件（默认只试算）")
    ap.add_argument("--all", action="store_true", help="对所有线路都算（默认只算 OSM 没给的）")
    args = ap.parse_args()

    from sqlalchemy import text

    from app.db import SessionLocal

    if not os.path.exists(args.geojson):
        print("✗ 找不到 N02 区间几何：%s\n  先跑 ./.venv/bin/python scripts/bd_fetch_rail.py"
              % args.geojson)
        return 2
    d = json.load(open(args.geojson, encoding="utf-8"))
    sec_by_line = {}
    for f in d["features"]:
        p = f.get("properties") or {}
        cs = (f.get("geometry") or {}).get("coordinates") or []
        if len(cs) >= 2:
            sec_by_line.setdefault((p.get("N02_004") or "", p.get("N02_003") or ""), []).append(cs)
    print("N02 区间几何：%d 条线路 / %d 段" % (len(sec_by_line),
          sum(len(v) for v in sec_by_line.values())))

    db = SessionLocal()
    lines = db.execute(text("SELECT id, operator, name, n_station FROM bd_line ORDER BY id")).all()
    have_osm = {r[0] for r in db.execute(text(
        "SELECT DISTINCT line_id FROM bd_station WHERE seq_src LIKE 'osm:%' "
        "AND line_id IS NOT NULL")).all()}
    out, skip = [], []
    for lid, op, nm, ns in lines:
        if not args.all and lid in have_osm:
            continue
        secs = sec_by_line.get((op, nm)) or []
        if not secs:
            skip.append((nm, "N02 里没有这条线的几何")); continue
        sts = [{"id": r[0], "name": r[1], "lat": r[2], "lon": r[3]} for r in db.execute(text(
            "SELECT id, name, lat, lon FROM bd_station WHERE line_id=:i AND lat IS NOT NULL"),
            {"i": lid}).all()]
        if not sts:
            skip.append((nm, "车站没有坐标")); continue
        path, cover, loop, junc = build_path(secs, prefer=sts)
        if not path:
            skip.append((nm, "串不成路径")); continue
        relaxed = False
        if cover < MIN_SECTION_RATIO * len(secs):
            # 串不全（有分叉/复线）→ 放宽，但后面必须"每个站都贴在线路上"才算数
            relaxed = True
        # 累积里程
        seq_pts, acc = [], 0.0
        for cs in path:
            for j in range(len(cs) - 1):
                seq_pts.append((acc, cs[j], cs[j + 1]))
                acc += dist_xy(cs[j], cs[j + 1], cs[j][1])
        rows, worst = [], 0.0
        for st in sts:
            best = None
            for base, a, b in seq_pts:
                t, dd, c = proj_to_seg((st["lon"], st["lat"]), a, b, st["lat"])
                if best is None or dd < best[1]:
                    best = (base + t * dist_xy(a, b, a[1]), dd)
            if best is None:
                continue
            worst = max(worst, best[1])
            rows.append({"id": st["id"], "name": st["name"], "km": round(best[0] / 1000.0, 3),
                         "proj_m": round(best[1], 1)})
        if worst > MAX_PROJ_M:
            skip.append((nm, "投影误差最大 %.0fm > %.0fm" % (worst, MAX_PROJ_M))); continue
        if relaxed:
            cov = cover / float(len(secs))
            if cov < MIN_COVER_RELAX:
                skip.append((nm, "只串起 %d/%d 段，且放宽后仍不足 %.0f%%"
                             % (cover, len(secs), MIN_COVER_RELAX * 100))); continue
            if len(rows) < len(sts):
                skip.append((nm, "只串起 %d/%d 段，且有 %d 个站贴不上线路"
                             % (cover, len(secs), len(sts) - len(rows)))); continue
        rows.sort(key=lambda r: r["km"])
        for i, r in enumerate(rows, 1):
            r["seq"] = i
        out.append({"line_id": lid, "operator": op, "name": nm, "n_station": len(sts),
                    "n_seq": len(rows), "loop": loop, "branch": junc,
                    "cover": "%d/%d" % (cover, len(secs)), "relaxed": bool(relaxed),
                    "max_proj_m": round(worst, 1), "total_km": round(acc / 1000.0, 2),
                    "stops": rows})

    print("\n=== 几何法结果 ===")
    print("① 拿到顺序: %d 条" % len(out))
    for o in sorted(out, key=lambda x: -x["n_seq"]):
        flags = ("闭环 " if o["loop"] else "") + ("有分叉 " if o["branch"] else "")
        if o.get("relaxed"):
            flags += "[串起 %s 段(放宽)] " % o["cover"]
        print("   %-16s %2d 站  全长 %7.1fkm  从起点 %6.1fkm  最大投影误差 %5.1fm  %s"
              % (o["name"], o["n_seq"], o["total_km"],
                 max(x["km"] for x in o["stops"]), o["max_proj_m"], flags))
    print("② 放弃（不勉强）: %d 条" % len(skip))
    for nm, why in skip:
        print("   %-16s %s" % (nm, why))
    if not args.apply:
        print("\n（dry-run；加 --apply 才写 %s）" % OUT)
        db.close()
        return 0
    payload = {"source": "N02-22 RailroadSection geometry (MLIT 国土数値情報)",
               "method": "区间几何串线 + 车站投影（沿線里程）",
               "note": "只用于 OSM 拿不到顺序的线路；算不干净的线路不产出",
               "n_line": len(out), "lines": out}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print("\n写入 %s（%d 条线，%.0f KB）" % (OUT, len(out), os.path.getsize(OUT) / 1024.0))
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
