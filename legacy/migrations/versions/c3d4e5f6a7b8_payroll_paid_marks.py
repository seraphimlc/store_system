# -*- coding: utf-8 -*-
"""发薪标记表：按月×期记录「已实际发薪」，供找平吸收额度计算。"""
from alembic import op
import sqlalchemy as sa

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a8"


def upgrade():
    op.create_table(
        "payroll_paid_marks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("month", sa.String(7), nullable=False),
        sa.Column("half", sa.Integer(), nullable=False),
        sa.Column("marked_by", sa.Integer(), nullable=True),
        sa.Column("marked_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("month", "half", name="uq_paid_mark"),
    )
    op.create_index("ix_payroll_paid_marks_month", "payroll_paid_marks", ["month"])


def downgrade():
    op.drop_index("ix_payroll_paid_marks_month", table_name="payroll_paid_marks")
    op.drop_table("payroll_paid_marks")
