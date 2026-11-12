"""Record store search diagnostics for nightly data completeness analysis."""
from alembic import op
import sqlalchemy as sa

revision = 'd8e9f0a1b2c3'
down_revision = 'd7e8f9a0b1c2'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('bd_store_search_log',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('actor_id', sa.Integer(), nullable=True),
        sa.Column('main_task_id', sa.Integer(), nullable=False),
        sa.Column('region_task_id', sa.Integer(), nullable=False),
        sa.Column('grid_task_id', sa.Integer(), nullable=False),
        sa.Column('query_text', sa.String(200), nullable=False, server_default=''),
        sa.Column('page', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('task_state', sa.String(16), nullable=False, server_default=''),
        sa.Column('business_status', sa.String(16), nullable=False, server_default=''),
        sa.Column('used_location', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('total_in_grid', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('matched_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('keyword_match_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('normalized_match_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('missing_name_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('missing_address_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('missing_source_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('missing_location_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('duration_ms', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('no_result_reason', sa.String(24), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_bd_store_search_log_created_at','bd_store_search_log',['created_at'])
    op.create_index('ix_bd_store_search_log_grid_time','bd_store_search_log',['grid_task_id','created_at'])

def downgrade():
    op.drop_table('bd_store_search_log')
