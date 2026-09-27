# -*- coding: utf-8 -*-
"""找平表：一行=一笔找平（月×人），进度（已找平/剩余/结清）为存储事实。"""
from alembic import op
import sqlalchemy as sa

revision = "f6a7b8c9d0e1"
down_revision = "b1c2d3e4f5a6"


def upgrade():
    op.create_table(
        "payroll_adjusts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_month", sa.String(7), nullable=False),
        sa.Column("person_code", sa.String(32), nullable=False),
        sa.Column("source_task_id", sa.Integer(), nullable=True),
        sa.Column("source_row_id", sa.Integer(), nullable=True),
        sa.Column("adjust_amount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("settled_amount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("remaining", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(12), nullable=False, server_default="in_progress"),
        sa.Column("settled_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("source_month", "person_code", name="uq_adjust_person"),
    )
    op.create_index("ix_payroll_adjusts_month", "payroll_adjusts", ["source_month"])
    op.create_index("ix_payroll_adjusts_person", "payroll_adjusts", ["person_code"])


def downgrade():
    op.drop_index("ix_payroll_adjusts_person", table_name="payroll_adjusts")
    op.drop_index("ix_payroll_adjusts_month", table_name="payroll_adjusts")
    op.drop_table("payroll_adjusts")
