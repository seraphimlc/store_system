# -*- coding: utf-8 -*-
"""sys_configs 系统配置表（每点金额/达标点数/达标奖金，按月生效）。"""
from alembic import op
import sqlalchemy as sa

revision = "f2c3d4e5f6a7"
down_revision = "f1a2b3c4d5e6"

def upgrade():
    op.create_table(
        "sys_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("config_month", sa.String(7), nullable=False, unique=True),
        sa.Column("per_point", sa.Integer(), nullable=False, server_default="250"),
        sa.Column("bonus_group", sa.Integer(), nullable=False, server_default="68"),
        sa.Column("bonus_amount", sa.Integer(), nullable=False, server_default="3000"),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.execute("INSERT INTO sys_configs (config_month, per_point, bonus_group, bonus_amount, updated_at) VALUES "
               "('2026-08', 250, 68, 3000, now()), ('2026-09', 250, 75, 1250, now())")

def downgrade():
    op.drop_table("sys_configs")
