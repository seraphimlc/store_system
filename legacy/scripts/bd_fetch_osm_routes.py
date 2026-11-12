# -*- coding: utf-8 -*-
"""从 OpenStreetMap 拉「运行系统线路 → 有序站列表」，作为车站顺序（seq）的数据源。

用户 2026-10-05 决策：**顺序不自己算，去找现成的源** → 选 **A（OSM）**，并接受 ODbL 署名。

为什么用 OSM：
- route relation 的**成员本身就是有序的**（`role=stop`），环线直接按一圈给出（山手線还能拿内圈/外圈）；
- 它给的是**运行系统口径**（山手線 = 30 站），而 N02 官方口径把山手环拆成 山手線(17)+東北線+東海道線
  —— 管理员认知的"线路"是前者。

产物：`scripts/bd_osm_routes.json`（**随仓库走**，导入时不需要网络）。
⚠️ 数据来源 OSM，许可 **ODbL**：使用处需署名 "© OpenStreetMap contributors"。

用法：
    ./.venv/bin/python scripts/bd_fetch_osm_routes.py              # 用缓存关系的 id 列表
    ./.venv/bin/python scripts/bd_fetch_osm_routes.py --refetch-ids
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "scripts", "bd_osm_routes.json")
IDS_CACHE = os.path.join(ROOT, "scripts", "bd_osm_route_ids.json")

# 一都三県の bounding box
BBOX = "35.40,138.90,36.30,140.95"
ROUTE_TYPES = "^(train|subway|light_rail|monorail|tram|funicular)$"
MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.jp/api/interpreter",
]
UA = {"User-Agent": "kanto-station-asset/1.0 (internal ops tool)"}


def post(query: str, timeout: int = 300) -> dict:
    """打 Overpass（多镜像轮询）。公开实例会限流（504），所以失败要换镜像。"""
    last = None
    for m in MIRRORS:
        try:
            data = urllib.parse.urlencode({"data": query}).encode()
            req = urllib.request.Request(m, data=data, headers=UA)
            return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
        except Exception as e:                                    # noqa: BLE001
            last = e
            print("   [%s 失败: %s]" % (m.split("/")[2], type(e).__name__), flush=True)
            time.sleep(2)
    raise RuntimeError("所有 Overpass 镜像都失败: %r" % (last,))


def fetch_ids(refetch: bool) -> list:
    if os.path.exists(IDS_CACHE) and not refetch:
        ids = json.load(open(IDS_CACHE, encoding="utf-8"))
        print("用缓存的 id 列表: %d 个" % len(ids))
        return ids
    q = ('[out:json][timeout:170];\n'
         'relation["route"~"%s"](%s);\nout ids;' % (ROUTE_TYPES, BBOX))
    d = post(q)
    ids = sorted(e["id"] for e in d.get("elements", []))
    json.dump(ids, open(IDS_CACHE, "w", encoding="utf-8"))
    print("拿到 %d 个线路关系 id" % len(ids))
    return ids


def _parse(d: dict, want: set) -> dict:
    """从 Overpass 结果里取「关系 → 有序 stop 节点」。skel 输出没有标签，但有 id + 坐标。"""
    nodes = {e["id"]: e for e in d.get("elements", []) if e.get("type") == "node"}
    out = {}
    for r in d.get("elements", []):
        if r.get("type") != "relation":
            continue
        t = r.get("tags", {})
        stops, seen = [], set()
        for m in r.get("members", []):
            if m.get("type") != "node" or not (m.get("role") or "").startswith("stop"):
                continue
            n = nodes.get(m["ref"])
            if not n or "lat" not in n:
                continue
            k = (round(n["lat"], 5), round(n["lon"], 5))
            if k in seen:                       # 环线起终点同站，去重
                continue
            seen.add(k)
            stops.append({"ref": m["ref"], "lat": n["lat"], "lon": n["lon"],
                          "name": (n.get("tags") or {}).get("name", ""),
                          "role": m.get("role", "")})
        out[r["id"]] = {"rel_id": r["id"], "name": t.get("name", ""),
                        "route": t.get("route", ""), "operator": t.get("operator", ""),
                        "from": t.get("from", ""), "to": t.get("to", ""),
                        "n_stop": len(stops), "stops": stops}
    return out


def fetch_routes(ids: list, batch: int = 25, tries: int = 3) -> list:
    """分批取关系。⚠️ 不能用 `out geom`（会把整条线的几何也带回来 → 太大被静默截断，
    实测 527 个 id 只回来 195 个）。用 `out body; >; out skel qt;`（只要成员节点）。"""
    got, todo = {}, list(ids)
    for attempt in range(tries):
        if not todo:
            break
        still = []
        for i in range(0, len(todo), batch):
            part = todo[i:i + batch]
            q = ("[out:json][timeout:180];\n"
                 "rel(id:%s);\nout body;\n>;\nout skel qt;" % ",".join(str(x) for x in part))
            try:
                got.update(_parse(post(q), set(part)))
            except Exception as e:                                # noqa: BLE001
                print("   批次失败(第%d次): %r" % (attempt + 1, e), flush=True)
            time.sleep(0.8)
        still = [x for x in todo if x not in got]
        print("   第 %d 轮完成：累计 %d/%d 条（本轮缺 %d）"
              % (attempt + 1, len(got), len(ids), len(still)), flush=True)
        todo = still
        if todo and batch > 1:
            batch = max(1, batch // 4)          # 剩下的用更小的批重试
    for x in todo:
        print("   ⚠️ 最终没取到: %s" % x, flush=True)
    return list(got.values())


def fetch_routes_api(ids: list, sleep_s: float = 0.0, workers: int = 4,
                     out_path: str = None, flush_every: int = 20) -> list:
    """并发走 OSM 官方 API + **断点续传**（同一文件里已有的 rel_id 就跳过）。

    Overpass 会限流，官方 API 稳；单条 full.json 服务端就要几秒，所以开 4 个并发
    （对 OSM 是礼貌范围），并每 20 条落一次盘 —— 中断了不用从头再来。
    """
    import threading
    have = {}
    if out_path and os.path.exists(out_path):
        try:
            d = json.load(open(out_path, encoding="utf-8"))
            have = {r["rel_id"]: r for r in d.get("routes", [])}
            print("   续传：文件里已有 %d 条" % len(have), flush=True)
        except Exception:                                         # noqa: BLE001
            have = {}
    todo = [i for i in ids if i not in have]
    print("   待抓 %d 条（%d 并发）" % (len(todo), workers), flush=True)
    lock = threading.Lock()
    done = [0]

    def save():
        rs = list(have.values())
        rs = [r for r in rs if r["n_stop"] >= 2]
        payload = {"source": "OpenStreetMap route relations (OSM API full.json)",
                   "license": "ODbL 1.0 — © OpenStreetMap contributors",
                   "note": "运行系统线路 → 有序站列表（成员顺序即乘车顺序；环线首尾同站已去重）",
                   "bbox": BBOX, "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "n_route": len(rs), "routes": rs}
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    def work(chunk):
        for rid in chunk:
            url = "https://api.openstreetmap.org/api/0.6/relation/%d/full.json" % rid
            try:
                req = urllib.request.Request(url, headers=UA)
                d = json.loads(urllib.request.urlopen(req, timeout=90).read())
                got = _parse(d, {rid})
                with lock:
                    have.update(got)
                    done[0] += 1
                    if flush_every and done[0] % flush_every == 0:
                        save()
                        print("   [%d/%d] 累计 %d 条" % (done[0], len(todo), len(have)), flush=True)
            except Exception as e:                                # noqa: BLE001
                with lock:
                    done[0] += 1
                    print("   id=%d 失败: %s" % (rid, type(e).__name__), flush=True)
            if sleep_s:
                time.sleep(sleep_s)

    chunks = [todo[i::workers] for i in range(workers)]
    ts = [threading.Thread(target=work, args=(c,), daemon=True) for c in chunks if c]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    save()
    return [r for r in have.values() if r["n_stop"] >= 2]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refetch-ids", action="store_true")
    ap.add_argument("--source", choices=("overpass", "api"), default="overpass",
                    help="api = 逐条走 OSM 官方 API（Overpass 限流时用）")
    args = ap.parse_args()
    ids = fetch_ids(args.refetch_ids)
    routes = (fetch_routes_api(ids, out_path=OUT) if args.source == "api"
              else fetch_routes(ids))
    routes = [r for r in routes if r["n_stop"] >= 2]
    payload = {
        "source": "OpenStreetMap route relations (via Overpass API)",
        "license": "ODbL 1.0 — © OpenStreetMap contributors",
        "note": "运行系统线路 → 有序站列表。成员顺序即乘车顺序；环线会首尾同站（已去重）。",
        "bbox": BBOX,
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "n_route": len(routes),
        "routes": routes,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print("\n写入 %s：%d 条线路 / %d 个站次（%.0f KB）"
          % (OUT, len(routes), sum(r["n_stop"] for r in routes),
             os.path.getsize(OUT) / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
