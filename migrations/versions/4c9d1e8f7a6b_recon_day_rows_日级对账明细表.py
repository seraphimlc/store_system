"""recon_day_rows 日级对账明细表

Revision ID: 4c9d1e8f7a6b
Revises: 0f3a9c7e2b51
Create Date: 2026-09-09
"""
from alembic import op
import sqlalchemy as sa

revision: str = '4c9d1e8f7a6b'
down_revision = '0f3a9c7e2b51'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'recon_day_rows',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('task_id', sa.Integer(), nullable=False),
        sa.Column('ref_date', sa.Date(), nullable=False),
        sa.Column('person_code', sa.String(32), nullable=False),
        sa.Column('sys_points', sa.Integer(), nullable=False,
                  server_default='0'),
        sa.Column('rep_points', sa.Integer(), nullable=False,
                  server_default='0'),
        sa.Column('sys_count', sa.Integer(), nullable=False,
                  server_default='0'),
        sa.Column('rep_count', sa.Integer(), nullable=False,
                  server_default='0'),
        sa.Column('diff', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('side', sa.String(12), nullable=False,
                  server_default='both'),
        sa.Column('note', sa.Text(), nullable=False, server_default=sa.text("('')")),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('task_id', 'ref_date', 'person_code',
                            name='uq_recon_day_task_date_person'),
    )
    op.create_index('ix_recon_day_task', 'recon_day_rows', ['task_id'])


def downgrade() -> None:
    op.drop_table('recon_day_rows')
