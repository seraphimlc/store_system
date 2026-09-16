# -*- coding: utf-8 -*-
"""payroll_period_rows 加金额列（円）

点数列已有；新增：上半月/下半月实发金额(手填)、对账金额(自动=工资规则×对账点数)、
上月修正金额(递延)、本月对账偏差金额(=对账金额−(上半月+下半月)+上月修正，可手改)。
"""
import sqlalchemy as sa
from alembic import op

revision = "e2f3a4b5c6d7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("payroll_period_rows",
                  sa.Column("half1_amount", sa.Integer(), nullable=False,
                            server_default="0"))
    op.add_column("payroll_period_rows",
                  sa.Column("half2_amount", sa.Integer(), nullable=False,
                            server_default="0"))
    op.add_column("payroll_period_rows",
                  sa.Column("settle_amount", sa.Integer(), nullable=False,
                            server_default="0"))
    op.add_column("payroll_period_rows",
                  sa.Column("prev_adjust_amount", sa.Integer(),
                            nullable=False, server_default="0"))
    op.add_column("payroll_period_rows",
                  sa.Column("diff_amount", sa.Integer(), nullable=False,
                            server_default="0"))


def downgrade():
    for c in ("diff_amount", "prev_adjust_amount", "settle_amount",
              "half2_amount", "half1_amount"):
        op.drop_column("payroll_period_rows", c)
