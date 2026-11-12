# -*- coding: utf-8 -*-
"""车站数据资产**体检**（只读）。

用户 2026-10-05："你把车站的数据好好整理一下，以后有更大的用处…车站数据可以当成我们的数据资产。"

资产做成"资产"的关键不是导一次数据，而是**能被持续校验**。本脚本检查：

1. **规模**：物理车站 / 站×线 / 线路 三层数量
2. **完整性**：缺线路 / 缺物理车站 / 缺县 / 缺坐标 / 缺市区町村（待补，只报告不判错）
3. **一致性**：同组站名一致、`n_line` 与成员数一致、`n_station` 与实际一致、同线不重名、无孤儿 place
4. **与数据源对齐**：`scripts/bd_kanto_rail.json`（N02）的线数/站数 vs 库里的 mlit 行

有**违规**（1/3/4 类）→ 非 0 退出（可当发布验收）；市区町村属于"待补的完整度"→ 只提示。

用法：`./.venv/bin/python scripts/check_station_asset.py`（`PYTHONPATH=.` 可省）
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

BAR = "─" * 58


def main() -> int:
    from app.db import SessionLocal
    from app.services import bd_places

    db = SessionLocal()
    st = bd_places.asset_stats(db)
    print(BAR)
    print("车站数据资产 · 体检")
    print(BAR)
    print("规模：物理车站 %d / 站×线 %d / 线路 %d（平均 %.2f 条线每个物理站，跨线站 %d）"
          % (st["n_place"], st["n_station"], st["n_line"],
             st["avg_line_per_place"], st["n_multi_line"]))
    print("类型：", "、".join("%s %d" % (k, v) for k, v in sorted(
        st["kinds"].items(), key=lambda kv: -kv[1])))
    print("按县：", "、".join("%s %d" % (st["pref_labels"].get(k, k), v)
                            for k, v in sorted(st["prefs"].items(), key=lambda kv: -kv[1])))

    print(BAR)
    issues = bd_places.integrity_issues(db)
    for i in issues:
        print("✗ %s：%s（%d 处）" % (i["kind"], i["detail"], i["n"]))

    # 与数据源对齐
    src = os.path.join(ROOT, "scripts", "bd_kanto_rail.json")
    src_ok = True
    if os.path.exists(src):
        d = json.load(open(src, encoding="utf-8"))
        n_line_src, n_st_src = len(d["lines"]), len(d["stations"])
        n_line_db = db.execute(__import__("sqlalchemy").text(
            "SELECT COUNT(*) FROM bd_line WHERE source='mlit'")).scalar()
        # ⚠️ 不能按 source='mlit' 数：**认领的 515 行**是从种子表建的（source=manual），
        # 但属性来自 N02。口径 = "N02 派生行" = group_code 非空（N02 导入/认领的行都有）
        n_st_db = db.execute(__import__("sqlalchemy").text(
            "SELECT COUNT(*) FROM bd_station WHERE group_code != ''")).scalar()
        same = (n_line_src == n_line_db and n_st_src == n_st_db)
        print("%s 数据源对齐：N02 线 %d/站 %d ↔ 库里 N02 派生 线 %d/站 %d"
              % ("✓" if same else "✗", n_line_src, n_st_src, n_line_db, n_st_db))
        src_ok = same
        if not same:
            issues.append({"kind": "数据源不一致", "detail": "库与 bd_kanto_rail.json 不符",
                           "n": 1})
    else:
        print("ⓘ 没有 scripts/bd_kanto_rail.json（跳过数据源对齐）")

    print(BAR)
    if st["n_no_pref"] or st["n_no_geo"]:
        print("ⓘ 待补：缺县 %d / 缺坐标 %d" % (st["n_no_pref"], st["n_no_geo"]))
    print("ⓘ 完整度：市区町村待补（需行政区划边界数据源；见 AGENTS「车站数据资产」）")
    db.close()

    if issues:
        print("\n结论：**有 %d 类问题**（见上 ✗）" % len(issues))
        return 1
    print("\n结论：资产健康 ✓（规模/完整性/一致性/数据源对齐 全部通过）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
