# -*- coding: utf-8 -*-
"""payroll_period_rows 加 上半月奖金/下半月奖金 列（円）

分期金额 = 该期点数×250 + 该期奖金（满68点=3000）。
"""
import sqlalchemy as sa
from alembic import op

revision = "e3f4a5b6c7d8"
down_revision = "e2f3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("payroll_period_rows",
                  sa.Column("half1_bonus", sa.Integer(), nullable=False,
                            server_default="0"))
    op.add_column("payroll_period_rows",
                  sa.Column("half2_bonus", sa.Integer(), nullable=False,
                            server_default="0"))


def downgrade():
    op.drop_column("payroll_period_rows", "half2_bonus")
    op.drop_column("payroll_period_rows", "half1_bonus")
