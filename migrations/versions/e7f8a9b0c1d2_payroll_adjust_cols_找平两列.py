# -*- coding: utf-8 -*-
"""payroll_period_rows 加 找平两列（adjust_points/adjust_amount）

- 偏差点数/偏差金额 = 系统按公式算的参考值（每次生成自动刷新）；
- 找平点数/找平金额 = 管理员人工确认执行的值（默认 0，保存后保留；
  金额=点数×该月锁存单价）；
- 上月修正递延来源 = 上月的"找平点数"（人工执行值）。
"""
import sqlalchemy as sa
from alembic import op

revision = "e7f8a9b0c1d2"
down_revision = "e6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("payroll_period_rows",
                  sa.Column("adjust_points", sa.Integer(), nullable=False,
                            server_default="0"))
    op.add_column("payroll_period_rows",
                  sa.Column("adjust_amount", sa.Integer(), nullable=False,
                            server_default="0"))


def downgrade():
    op.drop_column("payroll_period_rows", "adjust_amount")
    op.drop_column("payroll_period_rows", "adjust_points")
