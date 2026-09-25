# -*- coding: utf-8 -*-
"""找平回收明细：发放 ↔ 找平 多对多关联（双向可查）。"""
from alembic import op
import sqlalchemy as sa

revision = "a7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"


def upgrade():
    op.create_table(
        "payroll_settlement_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("adjust_id", sa.Integer(), sa.ForeignKey("payroll_adjusts.id"), nullable=False),
        sa.Column("payment_id", sa.Integer(), sa.ForeignKey("payroll_payments.id"), nullable=False),
        sa.Column("month", sa.String(7), nullable=False),
        sa.Column("person_code", sa.String(32), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("adjust_id", "payment_id", name="uq_link_adjust_payment"),
    )
    for c in ("adjust_id", "payment_id", "month", "person_code"):
        op.create_index(f"ix_links_{c}", "payroll_settlement_links", [c])


def downgrade():
    for c in ("adjust_id", "payment_id", "month", "person_code"):
        op.drop_index(f"ix_links_{c}", table_name="payroll_settlement_links")
    op.drop_table("payroll_settlement_links")
