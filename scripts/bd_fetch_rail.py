# -*- coding: utf-8 -*-
"""抓取「国土数値情報 N02（鉄道）」并抽出**一都三県**的线路 + 车站 → `scripts/bd_kanto_rail.json`。

数据源（都在 lftp.mlit.go.jp，官方、免费）：
- `N02-22_GML.zip`：全国铁路线路 + 车站（含运营公司、站名、坐标、駅コード、駅グループコード）
- `japan.geojson`（dataofjapan/land）：都道府県边界，用来判断车站属于哪个县
  （⚠️ N02 的车站属性里**没有**都道府県，必须靠坐标落点判断）

⚠️ 为什么要有这个脚本：N02 的字段含义（N02_001 鉄道区分 / N02_002 事業者種別）不是自解释的，
下面按**实测**归纳成 `kind`；将来 MLIT 发布新版本（N02-23…）时改 `VERSION` 重跑即可。

用法：
    ./.venv/bin/python scripts/bd_fetch_rail.py            # 用缓存（已下载的 zip 在 /tmp）
    ./.venv/bin/python scripts/bd_fetch_rail.py --refresh   # 重新下载
输出：`scripts/bd_kanto_rail.json`（随仓库走；导入脚本只读它，运行时不需要网络）
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import os
import sys
import urllib.request
import zipfile
from typing import Dict, List, Optional, Tuple

VERSION = "N02-22"
BASE = "https://nlftp.mlit.go.jp/ksj/gml/data/N02/%s/%s_GML.zip" % (VERSION, VERSION)
PREF_GEOJSON = "https://raw.githubusercontent.com/dataofjapan/land/master/japan.geojson"
CACHE_DIR = "/tmp/n02"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bd_kanto_rail.json")

#: 一都三県（JIS 都道府県コード）
KANTO = {"13": "東京都", "11": "埼玉県", "12": "千葉県", "14": "神奈川県"}

#: N02_001(鉄道区分) × N02_002(事業者種別) → 粗分类（实测归纳，见下方 main 的打印）
KIND = {
    ("11", "1"): "shinkansen",   # JR 新幹線
    ("11", "2"): "jr",           # JR 在来線
    ("12", "3"): "public",       # 公営（都営/横浜市営 等）
    ("12", "4"): "private",      # 私鉄（東京メトロ等もここ）
    ("12", "5"): "third",        # 第三セクター
    ("13", "3"): "cable", ("13", "4"): "cable",          # 鋼索鉄道（ケーブル）
    ("14", "3"): "monorail", ("14", "4"): "monorail",
    ("15", "4"): "monorail",
    ("22", "5"): "monorail",
    ("23", "5"): "monorail",
    ("16", "4"): "agt", ("16", "5"): "agt",               # 新交通システム（AGT）
    ("24", "3"): "agt", ("24", "5"): "agt",
    ("21", "3"): "tram", ("21", "4"): "tram",             # 軌道（路面電車）
}


def _opener():
    return urllib.request.build_opener(
        urllib.request.ProxyHandler(urllib.request.getproxies()))


def fetch(url: str, dest: str, refresh: bool = False) -> str:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest) and not refresh:
        print("  缓存命中 %s (%.1f MB)" % (dest, os.path.getsize(dest) / 1e6))
        return dest
    print("  下载 %s …" % url)
    with _opener().open(urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0"}), timeout=900) as r, \
            open(dest, "wb") as f:
        n = 0
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)
            n += len(b)
    print("  完成 %.1f MB → %s" % (n / 1e6, dest))
    return dest


# ---------------- 都道府県判定（点在多边形内，纯 Python） ----------------

def _in_ring(lon: float, lat: float, ring) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > lat) != (yj > lat)) and \
           (lon < (xj - xi) * (lat - yi) / (yj - yi + 1e-18) + xi):
            inside = not inside
        j = i
    return inside


def _in_polys(polys, lon: float, lat: float) -> bool:
    for poly in polys:
        if _in_ring(lon, lat, poly[0]) and not any(
                _in_ring(lon, lat, r) for r in poly[1:]):
            return True
    return False


def load_pref_polys(path: str) -> Dict[str, list]:
    """{JIS 县码: 多边形集合}（只留一都三県，省内存）。"""
    d = json.load(io.open(path, encoding="utf-8"))
    out = {}
    for f in d["features"]:
        code = "%02d" % f["properties"]["id"]
        if code not in KANTO:
            continue
        geom = f["geometry"]
        out[code] = ([geom["coordinates"]] if geom["type"] == "Polygon"
                     else geom["coordinates"])
    return out


def which_pref(polys: Dict[str, list], lon: float, lat: float) -> Optional[str]:
    bbox = ((-180, 180), (-90, 90))
    for code in KANTO:
        pl = polys.get(code)
        if not pl:
            continue
        for poly in pl:
            xs = [p[0] for p in poly[0]]
            ys = [p[1] for p in poly[0]]
            if min(xs) <= lon <= max(xs) and min(ys) <= lat <= max(ys):
                if _in_polys([poly], lon, lat):
                    return code
    return None


def _xy(feat: dict) -> Tuple[float, float]:
    c = feat["geometry"]["coordinates"]
    return (c[0][0], c[0][1]) if isinstance(c[0], list) else (c[0], c[1])


def _dist2(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="强制重新下载")
    args = ap.parse_args()

    print("[1/4] 取数据")
    zpath = fetch(BASE, os.path.join(CACHE_DIR, "%s_GML.zip" % VERSION), args.refresh)
    ppath = fetch(PREF_GEOJSON, os.path.join(CACHE_DIR, "japan.geojson"), args.refresh)

    print("[2/4] 读车站 + 判定都道府県")
    with zipfile.ZipFile(zpath) as z:
        raw = json.loads(z.read("%s/N02-22_Station.geojson" % "UTF-8")
                         .decode("utf-8"))["features"]
    polys = load_pref_polys(ppath)
    stations: List[dict] = []
    for f in raw:
        lon, lat = _xy(f)
        pref = which_pref(polys, lon, lat)
        if not pref:
            continue
        p = f["properties"]
        stations.append({
            "name": p["N02_005"], "line": p["N02_003"], "operator": p["N02_004"],
            "code": p.get("N02_005c", ""), "group": p.get("N02_005g", ""),
            "kind": KIND.get((p["N02_001"], p["N02_002"]), p["N02_001"]),
            "pref": pref, "lon": round(lon, 6), "lat": round(lat, 6),
            "flag": "%s/%s" % (p["N02_001"], p["N02_002"]),
        })
    print("  一都三県车站记录: %d" % len(stations))

    print("[3/4] 同一线路同一车站去重")
    # MLIT 会把同一个车站按站台/区间重复记录（坐标相差 100~300m），
    # 例：山手線「新宿」记 4 条。规则 = (线路, 站名) 相同 且 坐标邻近(<1km) → 合并。
    groups: Dict[Tuple[str, str], List[dict]] = collections.OrderedDict()
    for s in stations:
        groups.setdefault((s["operator"], s["line"], s["name"]), []).append(s)
    dedup: List[dict] = []
    merged = 0
    for (op, ln, nm), rows in groups.items():
        keep = []
        for r in rows:
            if any(_dist2((r["lon"], r["lat"]), (k["lon"], k["lat"])) < 1e-4
                   for k in keep):
                merged += 1
                continue
            keep.append(r)
        dedup.extend(keep)
        if len(keep) > 1:            # 同线同名但相距很远 → 真是两个站，保留并标记
            print("  ⚠️ 同线同名但不同位置：%s %s %s ×%d" % (op, ln, nm, len(keep)))
    print("  合并重复记录 %d 条 → %d 个车站" % (merged, len(dedup)))

    print("[4/4] 汇总线路")
    lines: Dict[str, dict] = collections.OrderedDict()
    for s in dedup:
        key = "%s|%s" % (s["operator"], s["line"])
        d = lines.setdefault(key, {
            "key": key, "operator": s["operator"], "name": s["line"],
            "kind": s["kind"], "prefs": [], "n": 0})
        d["n"] += 1
        if s["pref"] not in d["prefs"]:
            d["prefs"].append(s["pref"])
    for s in dedup:
        s["line_key"] = "%s|%s" % (s["operator"], s["line"])

    out = {
        "source": "国土数値情報（鉄道）%s / 国土交通省" % VERSION,
        "note": "一都三県（東京都・埼玉県・千葉県・神奈川県）の駅。"
                "同一个物理车站若跨多条线路，本数据里**按线路各存一行**"
                "（用户 2026-10-03 口径：先按线路来，合并规则以后再说）；"
                "`group`(N02_005g) 是 MLIT 的同一车站分组码，留给将来合并用。",
        "prefs": KANTO,
        "lines": list(lines.values()),
        "stations": dedup,
    }
    with io.open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"), sort_keys=False)
    print("\n线路 %d 条 / 车站 %d 个 → %s (%.0f KB)"
          % (len(lines), len(dedup), OUT, os.path.getsize(OUT) / 1024))
    kinds = collections.Counter(l["kind"] for l in lines.values())
    print("线路类型分布:", dict(kinds))
    return 0


if __name__ == "__main__":
    sys.exit(main())
