# -*- coding: utf-8 -*-
"""把 `bd_line.n_station` 回填成**实际站数**（冗余字段；资产体检会查）。

**为什么需要它**：`n_station` 原来只在 N02 导入时写成**源里的 `n`**，而资产体检
（`bd_places.integrity_issues` ④）要求它等于 `bd_station` 行数 → 一旦有手工补的站
（实例 `幕張豊砂`，N02-22 里没有），这个字段就漂了：

    京葉線 n_station=18，实际 19 行 → 体检报「线路站数与实际不符」并**非 0 退出**

新代码已让 `bd_tasks.create_station()` 和 `bd_import_rail()` 自动回填；
本脚本用来**补历史一次性回填**，也可以随时当巡检跑（幂等）。

用法（容器内）：`python scripts/bd_recount_line_stations.py`（干跑）
              `APPLY=1 python scripts/bd_recount_line_stations.py`（写）
"""
import os

from sqlalchemy import text

from app.db import SessionLocal
from app.services import bd_lines

APPLY = os.environ.get("APPLY") == "1"
DRIFT = """SELECT l.id, l.operator_short, l.name, l.n_station,
                  (SELECT COUNT(*) FROM bd_station s WHERE s.line_id = l.id) AS actual
           FROM bd_line l
           WHERE l.n_station != (SELECT COUNT(*) FROM bd_station s
                                 WHERE s.line_id = l.id)
           ORDER BY l.id"""

db = SessionLocal()
rows = db.execute(text(DRIFT)).all()
print("口径：n_station = 实际 bd_station 行数（资产体检 ④）")
print("漂了的线路：%d 条" % len(rows))
for r in rows:
    print("   id=%-5s %-6s %-22s n_station=%-4s 实际=%s"
          % (r[0], r[1] or "", r[2], r[3], r[4]))
if not rows:
    print("无需回填 ✓")
    db.close()
    raise SystemExit(0)
if not APPLY:
    print("\n（干跑；加 APPLY=1 才写）")
    db.close()
    raise SystemExit(0)

n = bd_lines.recount_stations(db)
db.commit()
left = db.execute(text(DRIFT)).all()
print("\n已回填 %d 行；复查仍漂 = %d 条 %s" % (n, len(left), [tuple(x) for x in left]))
assert not left, "回填后还有漂的线路"
print("✓ 资产一致性恢复（可再跑 scripts/check_station_asset.py）")
db.close()
