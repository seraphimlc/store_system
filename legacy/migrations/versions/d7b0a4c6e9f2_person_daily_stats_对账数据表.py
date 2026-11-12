"""person_daily_stats 统计表 + recon_data_rows 对账数据表

Revision ID: d7b0a4c6e9f2
Revises: 4c9d1e8f7a6b
Create Date: 2026-09-09
"""
from alembic import op
import sqlalchemy as sa

revision: str = 'd7b0a4c6e9f2'
down_revision = '4c9d1e8f7a6b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'person_daily_stats',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('person_code', sa.String(32), nullable=False),
        sa.Column('ref_date', sa.Date(), nullable=False),
        sa.Column('records', sa.Integer(), nullable=False,
                  server_default='0'),
        sa.Column('p1', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('p2', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('points', sa.Integer(), nullable=False,
                  server_default='0'),
        sa.UniqueConstraint('person_code', 'ref_date',
                            name='uq_pdstat_person_date'),
    )
    op.create_index('ix_pdstat_date', 'person_daily_stats', ['ref_date'])
    op.create_table(
        'recon_data_rows',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('task_id', sa.Integer(), nullable=False),
        sa.Column('ref_date', sa.Date(), nullable=False),
        sa.Column('person_code', sa.String(32), nullable=False),
        sa.Column('person_name', sa.String(64), nullable=False,
                  server_default=sa.text("('')")),
        sa.Column('points', sa.Integer(), nullable=False,
                  server_default='0'),
        sa.Column('cnt', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('task_id', 'ref_date', 'person_code',
                            name='uq_recon_data_task_date_person'),
    )
    op.create_index('ix_recon_data_task', 'recon_data_rows', ['task_id'])


def downgrade() -> None:
    op.drop_table('recon_data_rows')
    op.drop_table('person_daily_stats')
