"""Ops merchant photos and Google Place ID, separate from source identity."""
from alembic import op
import sqlalchemy as sa

revision = 'c2d3e4f5a6b8'
down_revision = 'b1c2d3e4f5a7'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('bd_merchant_detail',
        sa.Column('store_id', sa.String(128), primary_key=True),
        sa.Column('lat', sa.Float(), nullable=True),
        sa.Column('lon', sa.Float(), nullable=True),
        sa.Column('google_place_id', sa.String(255), nullable=True),
        sa.Column('place_lookup_state', sa.String(24), nullable=False),
        sa.Column('photo_urls', sa.JSON(), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False))


def downgrade():
    op.drop_table('bd_merchant_detail')
