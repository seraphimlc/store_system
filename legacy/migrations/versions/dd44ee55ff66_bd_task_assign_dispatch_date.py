# -*- coding: utf-8 -*-
"""bd_task_assign.dispatch_date（队长每天派工）

用户 2026-10-06 口径：队长**每天**给队员派当天的任务；**昨天未完成自动延续**
（不新增行，靠查询口径：今天派的 ∪ 未完成的）。本字段记录"哪一天派的"。

Revision ID: dd44ee55ff66
Revises: cc33dd44ee55
"""
from alembic import op
import sqlalchemy as sa

revision = "dd44ee55ff66"
down_revision = "cc33dd44ee55"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("bd_task_assign",
                  sa.Column("dispatch_date", sa.Date(), nullable=True))
    op.create_index("ix_bd_task_assign_dispatch_date", "bd_task_assign",
                    ["dispatch_date"])


def downgrade():
    op.drop_index("ix_bd_task_assign_dispatch_date", table_name="bd_task_assign")
    op.drop_column("bd_task_assign", "dispatch_date")
