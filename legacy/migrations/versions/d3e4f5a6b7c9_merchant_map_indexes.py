"""Viewport point lookup and recent unique-merchant visit lookup."""
from alembic import op
import sqlalchemy as sa

revision = 'd3e4f5a6b7c9'
down_revision = 'c2d3e4f5a6b8'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    bind.execute(sa.text('''CREATE INDEX IF NOT EXISTS ix_bd_task_event_visit_time
        ON bd_task_event (created_at,store_source_id)
        WHERE event_type IN ('complete_store_task','update_store_result')'''))
    bind.execute(sa.text('''CREATE INDEX IF NOT EXISTS ix_bd_task_event_visit_source
        ON bd_task_event (store_source_id,created_at)
        WHERE event_type IN ('complete_store_task','update_store_result')'''))
    if bind.dialect.name == 'postgresql' and sa.inspect(bind).has_table('kanto_merchants_v20261029', schema='public'):
        bind.execute(sa.text('''CREATE INDEX IF NOT EXISTS ix_merchant_map_location
            ON public.kanto_merchants_v20261029 USING gist (point(lon,lat))'''))


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        bind.execute(sa.text('DROP INDEX IF EXISTS public.ix_merchant_map_location'))
    bind.execute(sa.text('DROP INDEX IF EXISTS ix_bd_task_event_visit_source'))
    bind.execute(sa.text('DROP INDEX IF EXISTS ix_bd_task_event_visit_time'))
