# -*- coding: utf-8 -*-
"""staff_analyses 员工月度分析表（算完工资后自动生成）。"""
from alembic import op
import sqlalchemy as sa

revision = "f3c4d5e6f7a8"
down_revision = "f2c3d4e5f6a7"

def upgrade():
    op.create_table(
        "staff_analyses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("person_code", sa.String(32), nullable=False),
        sa.Column("month", sa.String(7), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("person_code", "month",
                            name="uq_staff_analysis_month"),
    )
    op.create_index("ix_staff_analyses_person_code", "staff_analyses",
                    ["person_code"])
    op.create_index("ix_staff_analyses_month", "staff_analyses", ["month"])

def downgrade():
    op.drop_table("staff_analyses")
