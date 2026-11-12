# -*- coding: utf-8 -*-
"""raw_records.visible_raw / deploy_raw 去掉长度上限（String(8) → Text）。

**为什么要改**（2026-10-17）：Visible 列在真实文件里会出现
`AUDIT_SUCCESS`（13 字符）、`AUDIT_FAILED`、`NOT_REQUEST`、`AUDITING` 等值
（`store_settle/loader.py` 明确接受这些，`rules.visible_is_candidate` 按布局
value_map 判定候选）。线上 PG 这两列**本来就是 text**（历史库不是按迁移建的，
最长 13 字符已存 7.4 万行）所以一直没暴露；但**按迁移新建的库**会得到
`varchar(8)` → 一导入就整批失败（PG 直接报 value too long / MySQL 严格模式
报 Data too long，或被静默截断）——本地 MySQL、灾难重建、新机都会踩。

所以：ORM 与迁移统一改成 `Text`。对线上是 no-op（本来就是 text），
对新建库才是真正修复。
"""
from alembic import op
import sqlalchemy as sa

revision = "f0a1b2c3d4e5"
down_revision = "ab12cd34ef56"

_COLS = ("visible_raw", "deploy_raw")


def _alter(col: str, to_text: bool):
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # SQLite 不支持 ALTER COLUMN TYPE，只能批量重建表（本地库本来不跑 alembic，
        # 这里是为了 tests_web/test_migrations.py 的临时库能真跑一遍）。
        with op.batch_alter_table("raw_records") as b:
            b.alter_column(col, type_=sa.Text() if to_text
                           else sa.String(length=8), existing_nullable=True)
    else:
        op.alter_column("raw_records", col,
                        type_=sa.Text() if to_text else sa.String(length=8),
                        existing_nullable=True,
                        existing_type=sa.String(length=8) if to_text else sa.Text())


def upgrade():
    for c in _COLS:
        _alter(c, to_text=True)


def downgrade():
    # ⚠️ 降级会失败：库里已经有 13 字符的 AUDIT_SUCCESS 之类值，
    # varchar(8) 装不下。真降级要先清理数据（或把这两列改回它原来的宽度）。
    for c in _COLS:
        _alter(c, to_text=False)
