"""users.must_change_password 首登/重置后强制改密

Revision ID: a1b2c3d4e5f6
Revises: 9f3a1c7d2b01
Create Date: 2026-09-07 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '9f3a1c7d2b01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('must_change_password', sa.Boolean(),
                                     nullable=False, server_default=sa.text('false')))
    # 存量员工默认口令即 demo123 → 一律要求首登改密（管理员不受限）
    op.execute("UPDATE users SET must_change_password = true"
               " WHERE role = 'staff' AND status IN ('active', 'leave')")


def downgrade() -> None:
    op.drop_column('users', 'must_change_password')
