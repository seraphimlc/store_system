# -*- coding: utf-8 -*-
"""假期模式自动标出勤计划：`staff_date_plans.leave_id`。

用户 2026-10-03：
- 「可以标」→ 开假时把休假期内的出勤计划标成**不出勤（×）**
- 「员工的休假状态可以标识成日期区间……就可以在出勤计划里连续多天是叉」
- 结束/替换休假时按 `leave_id` **精确撤销**（不会抹掉员工自己点的 ×）

只加一列（可空），无回填：历史行 `leave_id` 为空 = 不是休假标的。
"""
from alembic import op
import sqlalchemy as sa

revision = "c5d6e7f8a9b0"
down_revision = "b4c5d6e7f8a9"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("staff_date_plans",
                  sa.Column("leave_id", sa.Integer(), nullable=True))


def downgrade():
    op.drop_column("staff_date_plans", "leave_id")
