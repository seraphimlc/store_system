# -*- coding: utf-8 -*-
"""month_perf_records 月绩效工资记录表

入统计表/月度重算时物化（每员工每月一条），绩效工资页只查本表。
"""
import sqlalchemy as sa
from alembic import op

revision = "e4f5a6b7c8d9"
down_revision = "e3f4a5b6c7d8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "month_perf_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("month", sa.String(7), nullable=False),
        sa.Column("person_code", sa.String(32), nullable=False),
        sa.Column("records", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("p1", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("p2", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("points", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("salary", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("rate37", sa.Float(), nullable=False, server_default="0"),
        sa.Column("pass37", sa.Boolean(), nullable=False,
                  server_default="false"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("month", "person_code",
                            name="uq_mpf_month_code"),
    )


def downgrade():
    op.drop_table("month_perf_records")
