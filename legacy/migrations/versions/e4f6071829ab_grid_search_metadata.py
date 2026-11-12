"""Grid names, station arrays and landmark keyword arrays (operations domain only)."""
from alembic import op
import sqlalchemy as sa

revision = 'e4f6071829ab'
down_revision = 'd3e4f5a6b7c9'


def upgrade():
    op.add_column('bd_work_grid', sa.Column('name', sa.String(128), nullable=False, server_default=''))
    op.add_column('bd_work_grid', sa.Column('stations', sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
    op.add_column('bd_work_grid', sa.Column('keywords', sa.JSON(), nullable=False, server_default=sa.text("'[]'")))


def downgrade():
    with op.batch_alter_table('bd_work_grid') as batch:
        for name in ('keywords', 'stations', 'name'):
            batch.drop_column(name)
