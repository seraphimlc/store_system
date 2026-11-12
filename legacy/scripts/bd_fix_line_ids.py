# -*- coding: utf-8 -*-
"""把 `bd_line.id` 的**负数 id 修成正常正数**（并把车站引用一起改过去）。

为什么会有负数：`bd_import_rail` 为了"dry-run 也按真实逻辑预演"给线路发了临时负 id，
但 2026-10-05 第一版**真实导入时也用了它** → 131 条线路全是负 id（车站 1,920 行引用它们）。
代码已修（真实导入让数据库发号）；这个脚本修**已经写坏的库**。

做法（不碰任何唯一键，安全）：
1. 读出全部线路，按旧 id 排序
2. 用**正 id**（1..N）插入同一份线路数据
3. 把 `bd_station.line_id` 从旧 id 指向新 id
4. 删掉旧（负数）行

用法：
    ./.venv/bin/python scripts/bd_fix_line_ids.py            # dry-run
    ./.venv/bin/python scripts/bd_fix_line_ids.py --apply
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    from sqlalchemy import text

    from app.config import get_settings
    from app.db import get_engine

    url = get_settings().database_url
    print("库:", url)
    engine = get_engine()
    cols = ("name", "name_norm", "operator", "operator_short", "kind", "prefs",
            "n_station", "source", "note", "created_at", "updated_at")
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, %s FROM bd_line ORDER BY id" % ", ".join(cols))).all()
        bad = [r for r in rows if r[0] <= 0]
        print("线路 %d 条；其中非正 id %d 条" % (len(rows), len(bad)))
        if not bad:
            print("无需修复 ✓")
            return 0
        if not args.apply:
            print("（dry-run；加 --apply 才真改）")
            return 0

        path = url.replace("sqlite:///", "").split("?")[0]
        if os.path.exists(path):
            bak = "%s.pre_lineid_%s" % (path, time.strftime("%Y%m%d_%H%M%S"))
            shutil.copy2(path, bak)
            print("已备份 →", bak)

        for i, r in enumerate(rows, start=1):
            old_id = r[0]
            # ① 旧行先改成唯一占位：否则插新行会撞 uq_bd_line_op_name
            #    （operator+name 唯一；占位符用不可能出现在真数据里的 \x01）
            conn.execute(text("UPDATE bd_line SET operator=:o, name=:n WHERE id=:id"),
                         {"o": "\x01tmp%d" % old_id, "n": "\x01tmp%d" % old_id,
                          "id": old_id})
            # ② 用原值插正 id 行
            conn.execute(text(
                "INSERT INTO bd_line (id, %s) VALUES (%s)"
                % (", ".join(cols), ", ".join([":id"] + [":c%d" % k for k in range(len(cols))]))),
                dict([("id", i)] + [("c%d" % k, r[k + 1]) for k in range(len(cols))]))
            # ③ 车站改指向新 id
            conn.execute(text("UPDATE bd_station SET line_id=:new WHERE line_id=:old"),
                         {"new": i, "old": old_id})
        conn.execute(text("DELETE FROM bd_line WHERE id <= 0"))
        conn.commit()

        n = conn.execute(text("SELECT COUNT(*) FROM bd_line WHERE id <= 0")).scalar()
        left = conn.execute(text("SELECT COUNT(*) FROM bd_station WHERE line_id < 0")).scalar()
        rng = conn.execute(text("SELECT MIN(id), MAX(id), COUNT(*) FROM bd_line")).all()
        print("修完：非正 id %d 条 / 悬挂车站引用 %d 条 / id 范围 %s" % (n, left, rng[0]))
        return 0 if (n == 0 and left == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
