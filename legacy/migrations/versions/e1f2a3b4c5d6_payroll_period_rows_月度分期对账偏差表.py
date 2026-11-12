# -*- coding: utf-8 -*-
"""payroll_period_rows 月度分期对账偏差表

月 × 员工：上半月(1-15)/下半月(16-月末)/对账点数/上月修正/本月对账偏差(可手改)。
"""
import sqlalchemy as sa
from alembic import op

revision = "e1f2a3b4c5d6"
down_revision = "d7b0a4c6e9f2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "payroll_period_rows",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("month", sa.String(7), nullable=False),
        sa.Column("person_code", sa.String(32), nullable=False),
        sa.Column("half1_points", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("half2_points", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("settle_points", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("prev_adjust_points", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("diff_points", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("month", "person_code",
                            name="uq_period_month_code"),
    )


def downgrade():
    op.drop_table("payroll_period_rows")
