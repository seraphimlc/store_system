# -*- coding: utf-8 -*-
"""imports 表加 layout 列（解析布局：AI 识别 + 人工可纠正）。"""
from alembic import op
import sqlalchemy as sa

revision = "f1a2b3c4d5e6"
down_revision = "e9f0a1b2c3d4"  # 当前 head（amount_adj 迁移之后）

def upgrade():
    op.add_column("imports", sa.Column("layout", sa.JSON(), nullable=True))

def downgrade():
    op.drop_column("imports", "layout")
