# -*- coding: utf-8 -*-
"""团队 + 车站任务：bd_team / bd_team_member / bd_station / bd_task /
bd_task_assign / bd_task_progress。

设计 `docs/specs-team-management.md`（团队）与 `docs/specs-station-tasks.md`
（车站任务）。**只加表，不改任何现有业务表**（尤其不碰结算域四张表）。

- `bd_team` / `bd_team_member`：团队与成员（含历史：end_date）
- `bd_station`：车站主数据（一个站 = 一片区域）
- `bd_task`：任务 = 一个车站（UNIQUE(station_id)），带分配日期与进展百分比
- `bd_task_assign`：担当（1~2 人，上限在服务层）
- `bd_task_progress`：每日进展提交（UNIQUE(task_id, progress_date)）
"""
from alembic import op
import sqlalchemy as sa

revision = "b2c3d4e5f6a7"
down_revision = "d3e4f5a6b7c8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bd_team",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=True),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False,
                  server_default="active"),
        sa.Column("created_by", sa.String(length=64), nullable=False,
                  server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("name", name="uq_bd_team_name"),
        sa.UniqueConstraint("code", name="uq_bd_team_code"),
    )
    op.create_table(
        "bd_team_member",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("bd_team.id"),
                  nullable=False),
        sa.Column("person_code", sa.String(length=32),
                  sa.ForeignKey("persons.code"), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False,
                  server_default="member"),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("team_id", "person_code", "start_date",
                            name="uq_bd_team_member"),
    )
    op.create_index("ix_bd_team_member_person", "bd_team_member",
                    ["person_code"])
    op.create_table(
        "bd_station",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("name_norm", sa.String(length=64), nullable=False),
        sa.Column("line", sa.String(length=64), nullable=False,
                  server_default=""),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False,
                  server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("name_norm", name="uq_bd_station_name"),
    )
    op.create_index("ix_bd_station_line", "bd_station", ["line"])
    op.create_table(
        "bd_task",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("station_id", sa.Integer(), sa.ForeignKey("bd_station.id"),
                  nullable=False),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("bd_team.id"),
                  nullable=True),
        sa.Column("assign_date", sa.Date(), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False,
                  server_default="unassigned"),
        sa.Column("pct", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(length=64), nullable=False,
                  server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("station_id", name="uq_bd_task_station"),
    )
    op.create_index("ix_bd_task_team", "bd_task", ["team_id"])
    op.create_index("ix_bd_task_assign_date", "bd_task", ["assign_date"])
    op.create_index("ix_bd_task_state", "bd_task", ["state"])
    op.create_table(
        "bd_task_assign",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("bd_task.id"),
                  nullable=False),
        sa.Column("person_code", sa.String(length=32),
                  sa.ForeignKey("persons.code"), nullable=False),
        sa.Column("assigned_by", sa.String(length=64), nullable=False,
                  server_default=""),
        sa.Column("assigned_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("task_id", "person_code", name="uq_bd_task_assign"),
    )
    op.create_index("ix_bd_task_assign_person", "bd_task_assign",
                    ["person_code"])
    op.create_table(
        "bd_task_progress",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("bd_task.id"),
                  nullable=False),
        sa.Column("progress_date", sa.Date(), nullable=False),
        sa.Column("pct", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("submitted_by", sa.String(length=32), nullable=False,
                  server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("task_id", "progress_date",
                            name="uq_bd_task_progress"),
    )
    op.create_index("ix_bd_task_progress_date", "bd_task_progress",
                    ["progress_date"])


def downgrade():
    op.drop_index("ix_bd_task_progress_date", table_name="bd_task_progress")
    op.drop_table("bd_task_progress")
    op.drop_index("ix_bd_task_assign_person", table_name="bd_task_assign")
    op.drop_table("bd_task_assign")
    op.drop_index("ix_bd_task_state", table_name="bd_task")
    op.drop_index("ix_bd_task_assign_date", table_name="bd_task")
    op.drop_index("ix_bd_task_team", table_name="bd_task")
    op.drop_table("bd_task")
    op.drop_index("ix_bd_station_line", table_name="bd_station")
    op.drop_table("bd_station")
    op.drop_index("ix_bd_team_member_person", table_name="bd_team_member")
    op.drop_table("bd_team_member")
    op.drop_table("bd_team")
