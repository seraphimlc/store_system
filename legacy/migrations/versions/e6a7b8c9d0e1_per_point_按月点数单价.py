# -*- coding: utf-8 -*-
"""点数单价按月可变：月绩效/找平记录锁存 per_point（円/点，默认250）

- month_perf_records.per_point：该月单价（"当前点数金额"列）；
- adjust_records.per_point：找平发生时单价（下月纠偏按此价折算，不随本月变）。
"""
import sqlalchemy as sa
from alembic import op

revision = "e6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("month_perf_records",
                  sa.Column("per_point", sa.Integer(), nullable=False,
                            server_default="250"))
    op.add_column("adjust_records",
                  sa.Column("per_point", sa.Integer(), nullable=False,
                  server_default="250"))


def downgrade():
    op.drop_column("adjust_records", "per_point")
    op.drop_column("month_perf_records", "per_point")
