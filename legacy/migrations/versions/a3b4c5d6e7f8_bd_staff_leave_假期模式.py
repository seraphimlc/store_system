# -*- coding: utf-8 -*-
"""员工「假期模式」表 `bd_staff_leave`（用户 2026-10-03 要求）。

- 员工自己开/结束休假期；`end_date` 空 = 未定结束日（长期）
- 一人同时只有一条 `status='active'`
- **派工只看它做提醒，不强制约束**

只加一张表，不改任何现有表。
"""
from alembic import op
import sqlalchemy as sa

revision = "a3b4c5d6e7f8"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bd_staff_leave",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("person_code", sa.String(length=64), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False,
                  server_default="active"),
        sa.Column("created_by", sa.String(length=64), nullable=False,
                  server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_bd_staff_leave_person", "bd_staff_leave",
                    ["person_code", "start_date"])
    op.create_index("ix_bd_staff_leave_person_code", "bd_staff_leave",
                    ["person_code"])


def downgrade():
    op.drop_index("ix_bd_staff_leave_person_code", table_name="bd_staff_leave")
    op.drop_index("ix_bd_staff_leave_person", table_name="bd_staff_leave")
    op.drop_table("bd_staff_leave")
