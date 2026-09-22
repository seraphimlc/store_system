# -*- coding: utf-8 -*-
"""dash_metrics 看板统计物化表（月份-统计项-数值）。"""
from alembic import op
import sqlalchemy as sa

revision = "f4c5d6e7f8a9"
down_revision = "f3c4d5e6f7a8"

def upgrade():
    op.create_table(
        "dash_metrics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("month", sa.String(7), nullable=False),
        sa.Column("metric", sa.String(64), nullable=False),
        sa.Column("value", sa.Float(), nullable=False, server_default="0"),
        sa.Column("person", sa.String(32), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("month", "metric", "person",
                            name="uq_dash_metric"),
    )
    op.create_index("ix_dash_metrics_month", "dash_metrics", ["month"])

def downgrade():
    op.drop_table("dash_metrics")
