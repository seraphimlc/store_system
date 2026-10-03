# -*- coding: utf-8 -*-
"""bd_task 加开始日 / 完成日（对应用户 Excel 的「开始日 / 完成日」两列）。

口径（用户 2026-10-03 决定）：**自动写** ——
- 首次提交进展 → `start_date` = 提交日
- 进展到 100% → `done_date` = 提交日
- 进度回退（<100）→ 清掉 `done_date`（保持与 state 自洽）

只加两列，不改任何现有业务表。
"""
from alembic import op
import sqlalchemy as sa

revision = "c1b2a3d4e5f6"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("bd_task", sa.Column("start_date", sa.Date(), nullable=True))
    op.add_column("bd_task", sa.Column("done_date", sa.Date(), nullable=True))


def downgrade():
    op.drop_column("bd_task", "done_date")
    op.drop_column("bd_task", "start_date")
