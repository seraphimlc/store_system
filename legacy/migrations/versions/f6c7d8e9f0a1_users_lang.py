# -*- coding: utf-8 -*-
"""users 增加 lang（界面语言 zh/ja/空=自动）。"""
from alembic import op
import sqlalchemy as sa

revision = "f6c7d8e9f0a1"
down_revision = "f5c6d7e8f9a0"


def upgrade():
    op.add_column("users",
                  sa.Column("lang", sa.String(8), nullable=False,
                            server_default=""))


def downgrade():
    op.drop_column("users", "lang")
