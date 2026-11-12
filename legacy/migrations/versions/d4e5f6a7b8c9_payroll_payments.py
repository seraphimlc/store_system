# -*- coding: utf-8 -*-
"""发放台账：记录每次实际发薪（月×人×期），供找平吸收计算使用。"""
from alembic import op
import sqlalchemy as sa

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"


def upgrade():
    op.create_table(
        "payroll_payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("month", sa.String(7), nullable=False),
        sa.Column("person_code", sa.String(32), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("amount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bonus", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("adjust_applied", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("note", sa.String(255), nullable=True),
        sa.Column("paid_at", sa.DateTime(), nullable=False),
        sa.Column("paid_by", sa.Integer(), nullable=True),
        sa.UniqueConstraint("month", "person_code", "seq", name="uq_payment_inst"),
    )
    op.create_index("ix_payroll_payments_month", "payroll_payments", ["month"])
    op.create_index("ix_payroll_payments_person", "payroll_payments", ["person_code"])


def downgrade():
    op.drop_index("ix_payroll_payments_person", table_name="payroll_payments")
    op.drop_index("ix_payroll_payments_month", table_name="payroll_payments")
    op.drop_table("payroll_payments")
