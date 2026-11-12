"""users.status 员工状态（在岗/请假/停用/离职）

Revision ID: 9f3a1c7d2b01
Revises: d209d71a9b99
Create Date: 2026-09-07 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9f3a1c7d2b01'
down_revision: Union[str, None] = 'd209d71a9b99'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('status', sa.String(16),
                                     nullable=False, server_default='active'))
    # 历史数据：is_active=False 的员工视为停用/离职，其余在岗
    op.execute("UPDATE users SET status = CASE WHEN is_active THEN 'active' ELSE 'disabled' END")


def downgrade() -> None:
    op.drop_column('users', 'status')
