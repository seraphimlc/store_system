"""raw_records 加判定列：clean_status + filtered_by_raw_id（追溯主锚）

Revision ID: f0e1d2c3b4a5
Revises: c5d4e3f2a1b0
Create Date: 2026-09-08 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f0e1d2c3b4a5'
down_revision: Union[str, None] = 'c5d4e3f2a1b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 判定状态：final/dup_by_id/dup_by_name/visible_blank/manual_void
    op.add_column('raw_records', sa.Column('clean_status', sa.String(16),
                                           nullable=True))
    # 被哪条有效记录挤掉（指向 raw_records.id）；仅 dup 行有值
    op.add_column('raw_records', sa.Column('filtered_by_raw_id', sa.Integer(),
                                           nullable=True))


def downgrade() -> None:
    op.drop_column('raw_records', 'filtered_by_raw_id')
    op.drop_column('raw_records', 'clean_status')
