# -*- coding: utf-8 -*-
"""发放台账找平抵扣溯源列：抵扣必须指向来源记录，否则无法对账排查。"""
from alembic import op
import sqlalchemy as sa

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"


def upgrade():
    op.add_column("payroll_payments", sa.Column("adjust_source_type", sa.String(12), nullable=True))
    op.add_column("payroll_payments", sa.Column("adjust_source_month", sa.String(7), nullable=True))
    op.add_column("payroll_payments", sa.Column("adjust_source_row_id", sa.Integer(), nullable=True))
    op.add_column("payroll_payments", sa.Column("adjust_source_task_id", sa.Integer(), nullable=True))
    op.add_column("payroll_payments", sa.Column("adjust_leftover", sa.Integer(), nullable=True))


def downgrade():
    for c in ("adjust_leftover", "adjust_source_task_id", "adjust_source_row_id",
              "adjust_source_month", "adjust_source_type"):
        op.drop_column("payroll_payments", c)
