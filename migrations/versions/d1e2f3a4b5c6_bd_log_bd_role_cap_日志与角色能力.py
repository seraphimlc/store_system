# -*- coding: utf-8 -*-
"""作业域日志表 `bd_log` + 角色能力表 `bd_role_cap`。

用户 2026-10-03 要求：
- 「每个任务的变化日志；团队变化、团队成员变化、队长变化等，要记录日志」
  → **一张通用追加表**（domain 区分 task/team/member/station）
- 「权限先用 (a)」→ **角色能力表**（`(role, capability) → allowed`），判权与页面共用一份

只加两张表 + 种子角色能力行，不改任何现有表。
"""
from alembic import op
import sqlalchemy as sa

revision = "d1e2f3a4b5c6"
down_revision = "c1b2a3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bd_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("domain", sa.String(length=16), nullable=False),
        sa.Column("ref_id", sa.Integer(), nullable=True),
        sa.Column("ref_label", sa.String(length=128), nullable=False,
                  server_default=""),
        sa.Column("action", sa.String(length=24), nullable=False),
        sa.Column("field", sa.String(length=32), nullable=False,
                  server_default=""),
        sa.Column("old_value", sa.Text(), nullable=False, server_default=""),
        sa.Column("new_value", sa.Text(), nullable=False, server_default=""),
        sa.Column("actor", sa.String(length=64), nullable=False,
                  server_default=""),
        sa.Column("actor_name", sa.String(length=64), nullable=False,
                  server_default=""),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_bd_log_ref", "bd_log", ["domain", "ref_id"])
    op.create_index("ix_bd_log_created", "bd_log", ["created_at"])
    op.create_table(
        "bd_role_cap",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("capability", sa.String(length=32), nullable=False),
        sa.Column("allowed", sa.Boolean(), nullable=False,
                  server_default="0"),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("role", "capability", name="uq_bd_role_cap"),
    )
    # 种子：角色能力（与 app/services/bd_perm.py::DEFAULT_CAPS 保持一致）
    from datetime import datetime
    now = datetime.utcnow()
    rows = [
        ("admin", "task.view_all", True), ("admin", "task.view_team", True),
        ("admin", "task.dispatch", True), ("admin", "task.assign", True),
        ("admin", "task.report", True), ("admin", "task.adjust", True),
        ("admin", "team.manage", True), ("admin", "station.manage", True),
        ("admin", "log.view", True),
        ("leader", "task.view_all", False), ("leader", "task.view_team", True),
        ("leader", "task.dispatch", False), ("leader", "task.assign", True),
        ("leader", "task.report", True), ("leader", "task.adjust", True),
        ("leader", "team.manage", False), ("leader", "station.manage", False),
        ("leader", "log.view", True),
        ("staff", "task.view_all", False), ("staff", "task.view_team", False),
        ("staff", "task.dispatch", False), ("staff", "task.assign", False),
        ("staff", "task.report", True), ("staff", "task.adjust", False),
        ("staff", "team.manage", False), ("staff", "station.manage", False),
        ("staff", "log.view", False),
    ]
    op.bulk_insert(
        sa.table("bd_role_cap",
                 sa.column("role", sa.String), sa.column("capability", sa.String),
                 sa.column("allowed", sa.Boolean),
                 sa.column("note", sa.Text), sa.column("updated_at", sa.DateTime)),
        [{"role": r, "capability": c, "allowed": a, "note": "",
          "updated_at": now} for r, c, a in rows])


def downgrade():
    op.drop_table("bd_role_cap")
    op.drop_index("ix_bd_log_created", table_name="bd_log")
    op.drop_index("ix_bd_log_ref", table_name="bd_log")
    op.drop_table("bd_log")
