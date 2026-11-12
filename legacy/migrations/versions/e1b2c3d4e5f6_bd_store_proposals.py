"""新店待核实申请及私有照片。"""
from alembic import op
import sqlalchemy as sa
revision='e1b2c3d4e5f6'
down_revision='e0a1b2c3d4e5'


def upgrade():
    op.create_table('bd_store_proposal',
        sa.Column('id',sa.Integer,primary_key=True),
        sa.Column('grid_task_id',sa.Integer,sa.ForeignKey('bd_grid_task.id'),nullable=False),
        sa.Column('submitted_by',sa.Integer,sa.ForeignKey('users.id'),nullable=False),
        sa.Column('request_id',sa.String(64),nullable=False,unique=True),
        sa.Column('payload',sa.JSON,nullable=False),
        sa.Column('state',sa.String(16),nullable=False),sa.Column('revision',sa.Integer,nullable=False),
        sa.Column('reviewed_by',sa.Integer,sa.ForeignKey('users.id')),
        sa.Column('reviewed_at',sa.DateTime),sa.Column('reason',sa.String(500),nullable=False),
        sa.Column('store_id',sa.String(128)),sa.Column('created_at',sa.DateTime,nullable=False))
    op.create_index('ix_bd_store_proposal_grid_task_id','bd_store_proposal',['grid_task_id'])


def downgrade():
    op.drop_table('bd_store_proposal')
