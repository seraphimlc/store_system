# -*- coding: utf-8 -*-
"""本地库补列：模型有、库缺的列用 ALTER TABLE ADD COLUMN 补齐（SQLite）。

为什么需要：本地 `store_settle_live.db` 是很早用 `create_all` 建的；后续迁移
（alembic）新增的列不会自动补到既有表 → 运行时 `no such column`（实测
`users.lang`、`api_tokens.expires_at` 都踩过）。**只加列，不改/不删数据。**

用法：DATABASE_URL="sqlite:///./store_settle_live.db" ./.venv/bin/python scripts/sync_local_columns.py [--apply]
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import String, Text, Boolean, DateTime, Integer, Float
from app.db import Base
import app.models  # noqa: F401  注册模型

APPLY = "--apply" in sys.argv
DB = os.environ.get("DATABASE_URL", "sqlite:///./store_settle_live.db")
PATH = DB.replace("sqlite:///", "").replace("file:", "").split("?")[0]

con = sqlite3.connect(PATH)
cur = con.cursor()
existing_tables = {r[0] for r in cur.execute(
    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

added = []
for table in Base.metadata.sorted_tables:
    if table.name not in existing_tables:
        print(f"  ⚠️ 表缺失（需建表，不在此脚本范围）：{table.name}")
        continue
    have = {r[1] for r in cur.execute(f"PRAGMA table_info({table.name})").fetchall()}
    for col in table.columns:
        if col.name in have:
            continue
        if col.primary_key:
            continue
        t = col.type
        if isinstance(t, (String, Text)):
            sql_type = f"VARCHAR({getattr(t, 'length', None) or 255})"
        elif isinstance(t, Boolean):
            sql_type = "BOOLEAN"
        elif isinstance(t, DateTime):
            sql_type = "DATETIME"
        elif isinstance(t, Float):
            sql_type = "FLOAT"
        else:
            sql_type = "INTEGER"
        ddl = f"ALTER TABLE {table.name} ADD COLUMN {col.name} {sql_type}"
        default = None
        if col.default is not None and getattr(col.default, "arg", None) is not None \
                and not callable(col.default.arg):
            default = col.default.arg
        if default is not None:
            lit = f"'{default}'" if isinstance(default, str) else (
                "1" if default is True else "0" if default is False else str(default))
            ddl += f" DEFAULT {lit}"
        print(f"  {'＋' if APPLY else '（dry）'} {table.name}.{col.name} {sql_type}"
              + (f" DEFAULT {default}" if default is not None else ""))
        if APPLY:
            cur.execute(ddl)
        added.append(f"{table.name}.{col.name}")

if APPLY:
    con.commit()
con.close()
print(f"\n{'✅ 已补 ' + str(len(added)) + ' 列' if APPLY else '（dry-run：加 --apply 执行）'}"
      f"：{', '.join(added[:12])}{' …' if len(added) > 12 else ''}")
