"""adjust_records 找平表

Revision ID: 0f3a9c7e2b51
Revises: 5fff74d79368
Create Date: 2026-09-08
"""
from alembic import op
import sqlalchemy as sa

revision: str = '0f3a9c7e2b51'
down_revision = '5fff74d79368'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'adjust_records',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('month', sa.String(7), nullable=False),
        sa.Column('applied_to_month', sa.String(7), nullable=False),
        sa.Column('person_code', sa.String(32), nullable=False),
        sa.Column('amount', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('reason', sa.Text(), nullable=False, server_default=sa.text("('')")),
        sa.Column('source_task_id', sa.Integer(), nullable=False),
        sa.Column('source_points_diff', sa.Integer(), nullable=False,
                  server_default='0'),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('source_task_id', 'person_code',
                            name='uq_adjust_task_person'),
    )


def downgrade() -> None:
    op.drop_table('adjust_records')
