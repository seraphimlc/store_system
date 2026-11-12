"""append-only structured history for hierarchical batch tasks"""
from alembic import op
import sqlalchemy as sa

revision = 'f8a9b0c1d2e3'
down_revision = 'e1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'bd_task_event',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('main_task_id', sa.Integer(), nullable=False),
        sa.Column('region_task_id', sa.Integer(), nullable=True),
        sa.Column('grid_task_id', sa.Integer(), nullable=True),
        sa.Column('store_task_id', sa.Integer(), nullable=True),
        sa.Column('event_type', sa.String(40), nullable=False),
        sa.Column('actor_id', sa.Integer(), nullable=True),
        sa.Column('actor_label', sa.String(128), nullable=False, server_default=''),
        sa.Column('actor_role', sa.String(16), nullable=False, server_default=''),
        sa.Column('team_id', sa.Integer(), nullable=True),
        sa.Column('team_label', sa.String(128), nullable=False, server_default=''),
        sa.Column('request_id', sa.String(64), nullable=True, unique=True),
        sa.Column('old_value', sa.JSON(), nullable=False),
        sa.Column('new_value', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    for suffix, columns in (
        ('main_time', ['main_task_id', 'created_at']),
        ('region_time', ['region_task_id', 'created_at']),
        ('grid_time', ['grid_task_id', 'created_at']),
        ('store_time', ['store_task_id', 'created_at']),
        ('actor_time', ['actor_id', 'created_at']),
        ('team_time', ['team_id', 'created_at']),
    ):
        op.create_index(f'ix_bd_task_event_{suffix}', 'bd_task_event', columns)


def downgrade():
    op.drop_table('bd_task_event')
