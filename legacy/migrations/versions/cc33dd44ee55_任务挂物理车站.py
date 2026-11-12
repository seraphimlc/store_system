# -*- coding: utf-8 -*-
"""任务挂到**物理车站**：`bd_task.place_id` + `station_id` 改可空。

用户 2026-10-05 定稿："应该是 18 个任务…因为每个站对应一个任务，都有自己的状态。
我分给 A 队的 10 个站，并不是他这 10 个站作为一个整体任务跑完我再分新的任务，
而是在剩下几个站的时候，我就可以再派发新的一组任务给他。"
→ **1 个物理车站 = 1 个任务**（跨线站只 1 个），派活滚动进行。

要点：
- `UNIQUE(place_id)` 用**部分唯一索引**（`place_id IS NOT NULL` 上唯一）；
  SQLite/PG 都支持，MySQL 跳过（靠服务层）。
- `station_id` 由 NOT NULL 改**可空**（老口径留空即可）；`UNIQUE(station_id)` 对 NULL 不生效，
  所以"老行有 station_id / 新行只有 place_id"能共存。
- 本迁移时 `bd_task` 是空的（用户已清空旧任务），所以 SQLite 的表重建代价为零。
"""
from alembic import op
import sqlalchemy as sa

revision = "cc33dd44ee55"
down_revision = "bb22cc33dd44"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("bd_task") as b:
        b.alter_column("station_id", existing_type=sa.Integer(), nullable=True)
        b.add_column(sa.Column("place_id", sa.Integer(), nullable=True))
        b.create_foreign_key("fk_bd_task_place", "bd_station_place", ["place_id"], ["id"])
        b.create_index("uq_bd_task_place", ["place_id"], unique=True,
                       sqlite_where=sa.text("place_id IS NOT NULL"),
                       postgresql_where=sa.text("place_id IS NOT NULL"))


def downgrade() -> None:
    with op.batch_alter_table("bd_task") as b:
        b.drop_index("uq_bd_task_place")
        b.drop_constraint("fk_bd_task_place", type_="foreignkey")
        b.drop_column("place_id")
        b.alter_column("station_id", existing_type=sa.Integer(), nullable=False)
