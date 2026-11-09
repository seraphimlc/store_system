"""Cache exact store-task totals per grid to avoid whole-batch rescans."""
from alembic import op
import sqlalchemy as sa

revision = 'c1d2e3f4a5b6'
down_revision = 'b5c6d7e8f901'


def upgrade():
    op.add_column('bd_grid_task', sa.Column('total_stores', sa.Integer(), nullable=False,
                                             server_default=sa.text('0')))
    op.add_column('bd_grid_task', sa.Column('completed_stores', sa.Integer(), nullable=False,
                                             server_default=sa.text('0')))
    bind = op.get_bind()
    bind.execute(sa.text('''UPDATE bd_grid_task SET
        total_stores=counts.total,completed_stores=counts.completed
        FROM (SELECT grid_task_id,COUNT(*) AS total,
                     COUNT(*) FILTER (WHERE status='completed') AS completed
              FROM bd_store_task GROUP BY grid_task_id) counts
        WHERE bd_grid_task.id=counts.grid_task_id'''))


def downgrade():
    op.drop_column('bd_grid_task', 'completed_stores')
    op.drop_column('bd_grid_task', 'total_stores')
