# -*- coding: utf-8 -*-
"""WorkBuddy P1：mcp_audit_log（两阶段审计）+ sealed_months（封账，预置 2026-08）
+ rebuild_snapshots（重算前正式表快照）。"""
from alembic import op
import sqlalchemy as sa

revision = "b2c3d4e5f6a8"
down_revision = "a1b2c3d4e5f7"


def upgrade():
    op.create_table(
        "mcp_audit_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("tool", sa.String(64), nullable=True),
        sa.Column("params_json", sa.Text(), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=True),
        sa.Column("error_code", sa.String(32), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("client_info", sa.String(255), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_mcp_audit_log_created_at", "mcp_audit_log", ["created_at"])
    op.create_index("ix_mcp_audit_log_tool", "mcp_audit_log", ["tool"])

    op.create_table(
        "sealed_months",
        sa.Column("month", sa.String(7), primary_key=True),
        sa.Column("note", sa.String(255), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    # 预置 8 月封账：AGENTS.md「8 月封账数据不随规则变更」
    op.execute(
        "INSERT INTO sealed_months (month, note, created_at) "
        "VALUES ('2026-08', '8月封账（历史演示口径）', CURRENT_TIMESTAMP)"
    )

    op.create_table(
        "rebuild_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("month", sa.String(7), nullable=False),
        sa.Column("audit_id", sa.Integer(), nullable=True),   # 不加 FK
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_rebuild_snapshots_month", "rebuild_snapshots", ["month"])


def downgrade():
    op.drop_index("ix_rebuild_snapshots_month", table_name="rebuild_snapshots")
    op.drop_table("rebuild_snapshots")
    op.drop_table("sealed_months")
    op.drop_index("ix_mcp_audit_log_tool", table_name="mcp_audit_log")
    op.drop_index("ix_mcp_audit_log_created_at", table_name="mcp_audit_log")
    op.drop_table("mcp_audit_log")
