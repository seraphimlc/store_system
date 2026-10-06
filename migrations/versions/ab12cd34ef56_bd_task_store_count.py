# -*- coding: utf-8 -*-
"""bd_task.store_count：完成时要填的**店铺数**（用户 2026-10-06）

口径：队员自报做到 100% 时**必填**（允许 0）；队长批量补录可留空。
⚠️ 系统算不出来 —— `bd_store` 3 万家店的 `place_id`/经纬度实测全空（0/30681）。

Revision ID: ab12cd34ef56
Revises: ee55ff66aa77
"""
from alembic import op
import sqlalchemy as sa

revision = "ab12cd34ef56"
down_revision = "ee55ff66aa77"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("bd_task", sa.Column("store_count", sa.Integer(), nullable=True))


def downgrade():
    op.drop_column("bd_task", "store_count")
