"""V3：正式表/确认任务建表；店铺主从/最早锚字段；raw 判定扩展

Revision ID: a9b8c7d6e5f4
Revises: f0e1d2c3b4a5
Create Date: 2026-09-08 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a9b8c7d6e5f4'
down_revision: Union[str, None] = 'f0e1d2c3b4a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('store_entities', sa.Column('master_store_id', sa.String(64),
                                              nullable=True))
    op.add_column('store_entities', sa.Column('raw_data_id', sa.Integer(),
                                              nullable=True))
    op.add_column('store_entities', sa.Column('master_raw_data_id',
                                              sa.Integer(), nullable=True))
    op.add_column('raw_records', sa.Column('filter_reason', sa.String(20),
                                           nullable=True))
    op.add_column('raw_records', sa.Column('confirm_state', sa.String(24),
                                           nullable=False,
                                           server_default='pending'))
    op.create_table(
        'confirm_tasks',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('import_id', sa.Integer(), sa.ForeignKey('imports.id'),
                  nullable=False),
        sa.Column('person_code', sa.String(32), sa.ForeignKey('persons.code'),
                  nullable=False),
        sa.Column('status', sa.String(16), nullable=False,
                  server_default='open'),
        sa.Column('due_at', sa.DateTime(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('import_id', 'person_code',
                            name='uq_confirmtask_imp_person'),
    )
    op.create_table(
        'formal_records',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('import_id', sa.Integer(), sa.ForeignKey('imports.id'),
                  nullable=False),
        sa.Column('raw_record_id', sa.Integer(),
                  sa.ForeignKey('raw_records.id'), nullable=False),
        sa.Column('person_code', sa.String(32), sa.ForeignKey('persons.code'),
                  nullable=True),
        sa.Column('store_id_raw', sa.Text(), nullable=False,
                  server_default=sa.text("('')")),
        sa.Column('japan_date', sa.Date(), nullable=True),
        sa.Column('points', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('raw_record_id', name='uq_formal_raw'),
    )


def downgrade() -> None:
    op.drop_table('formal_records')
    op.drop_table('confirm_tasks')
    op.drop_column('raw_records', 'confirm_state')
    op.drop_column('raw_records', 'filter_reason')
    op.drop_column('store_entities', 'master_raw_data_id')
    op.drop_column('store_entities', 'raw_data_id')
    op.drop_column('store_entities', 'master_store_id')
