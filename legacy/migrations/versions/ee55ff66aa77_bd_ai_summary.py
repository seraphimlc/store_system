# -*- coding: utf-8 -*-
"""bd_ai_summary（任务域 AI 总结缓存：一天一条）

用户 2026-10-06："任务AI总结。过去7天的总结…生成一次缓存当天，点开即看"

Revision ID: ee55ff66aa77
Revises: dd44ee55ff66
"""
from alembic import op
import sqlalchemy as sa

revision = "ee55ff66aa77"
down_revision = "dd44ee55ff66"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bd_ai_summary",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("summary_date", sa.Date(), nullable=False),
        sa.Column("days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("metrics_json", sa.Text(), nullable=False, server_default=""),
        sa.Column("summary_json", sa.Text(), nullable=False, server_default=""),
        sa.Column("model", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("summary_date", name="uq_bd_ai_summary_day"),
    )


def downgrade():
    op.drop_table("bd_ai_summary")
