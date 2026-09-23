# -*- coding: utf-8 -*-
"""sys_configs 增加 staff_visible_from（员工可见起始月，默认空=不限制）。"""
from alembic import op
import sqlalchemy as sa

revision = "f5c6d7e8f9a0"
down_revision = "f4c5d6e7f8a9"


def upgrade():
    op.add_column("sys_configs",
                  sa.Column("staff_visible_from", sa.String(7),
                            nullable=False, server_default=""))


def downgrade():
    op.drop_column("sys_configs", "staff_visible_from")
