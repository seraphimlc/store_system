# -*- coding: utf-8 -*-
"""正式表 japan_date 加索引（ix_formal_japan_date）。

原因：`formal_date_range()`（原 file_coverage / import_period / suggest_period 里的
min/max(japan_date)）**出现在每个报告/对比/导出请求里**，而该列原先没有索引
（评审实测：3 万行 SCAN formal_records，冷启动 ~47ms）。

CREATE INDEX 会在 PG 上短暂持锁；正式表每月约 +1.5 万行，直接建即可。
"""
from alembic import op

revision = "a2b3c4d5e6f7"
down_revision = "d0e1f2a3b4c5"


def upgrade():
    op.create_index("ix_formal_japan_date", "formal_records", ["japan_date"])


def downgrade():
    op.drop_index("ix_formal_japan_date", table_name="formal_records")
