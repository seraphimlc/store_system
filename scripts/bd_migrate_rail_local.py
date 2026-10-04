# -*- coding: utf-8 -*-
"""把「一都三県线路+车站」的表结构变更应用到**本地库**（本地不跑 alembic）。

背景：本地库是 `Base.metadata.create_all` 建的、`alembic_version` 表是空的 →
`alembic upgrade head` 会从头跑并因表已存在而失败。生产走 alembic 迁移
（`migrations/versions/f9a8b7c6d5e4_*.py`），本地走这个脚本。

⚠️ 本地库是 SQLite：**不能直接 drop 唯一约束**（旧的 `uq_bd_station_name`）→ 必须重建表。
用「建新表 → 拷数据 → 删旧表 → 改名」的顺序（**不要**先 RENAME 旧表：
SQLite ≥3.25 会把别的表里指向它的外键一起改写）。
⚠️ 不 import alembic（本机 iCloud 会把 venv 文件驱逐成 dataless，import 会超时）。

用法：
    ./.venv/bin/python scripts/bd_migrate_rail_local.py            # dry-run
    ./.venv/bin/python scripts/bd_migrate_rail_local.py --apply    # 真改（先自动备份）
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _cols(insp, table):
    try:
        return {c["name"] for c in insp.get_columns(table)}
    except Exception:                                     # noqa: BLE001
        return set()


def _idx_names(insp, table):
    try:
        return {i["name"] for i in insp.get_indexes(table)}
    except Exception:                                     # noqa: BLE001
        return set()


def _uniq_names(insp, table):
    try:
        return {c["name"] for c in insp.get_unique_constraints(table)}
    except Exception:                                     # noqa: BLE001
        return set()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    from sqlalchemy import inspect, text
    from sqlalchemy.schema import CreateTable

    from app.config import get_settings
    from app.db import Base, get_engine
    import app.models  # noqa: F401  注册全部表

    url = get_settings().database_url
    print("库:", url)
    engine = get_engine()
    orm_st = Base.metadata.tables["bd_station"]
    orm_idx = {i.name for i in orm_st.indexes}
    orm_cols = {c.name for c in orm_st.columns}
    old_unique = "uq_bd_station_name"

    with engine.connect() as conn:
        insp = inspect(conn)
        have_tables = set(insp.get_table_names())
        st_cols = _cols(insp, "bd_station")
        tk_cols = _cols(insp, "bd_task")
        has_old_unique = (old_unique in _uniq_names(insp, "bd_station")
                          or old_unique in _idx_names(insp, "bd_station"))
        print("  %-20s %s" % ("bd_line 新表", "缺" if "bd_line" not in have_tables else "已有"))
        print("  %-20s %s" % ("bd_station 缺列", sorted(orm_cols - st_cols) or "无"))
        print("  %-20s %s" % ("bd_station 旧唯一键", "在（要拆）" if has_old_unique else "无"))
        print("  %-20s %s" % ("bd_task.source_type", "缺" if "source_type" not in tk_cols else "已有"))

        if not args.apply:
            print("\n（dry-run；加 --apply 才真改）")
            return 0

        path = url.replace("sqlite:///", "").split("?")[0]
        if os.path.exists(path):
            bak = "%s.pre_rail_%s" % (path, time.strftime("%Y%m%d_%H%M%S"))
            shutil.copy2(path, bak)
            print("\n已备份 →", bak)

        # ① 线路主档（create 连索引一起建；checkfirst 幂等）
        if "bd_line" not in have_tables:
            Base.metadata.tables["bd_line"].create(conn, checkfirst=True)
            print("建表 bd_line ✓")

        # ② 任务来源判别
        if "source_type" not in tk_cols:
            conn.execute(text("ALTER TABLE bd_task ADD COLUMN "
                              "source_type VARCHAR(16) NOT NULL DEFAULT 'station'"))
            print("bd_task.source_type ✓")

        # ③ 车站表重建（加列 + 换唯一键：name_norm → (line_id, name_norm)）
        if (orm_cols - st_cols) or has_old_unique:
            conn.execute(text("PRAGMA foreign_keys=OFF"))
            newt = orm_st.to_metadata(orm_st.metadata, name="bd_station_new")
            newt.indexes.clear()                 # 索引等改名后再建（否则索引名冲突）
            conn.execute(text(str(CreateTable(newt).compile(conn))))
            keep = [c for c in orm_st.columns if c.name in st_cols]
            collist = ", ".join('"%s"' % c.name for c in keep)
            conn.execute(text("INSERT INTO bd_station_new (%s) SELECT %s FROM bd_station"
                              % (collist, collist)))
            n = conn.execute(text("SELECT COUNT(*) FROM bd_station_new")).scalar()
            conn.execute(text("DROP TABLE bd_station"))
            conn.execute(text("ALTER TABLE bd_station_new RENAME TO bd_station"))
            for i in orm_st.indexes:
                i.create(conn, checkfirst=True)
            conn.commit()
            print("重建 bd_station ✓ 迁移 %d 行" % n)

        # ④ 校验
        insp = inspect(conn)
        after_cols = _cols(insp, "bd_station")
        after_idx = _idx_names(insp, "bd_station")
        ok = after_cols == orm_cols
        print("\n列 = ORM:", ok)
        if not ok:
            print("  ⚠️ 库多 %s / ORM 多 %s"
                  % (sorted(after_cols - orm_cols), sorted(orm_cols - after_cols)))
        print("索引缺:", sorted(orm_idx - after_idx) or "无")
        print("bd_station 行数:",
              conn.execute(text("SELECT COUNT(*) FROM bd_station")).scalar())
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
