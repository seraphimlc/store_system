# -*- coding: utf-8 -*-
"""adjust_records 加 amount_adj 列：确认对账时写入薪资找平的金额增量(円)。

金额找平语义（2026-09 修正）：找平按金额（含奖金差）修正，不按点数；
取消时按 amount_adj 精确回滚薪资找平执行值。
"""
import sqlalchemy as sa
from alembic import op

revision = "e9f0a1b2c3d4"
down_revision = "e8f9a0b1c2d3"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("adjust_records",
                  sa.Column("amount_adj", sa.Integer(), nullable=False,
                            server_default="0"))


def downgrade():
    op.drop_column("adjust_records", "amount_adj")