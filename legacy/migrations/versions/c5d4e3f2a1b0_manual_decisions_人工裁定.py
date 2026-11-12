"""manual_decisions 人工裁定表 + bucket manual_void 支持

Revision ID: c5d4e3f2a1b0
Revises: a1b2c3d4e5f6
Create Date: 2026-09-07 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5d4e3f2a1b0'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'manual_decisions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('import_id', sa.Integer(), sa.ForeignKey('imports.id'),
                  nullable=False),
        sa.Column('sheet_name', sa.String(255), nullable=False, server_default=sa.text("('')")),
        sa.Column('excel_row', sa.Integer(), nullable=False),
        sa.Column('decision', sa.String(16), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'),
                  nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('import_id', 'sheet_name', 'excel_row',
                            name='uq_manual_import_row'),
    )
    # person_stats 增加 manual_void 计数（原 final 被人工作废的行数）
    op.add_column('person_stats', sa.Column('manual_void', sa.Integer(),
                                            nullable=False,
                                            server_default='0'))
    # clean_records.bucket 是字符串，历史数据无 manual_void → 无需改列


def downgrade() -> None:
    op.drop_column('person_stats', 'manual_void')
    op.drop_table('manual_decisions')
