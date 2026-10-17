# -*- coding: utf-8 -*-
"""raw_records 两个索引：clean_status、以及 (clean_status, 月份前缀)。

**为什么要加**（2026-10-17 grokbot 复核 P0-8 / P1-15 / P1-16）：
`perf.month_dup_map()`（月度绩效页、员工 `/my/perf` 都走）按
`clean_status='cross_file_dup'` + `modified_raw[:7]==月份` 统计"重复巡店"。
线上 `raw_records` 148,851 行，其中 `cross_file_dup` **56,110 行（38%）**。

实测（本地 PG15 + 线上数据副本 148,851 行，同一句 SQL）：

| 索引 | 计划 | 耗时 |
|---|---|---|
| 无 | Parallel Seq Scan | 118.5 ms |
| `clean_status` | Bitmap Index Scan | 30.2 ms |
| `(clean_status, substr(modified_raw,1,7))` | Bitmap Index Scan | **11.1 ms** |

⚠️ **报告里说的"给 `modified_raw` 加普通索引"其实没用**：查询过滤的是
`substr(modified_raw,1,7)`（月份前缀），普通 btree 对这种**列上的函数**不生效
（除非 collation 是 C 且写成 `LIKE '2026-09%'`）。所以这里直接给表达式建索引。

⚠️ MySQL 不支持这种表达式索引（SQLAlchemy 生成的 DDL 与 8.0.13+ 的功能索引语法不一致），
退化成 `(clean_status, modified_raw(32))` 前缀索引 —— 本仓库 MySQL 只作**本地只读副本**
（`scripts/pull_prod_to_mysql.py`），不影响线上。
"""
from alembic import op
import sqlalchemy as sa

revision = "ab7f3c9d21e0"
down_revision = "f0a1b2c3d4e5"

_CS = "ix_raw_records_clean_status"
_CSM = "ix_raw_records_cs_modmonth"


def upgrade():
    bind = op.get_bind()
    op.create_index(_CS, "raw_records", ["clean_status"])
    if bind.dialect.name == "mysql":
        op.create_index(_CSM, "raw_records",
                        ["clean_status", "modified_raw"], mysql_length=32)
    else:
        # PG / SQLite 都支持表达式索引（substr 是确定性函数）。
        op.create_index(_CSM, "raw_records",
                        ["clean_status", sa.text("substr(modified_raw, 1, 7)")])


def downgrade():
    op.drop_index(_CSM, table_name="raw_records")
    op.drop_index(_CS, table_name="raw_records")
