# -*- coding: utf-8 -*-
"""把一个 **N02 里还没有的站** 补进车站资产，坐标取自 OSM 路线资产。

**为什么单开一条流程**：车站资产的正规入口是 `scripts/bd_import_rail.py`
（N02 派生、幂等 + 认领历史站）。可 **2023 年之后开的新站，N02-22 里根本没有**
（实例：`幕張豊砂`，JR 京葉線，2023-03-18 开业），而真作业的计划表已经派到它 →
需要一条"补站但坐标**不靠手抄**"的路子。

**坐标从哪来**：`scripts/bd_osm_routes.json` 里 OSM 的线路关系**已经包含**这些新站
——「JR京葉線 各駅停車 (東京 → 蘇我)」18 站里就有 `幕張豊砂`，而且位置正好是
**第 13 站**：库里京葉線的 `seq` 就是 **12(新習志野) → 14(海浜幕張)**，13 空着。
（seq 来自 OSM、站行来自 N02 ⇒ 洞就是"N02 缺这个站"留下的。）

写库用服务层 `bd_tasks.create_station(..., pref/lon/lat)`：
`source="manual"`、`ekicode/group_code` **故意留空** → 物理车站层按「站名+县」归组、
**不会污染**资产体检的「数据源对齐」（那一项数的是 `group_code != ''` 的 N02 派生行）；
将来 N02 新版本一到，`bd_import_rail` 会按 (线路, 站名) 认领它并补齐 MLIT 属性。

补完**必须**跑 `scripts/bd_fill_seq.py --apply` 填 `seq/along_km`，再跑
`scripts/check_station_asset.py` 体检。

用法（容器内）：`APPLY=1 python - < scripts/add_station_from_osm.py`
"""
import json
import os
import sys

NAME = "幕張豊砂"
LINE_NAME = "京葉線"
PREF = "12"                      # 12 = 千葉県
APPLY = os.environ.get("APPLY") == "1"

CAND = [os.path.join(os.getcwd(), "scripts", "bd_osm_routes.json"),
        "/srv/app/scripts/bd_osm_routes.json"]
SRC = next((p for p in CAND if os.path.exists(p)), None)
if not SRC:
    raise SystemExit("找不到 bd_osm_routes.json，试过：%s" % CAND)

osm = json.load(open(SRC, encoding="utf-8"))
hits = []
for r in osm.get("routes") or []:
    for s in r.get("stops") or []:
        if s.get("name") == NAME:
            hits.append((r.get("name"), r.get("n_stop"), s["lat"], s["lon"]))
if not hits:
    raise SystemExit("OSM 路线资产里没有 %s —— 别硬造坐标" % NAME)
lat, lon = hits[0][2], hits[0][3]
print("OSM 里找到 %s（%d 条线路关系提到它）：" % (NAME, len(hits)))
for rn, n, la, lo in hits:
    print("   %-42s n_stop=%-3s lat=%.5f lon=%.5f" % (rn, n, la, lo))
spread = max(abs(h[2] - lat) for h in hits) + max(abs(h[3] - lon) for h in hits)
assert spread < 0.01, "各来源坐标差太多（%.4f），先人工核对" % spread
print("采用坐标：lat=%.5f lon=%.5f（各来源一致）" % (lat, lon))

from sqlalchemy import text                                    # noqa: E402
from app.db import SessionLocal                                # noqa: E402
from app.services import bd_tasks                              # noqa: E402

db = SessionLocal()
line = db.execute(text("SELECT id, name, operator FROM bd_line WHERE name=:n"),
                  {"n": LINE_NAME}).first()
if line is None:
    raise SystemExit("库里没有线路 %s" % LINE_NAME)
print("\n线路：id=%s %s（%s）" % tuple(line))

before = db.execute(text("""SELECT COUNT(*) FROM bd_station""")).scalar()
exist = db.execute(text("""SELECT id, pref, lon, lat, place_id FROM bd_station
                           WHERE name=:n AND line_id=:l"""),
                   {"n": NAME, "l": line[0]}).first()
if exist:
    print("\n⚠️ 这个站已经在了：id=%s pref=%s lon=%s lat=%s place=%s"
          % tuple(exist))
    db.close()
    sys.exit(0)

seq_now = db.execute(text("""SELECT seq, name FROM bd_station
                             WHERE line_id=:l ORDER BY seq NULLS LAST"""),
                     {"l": line[0]}).all()
print("补前 %s 的 seq（注意洞）：" % LINE_NAME)
print("   " + "、".join("%s:%s" % (s, n) for s, n in seq_now))

if not APPLY:
    print("\n（干跑；加 APPLY=1 才写）")
    db.close()
    sys.exit(0)

st = bd_tasks.create_station(db, NAME, line_id=line[0], pref=PREF,
                             lon=lon, lat=lat)
db.commit()
after = db.execute(text("""SELECT COUNT(*) FROM bd_station""")).scalar()
print("\n✓ 已建站：id=%s name=%s pref=%s source=%s group_code=%r ekicode=%r"
      % (st.id, st.name, st.pref, st.source, st.group_code, st.ekicode))
print("  坐标 lat=%.5f lon=%.5f" % (st.lat, st.lon))
pl = db.execute(text("SELECT id, name, pref, lon, lat, source, group_code "
                     "FROM bd_station_place WHERE id=:i"),
                {"i": st.place_id}).first()
print("  物理车站层：%s" % (tuple(pl),))
print("  车站数 %d → %d（+%d）" % (before, after, after - before))
print("\n⚠️ 接着必须跑：scripts/bd_fill_seq.py --apply 然后 check_station_asset.py")
db.close()
