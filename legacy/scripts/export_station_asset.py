# -*- coding: utf-8 -*-
"""把**车站数据资产**导出成 CSV（给别的系统用 / 存档 / Excel 看）。

用户 2026-10-05："车站数据可以当成我们的数据资产。也是任务的输入源之一。"
→ 资产要能**拿出去用**，所以有两份、两种粒度：

- `stations_places.csv`：**物理车站**一层（1,568 行）——站名/县/坐标/线路数/经过的线路
- `stations_lines.csv`：**站×线**一层（1,920 行）——含**任务状态**（任务就是挂在这一层）
  列：物理车站id/站名/运营商/线路/线路类型/站名/駅コード/县/经纬度/任务id/任务状态/进度/队伍/担当

⚠️ 用 **utf-8-sig**（带 BOM）：Excel 直接双击打开日文/中文不乱码。

用法：
    ./.venv/bin/python scripts/export_station_asset.py                 # → data/stations_*.csv
    ./.venv/bin/python scripts/export_station_asset.py --out /tmp/asset
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _d(x) -> str:
    """日期/字符串都吃（SQLite 的原始 text() 查询不解析类型，日期可能是 str）。"""
    if not x:
        return ""
    return x.strftime("%Y-%m-%d") if hasattr(x, "strftime") else str(x)[:10]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "data"))
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    from sqlalchemy import text
    from app.db import SessionLocal

    db = SessionLocal()
    p1 = os.path.join(args.out, "stations_places.csv")
    p2 = os.path.join(args.out, "stations_lines.csv")

    with io.open(p1, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["物理车站id", "站名", "归一化站名", "都道府県", "市区町村",
                    "纬度", "经度", "线路数", "运营商数", "运营商", "经过的线路",
                    "駅グループコード", "来源"])
        for r in db.execute(text(
                "SELECT id, name, name_norm, pref, city, lat, lon, n_line, "
                "n_operator, operators, lines_text, group_code, source "
                "FROM bd_station_place ORDER BY pref, name")).all():
            w.writerow(list(r))

    with io.open(p2, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["物理车站id", "站名", "运营公司", "线路", "线路类型", "県",
                    "站名(线)", "駅コード", "纬度", "经度",
                    "任务id", "任务状态", "进度%", "队伍", "担当", "分配日期"])
        rows = db.execute(text("""
            SELECT p.id, p.name, l.operator_short, l.name, l.kind, s.pref,
                   s.name, s.ekicode, s.lat, s.lon,
                   t.id, t.state, t.pct, COALESCE(tm.name, ''), t.assign_date,
                   COALESCE((SELECT GROUP_CONCAT(pe.display_name, '、')
                             FROM bd_task_assign a
                             LEFT JOIN persons pe ON pe.code = a.person_code
                             WHERE a.task_id = t.id), '')
              FROM bd_station s
              LEFT JOIN bd_station_place p ON p.id = s.place_id
              LEFT JOIN bd_line l ON l.id = s.line_id
              LEFT JOIN bd_task t ON t.station_id = s.id
              LEFT JOIN bd_team tm ON tm.id = t.team_id
             ORDER BY p.pref, p.name, l.operator, l.name""")).all()
        for r in rows:
            w.writerow([r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9],
                        r[10], r[11], r[12], r[13], r[15],
                        _d(r[14])])

    n1 = db.execute(text("SELECT COUNT(*) FROM bd_station_place")).scalar()
    n2 = db.execute(text("SELECT COUNT(*) FROM bd_station")).scalar()
    db.close()
    print("物理车站 %d 行 → %s" % (n1, p1))
    print("站×线 %d 行（含任务状态）→ %s" % (n2, p2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
