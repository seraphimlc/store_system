# -*- coding: utf-8 -*-
"""站内消息（`bd_message` + `bd_message_recipient`）+ 进展审核字段。

用户 2026-10-03：
- 员工上报进展 → 队长**确认**或**调整** → 结果要**发消息通知**员工
- 「消息模块」：一条消息 + N 个收件人、按人已读

对 `bd_task_progress` 加 6 列（**作业域自己的表**，不动结算域）：
`reported_pct` / `reported_by` / `review_status` / `reviewed_by` / `reviewed_at` / `review_note`。
"""
from alembic import op
import sqlalchemy as sa

revision = "b4c5d6e7f8a9"
down_revision = "a3b4c5d6e7f8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bd_message",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sender_kind", sa.String(length=16), nullable=False,
                  server_default="system"),
        sa.Column("sender", sa.String(length=64), nullable=False,
                  server_default=""),
        sa.Column("sender_name", sa.String(length=64), nullable=False,
                  server_default=""),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("url", sa.String(length=255), nullable=False,
                  server_default=""),
        sa.Column("scope", sa.String(length=24), nullable=False,
                  server_default="manual"),
        sa.Column("ref_type", sa.String(length=24), nullable=False,
                  server_default=""),
        sa.Column("ref_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_bd_message_created", "bd_message", ["created_at"])
    op.create_table(
        "bd_message_recipient",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("message_id", sa.Integer(),
                  sa.ForeignKey("bd_message.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("person_code", sa.String(length=64), nullable=False),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("message_id", "person_code",
                            name="uq_bd_message_recipient"),
    )
    op.create_index("ix_bd_message_recipient_person", "bd_message_recipient",
                    ["person_code", "read_at"])
    # 进展审核字段
    op.add_column("bd_task_progress",
                  sa.Column("reported_pct", sa.Integer(), nullable=True))
    op.add_column("bd_task_progress",
                  sa.Column("reported_by", sa.String(length=32), nullable=False,
                            server_default=""))
    op.add_column("bd_task_progress",
                  sa.Column("review_status", sa.String(length=16),
                            nullable=False, server_default="pending"))
    op.add_column("bd_task_progress",
                  sa.Column("reviewed_by", sa.String(length=32), nullable=False,
                            server_default=""))
    op.add_column("bd_task_progress",
                  sa.Column("reviewed_at", sa.DateTime(), nullable=True))
    op.add_column("bd_task_progress",
                  sa.Column("review_note", sa.Text(), nullable=False,
                            server_default=""))


def downgrade():
    for col in ("review_note", "reviewed_at", "reviewed_by", "review_status",
                "reported_by", "reported_pct"):
        op.drop_column("bd_task_progress", col)
    op.drop_index("ix_bd_message_recipient_person",
                  table_name="bd_message_recipient")
    op.drop_table("bd_message_recipient")
    op.drop_index("ix_bd_message_created", table_name="bd_message")
    op.drop_table("bd_message")
