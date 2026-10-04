# -*- coding: utf-8 -*-
"""一都三県「线路 + 车站」全量入池（用户 2026-10-03："把一都三县所有的地铁线和车站都收集进来"）。

- **新增 `bd_line`**：线路主档（数据来源 国土数値情報 N02 → `scripts/bd_kanto_rail.json`）
- **`bd_station` 改「一线一站」**：唯一键 从 `name_norm` 改成 `(line_id, name_norm)`
  （同名车站跨线时先各存一行；合并规则以后再说），并加 `line_id/operator/pref/lon/lat/
  ekicode/group_code/source`（`group_code` = N02_005g，将来合并同一车站用）
- **`bd_task` 加 `source_type`**：任务来源判别（现在恒为 `station`，将来片区 = `zone`）

⚠️ SQLite 不能直接 drop 唯一约束 → `batch_alter_table(recreate="always")` 重建表
（SQLite/PG 都能跑；MySQL 跳过两个部分唯一索引，靠服务层判重）。
"""
from alembic import op
import sqlalchemy as sa

revision = "f9a8b7c6d5e4"
down_revision = "d6e7f8a9b0c1"
branch_labels = None
depends_on = None


def _cols(bind, table):
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def _idx(bind, table):
    return {i["name"] for i in sa.inspect(bind).get_indexes(table)}


def apply(op, bind):
    """真正的迁移逻辑（**alembic 与本地库脚本共用这一份**，避免两套 DDL 漂移）。

    本地库不跑 alembic（`alembic_version` 为空、表是 create_all 建的），
    `scripts/bd_migrate_rail_local.py` 会用 `alembic.operations.Operations`
    包一层后直接调用这里 —— 与生产路径**完全同一段代码**。
    """
    mysql = bind.dialect.name == "mysql"

    # ① 线路主档
    if "bd_line" not in sa.inspect(bind).get_table_names():
        op.create_table(
            "bd_line",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(64), nullable=False),
            sa.Column("name_norm", sa.String(64), nullable=False),
            sa.Column("operator", sa.String(64), nullable=False),
            sa.Column("operator_short", sa.String(32), nullable=False,
                      server_default=""),
            sa.Column("kind", sa.String(16), nullable=False, server_default=""),
            sa.Column("prefs", sa.String(32), nullable=False, server_default=""),
            sa.Column("n_station", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("source", sa.String(16), nullable=False, server_default="mlit"),
            sa.Column("note", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("operator", "name", name="uq_bd_line_op_name"),
        )
        op.create_index("ix_bd_line_kind", "bd_line", ["kind"])
        op.create_index("ix_bd_line_name_norm", "bd_line", ["name_norm"])

    # ② 车站：加列 + 换唯一键（SQLite 需要重建表）
    have = _cols(bind, "bd_station")
    with op.batch_alter_table("bd_station", recreate="always") as b:
        if "line_id" not in have:
            b.add_column(sa.Column("line_id", sa.Integer(), nullable=True))
        if "operator" not in have:
            b.add_column(sa.Column("operator", sa.String(64), nullable=False,
                                   server_default=""))
        if "pref" not in have:
            b.add_column(sa.Column("pref", sa.String(8), nullable=False,
                                   server_default=""))
        if "lon" not in have:
            b.add_column(sa.Column("lon", sa.Float(), nullable=True))
        if "lat" not in have:
            b.add_column(sa.Column("lat", sa.Float(), nullable=True))
        if "ekicode" not in have:
            b.add_column(sa.Column("ekicode", sa.String(16), nullable=False,
                                   server_default=""))
        if "group_code" not in have:
            b.add_column(sa.Column("group_code", sa.String(16), nullable=False,
                                   server_default=""))
        if "source" not in have:
            b.add_column(sa.Column("source", sa.String(16), nullable=False,
                                   server_default="manual"))
        if "uq_bd_station_name" in {c["name"] for c in
                                    sa.inspect(bind).get_unique_constraints("bd_station")}:
            b.drop_constraint("uq_bd_station_name", type_="unique")

    insp_idx = _idx(bind, "bd_station")
    if "ix_bd_station_line" not in insp_idx:
        op.create_index("ix_bd_station_line", "bd_station", ["line"])
    if "ix_bd_station_line_id" not in insp_idx:
        op.create_index("ix_bd_station_line_id", "bd_station", ["line_id"])
    if "ix_bd_station_pref" not in insp_idx:
        op.create_index("ix_bd_station_pref", "bd_station", ["pref"])
    if "ix_bd_station_group_code" not in insp_idx:
        op.create_index("ix_bd_station_group_code", "bd_station", ["group_code"])
    if not mysql:
        if "uq_bd_station_line_name" not in insp_idx:
            op.create_index("uq_bd_station_line_name", "bd_station",
                            ["line_id", "name_norm"], unique=True,
                            sqlite_where=sa.text("line_id IS NOT NULL"),
                            postgresql_where=sa.text("line_id IS NOT NULL"))
        if "uq_bd_station_noline_name" not in insp_idx:
            op.create_index("uq_bd_station_noline_name", "bd_station",
                            ["name_norm"], unique=True,
                            sqlite_where=sa.text("line_id IS NULL"),
                            postgresql_where=sa.text("line_id IS NULL"))

    # ③ 任务来源判别
    if "source_type" not in _cols(bind, "bd_task"):
        op.add_column("bd_task", sa.Column(
            "source_type", sa.String(16), nullable=False,
            server_default="station"))


def upgrade():
    apply(op, op.get_bind())


def downgrade():
    bind = op.get_bind()
    op.drop_column("bd_task", "source_type")
    for name in ("uq_bd_station_noline_name", "uq_bd_station_line_name",
                 "ix_bd_station_group_code", "ix_bd_station_pref",
                 "ix_bd_station_line_id"):
        if name in _idx(bind, "bd_station"):
            op.drop_index(name, table_name="bd_station")
    with op.batch_alter_table("bd_station", recreate="always") as b:
        for col in ("source", "group_code", "ekicode", "lat", "lon", "pref",
                    "operator", "line_id"):
            b.drop_column(col)
        b.create_unique_constraint("uq_bd_station_name", ["name_norm"])
    op.drop_index("ix_bd_line_name_norm", table_name="bd_line")
    op.drop_index("ix_bd_line_kind", table_name="bd_line")
    op.drop_table("bd_line")
