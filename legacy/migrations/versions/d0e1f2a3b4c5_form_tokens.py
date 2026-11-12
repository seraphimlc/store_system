# -*- coding: utf-8 -*-
"""一次性提交令牌表 form_tokens（防重复提交）。"""
from alembic import op
import sqlalchemy as sa

revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"


def upgrade():
    op.create_table(
        "form_tokens",
        sa.Column("token", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"),
                  nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_form_tokens_user_id", "form_tokens", ["user_id"])
    op.create_index("ix_form_tokens_created_at", "form_tokens", ["created_at"])


def downgrade():
    op.drop_index("ix_form_tokens_created_at", table_name="form_tokens")
    op.drop_index("ix_form_tokens_user_id", table_name="form_tokens")
    op.drop_table("form_tokens")
