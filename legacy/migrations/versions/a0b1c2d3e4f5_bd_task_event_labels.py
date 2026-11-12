"""snapshot readable batch hierarchy labels on task events"""
from alembic import op
import sqlalchemy as sa

revision = 'a0b1c2d3e4f5'
down_revision = 'f8a9b0c1d2e3'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('bd_task_event') as batch:
        batch.add_column(sa.Column('main_label', sa.String(160), nullable=False, server_default=''))
        batch.add_column(sa.Column('region_label', sa.String(128), nullable=False, server_default=''))
        batch.add_column(sa.Column('grid_label', sa.String(128), nullable=False, server_default=''))
        batch.add_column(sa.Column('store_label', sa.String(300), nullable=False, server_default=''))
        batch.add_column(sa.Column('store_source_id', sa.String(128), nullable=False, server_default=''))


def downgrade():
    with op.batch_alter_table('bd_task_event') as batch:
        batch.drop_column('store_source_id')
        batch.drop_column('store_label')
        batch.drop_column('grid_label')
        batch.drop_column('region_label')
        batch.drop_column('main_label')
