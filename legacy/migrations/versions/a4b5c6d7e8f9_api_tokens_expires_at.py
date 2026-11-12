# -*- coding: utf-8 -*-
"""api_tokens 增加 expires_at：默认签发 90 天，管理员可签永久（NULL=永久）。spec §三。"""
from alembic import op
import sqlalchemy as sa

revision = "a4b5c6d7e8f9"
down_revision = "a7b8c9d0e1f2"


def upgrade():
    op.add_column("api_tokens",
                  sa.Column("expires_at", sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column("api_tokens", "expires_at")
