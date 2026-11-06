"""A visit batch may contain areas from the entire operational map.

The grid provenance remains on each grid/area/mapping; the batch itself
has no single source when its selected areas span regions.
"""
from alembic import op
import sqlalchemy as sa

revision = 'b5c6d7e8f901'
down_revision = 'e4f6071829ab'


def upgrade():
    sqlite = op.get_bind().dialect.name == 'sqlite'
    with op.batch_alter_table('bd_main_task', recreate='always' if sqlite else 'never') as batch:
        batch.drop_index('ix_bd_main_task_release_id')
        batch.drop_column('release_id')


def downgrade():
    bind = op.get_bind()
    # A batch spanning multiple sources cannot be expressed by the old schema.
    bad = bind.execute(sa.text('''SELECT t.id FROM bd_main_task t
        LEFT JOIN bd_region_task rt ON rt.main_task_id=t.id
        LEFT JOIN bd_region r ON r.id=rt.region_id
        GROUP BY t.id HAVING COUNT(DISTINCT r.release_id) != 1 LIMIT 1''')).first()
    if bad:
        raise RuntimeError('Cannot downgrade: a visit batch has no single source')
    op.add_column('bd_main_task', sa.Column('release_id', sa.Integer(), nullable=True))
    bind.execute(sa.text('''UPDATE bd_main_task SET release_id=(
        SELECT r.release_id FROM bd_region_task rt
        JOIN bd_region r ON r.id=rt.region_id
        WHERE rt.main_task_id=bd_main_task.id LIMIT 1)'''))
    sqlite = bind.dialect.name == 'sqlite'
    with op.batch_alter_table('bd_main_task', recreate='always' if sqlite else 'never') as batch:
        batch.alter_column('release_id', existing_type=sa.Integer(), nullable=False)
        batch.create_foreign_key('fk_bd_main_task_release_id', 'bd_zone_release', ['release_id'], ['id'])
        batch.create_index('ix_bd_main_task_release_id', ['release_id'])
