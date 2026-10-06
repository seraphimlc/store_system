# -*- coding: utf-8 -*-
"""bd_area / bd_store：BD 作业域地基（行政区划基底 + 门店宇宙）。

设计 `docs/specs-bd-ops-layer.md` §五。
**只加表，不改任何现有业务表**（尤其不碰结算域四张表）。

- `bd_area`：都道府県 / 市区町村 / 町丁目（官方编码，只读同步）
- `bd_store`：门店宇宙（`store_key` 取既有 `raw_records.store_id_raw`）
"""
from alembic import op
import sqlalchemy as sa

revision = "d3e4f5a6b7c8"
down_revision = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bd_area",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("level", sa.String(length=8), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False,
                  server_default=""),
        sa.Column("name_kana", sa.String(length=128), nullable=False,
                  server_default=""),
        sa.Column("parent_code", sa.String(length=16), nullable=True),
        sa.Column("pref_code", sa.String(length=4), nullable=False,
                  server_default=""),
        sa.Column("city_code", sa.String(length=8), nullable=False,
                  server_default=""),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lng", sa.Float(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("level", "code", name="uq_bd_area_level_code"),
    )
    op.create_index("ix_bd_area_parent", "bd_area", ["parent_code"])

    op.create_table(
        "bd_store",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_key", sa.String(length=64), nullable=False),
        sa.Column("name_raw", sa.Text(), nullable=False,
                  server_default=sa.text("('')")),
        sa.Column("name_norm", sa.Text(), nullable=False,
                  server_default=sa.text("('')")),
        sa.Column("address", sa.Text(), nullable=False,
                  server_default=sa.text("('')")),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lng", sa.Float(), nullable=True),
        sa.Column("area_code", sa.String(length=16), nullable=True),
        sa.Column("zone_id", sa.Integer(), nullable=True),
        sa.Column("place_id", sa.String(length=64), nullable=True),
        sa.Column("gyotai", sa.String(length=64), nullable=False,
                  server_default=""),
        sa.Column("first_seen_person_code", sa.String(length=32), nullable=True),
        sa.Column("first_seen_date", sa.Date(), nullable=True),
        sa.Column("last_visit_date", sa.Date(), nullable=True),
        sa.Column("visit_count", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("source", sa.String(length=16), nullable=False,
                  server_default="import"),
        sa.Column("status", sa.String(length=16), nullable=False,
                  server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("store_key", name="uq_bd_store_key"),
    )
    op.create_index("ix_bd_store_area", "bd_store", ["area_code"])
    op.create_index("ix_bd_store_zone", "bd_store", ["zone_id"])


def downgrade():
    op.drop_index("ix_bd_store_zone", table_name="bd_store")
    op.drop_index("ix_bd_store_area", table_name="bd_store")
    op.drop_table("bd_store")
    op.drop_index("ix_bd_area_parent", table_name="bd_area")
    op.drop_table("bd_area")
