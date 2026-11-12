# -*- coding: utf-8 -*-
"""车站数据资产化：新增**物理车站层** `bd_station_place` + `bd_station.place_id`。

用户 2026-10-05："你把车站的数据好好整理一下，以后有更大的用处…车站数据可以当成我们的
数据资产。也是任务的输入源之一。"

三层结构（本次补齐中间那层的"上家"）：

    线路 bd_line ──1:N──> 站×线 bd_station ──N:1──> **物理车站 bd_station_place**

- 分层键 = N02_005g 駅グループコード（MLIT 官方"同一车站"分组；实测 1,920 → 1,568，
  237 个跨线站，同组站名 100% 一致、坐标 100% 在 1km 内）
- ⚠️ **任务仍挂在 `bd_station`**：本迁移**不改任务行为**；将来要"一个物理车站一个任务"时，
  按届时定的规则把任务指到 place（`bd_task.source_type` 已为此留好）
- SQLite 只需 add_column（不需要重建表：只是加一列 + 建新表）
"""
from alembic import op
import sqlalchemy as sa

revision = "aa11bb22cc33"
down_revision = "f9a8b7c6d5e4"
branch_labels = None
depends_on = None


def _tables():
    return set(sa.inspect(op.get_bind()).get_table_names())


def _cols(table):
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    if "bd_station_place" not in _tables():
        op.create_table(
            "bd_station_place",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(64), nullable=False),
            sa.Column("name_norm", sa.String(64), nullable=False),
            sa.Column("pref", sa.String(8), nullable=False, server_default=""),
            sa.Column("city", sa.String(64), nullable=False, server_default=""),
            sa.Column("lon", sa.Float(), nullable=True),
            sa.Column("lat", sa.Float(), nullable=True),
            sa.Column("group_code", sa.String(16), nullable=False, server_default=""),
            sa.Column("n_line", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("n_operator", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("operators", sa.String(128), nullable=False, server_default=""),
            sa.Column("lines_text", sa.Text(), nullable=False, server_default=""),
            sa.Column("source", sa.String(16), nullable=False, server_default="mlit"),
            sa.Column("note", sa.Text(), nullable=False, server_default=""),
            sa.Column("status", sa.String(16), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        bind = op.get_bind()
        if bind.dialect.name != "mysql":          # MySQL 不支持部分唯一索引
            op.create_index("uq_bd_place_group", "bd_station_place", ["group_code"],
                            unique=True,
                            sqlite_where=sa.text("group_code != ''"),
                            postgresql_where=sa.text("group_code != ''"))
        op.create_index("ix_bd_place_pref", "bd_station_place", ["pref"])
        op.create_index("ix_bd_place_name_norm", "bd_station_place", ["name_norm"])
        op.create_index("ix_bd_place_city", "bd_station_place", ["city"])

    if "place_id" not in _cols("bd_station"):
        # ⚠️ SQLite 不支持用 ALTER 加**带外键**的列（"No support for ALTER of constraints"）
        # → 必须走 batch（SQLite 重建表 / PG 普通 ALTER），与上一个迁移同一套路
        # ⚠️ batch 模式下**外键必须显式命名**（否则 "Constraint must have a name"）
        with op.batch_alter_table("bd_station") as b:
            b.add_column(sa.Column("place_id", sa.Integer(), nullable=True))
            b.create_foreign_key("fk_bd_station_place", "bd_station_place",
                                 ["place_id"], ["id"])
        op.create_index("ix_bd_station_place_id", "bd_station", ["place_id"])


def downgrade():
    if "ix_bd_station_place_id" in {i["name"] for i in
                                    sa.inspect(op.get_bind()).get_indexes("bd_station")}:
        op.drop_index("ix_bd_station_place_id", table_name="bd_station")
    op.drop_column("bd_station", "place_id")
    op.drop_table("bd_station_place")
