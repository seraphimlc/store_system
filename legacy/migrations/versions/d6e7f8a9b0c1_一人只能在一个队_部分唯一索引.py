# -*- coding: utf-8 -*-
"""把「一个队员只能在一个队」钉成**数据库级硬约束**（用户 2026-10-03："这是死规定"）。

部分唯一索引：`bd_team_member.person_code` 在 **`end_date IS NULL`（现役）** 的行上唯一。
- 转队 = 原队 `end_date` 收口 + 新队插一行 → 不冲突（历史行保留，可追溯）
- 任何代码路径 / 手工 SQL 想让人同时在两个队 → **直接 IntegrityError**
- SQLite（≥3.8）与 PostgreSQL 都支持 partial index；MySQL 不支持 → 该 dialect 下不建
  （MySQL 部署时会跳过，服务层判重仍然生效）

⚠️ 上线前该表是空的（团队功能未上线），所以建索引不会撞到历史数据。
"""
from alembic import op
import sqlalchemy as sa

revision = "d6e7f8a9b0c1"
down_revision = "c5d6e7f8a9b0"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "mysql":
        return                      # MySQL 不支持部分唯一索引
    op.create_index("uq_bd_team_member_active_person", "bd_team_member",
                    ["person_code"], unique=True,
                    sqlite_where=sa.text("end_date IS NULL"),
                    postgresql_where=sa.text("end_date IS NULL"))


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "mysql":
        return
    op.drop_index("uq_bd_team_member_active_person",
                  table_name="bd_team_member")
