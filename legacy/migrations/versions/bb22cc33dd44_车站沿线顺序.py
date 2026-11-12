# -*- coding: utf-8 -*-
"""`bd_station` 加**沿线顺序**：seq / along_km / seq_src。

用户 2026-10-05："顺序起来你自己定一下就行…只是对于环线的顺序，你能算得准吗？"
    → "我觉得你应该还能找到其它的数据源来确定这个顺序，而不是计算出来。"
    → 决策 **A：用 OSM**（route relation 的成员本身有序，环线直接给一圈），许可 ODbL 已接受。
    → "直接在现有的车站表里加一列，后面我们查的时候就拿这列做 order 排序。"

口径（重要）：
- `seq` 来自 **OSM 的"运行系统线路"**（山手線 = 30 站；京葉線 = 18 站）。
- N02 官方口径把山手环拆成 `山手線`(17) + `東北線`(東京〜田端) + `東海道線`(東京〜品川)，
  所以**同一条 N02 线路内的 seq 可能不连续，但相对顺序正确**（排序只看相对大小）。
- `seq_src` 记录这条顺序照的是哪个 OSM 关系（如 `osm:JR山手線`），便于审计。
- 三列都可空/有默认值 → 纯 add_column，SQLite 不需要重建表。
"""
from alembic import op
import sqlalchemy as sa

revision = "bb22cc33dd44"
down_revision = "aa11bb22cc33"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bd_station", sa.Column("seq", sa.Integer(), nullable=True))
    op.add_column("bd_station", sa.Column("along_km", sa.Float(), nullable=True))
    op.add_column("bd_station", sa.Column(
        "seq_src", sa.String(length=64), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("bd_station", "seq_src")
    op.drop_column("bd_station", "along_km")
    op.drop_column("bd_station", "seq")
