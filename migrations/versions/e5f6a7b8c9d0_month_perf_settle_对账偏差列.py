# -*- coding: utf-8 -*-
"""month_perf_records 加 对账/偏差 列

月度对账完成后更新：对账点数/对账金额/偏差点数/偏差金额。
"""
import sqlalchemy as sa
from alembic import op

revision = "e5f6a7b8c9d0"
down_revision = "e4f5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("month_perf_records",
                  sa.Column("settle_points", sa.Integer(), nullable=False,
                            server_default="0"))
    op.add_column("month_perf_records",
                  sa.Column("settle_amount", sa.Integer(), nullable=False,
                            server_default="0"))
    op.add_column("month_perf_records",
                  sa.Column("diff_points", sa.Integer(), nullable=False,
                            server_default="0"))
    op.add_column("month_perf_records",
                  sa.Column("diff_amount", sa.Integer(), nullable=False,
                            server_default="0"))


def downgrade():
    for c in ("diff_amount", "diff_points", "settle_amount",
              "settle_points"):
        op.drop_column("month_perf_records", c)
