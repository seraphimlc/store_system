"""主任务→片区→网格→店铺四级巡店任务。"""
from alembic import op
import sqlalchemy as sa

revision = 'e0a1b2c3d4e5'
down_revision = 'd9f42a310bce'


def upgrade():
    op.create_table('bd_main_task',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('release_id', sa.Integer, sa.ForeignKey('bd_zone_release.id'), nullable=False),
        sa.Column('title', sa.String(160), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('progress', sa.Integer, nullable=False),
        sa.Column('created_by', sa.Integer, sa.ForeignKey('users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime, nullable=False),
        sa.Column('started_at', sa.DateTime), sa.Column('completed_at', sa.DateTime),
        sa.Column('revision', sa.Integer, nullable=False))
    op.create_index('ix_bd_main_task_release_id','bd_main_task',['release_id'])
    op.create_table('bd_region_task',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('main_task_id', sa.Integer, sa.ForeignKey('bd_main_task.id'), nullable=False),
        sa.Column('region_id', sa.Integer, sa.ForeignKey('bd_region.id'), nullable=False),
        sa.Column('snapshot', sa.JSON, nullable=False),
        sa.Column('team_id', sa.Integer, sa.ForeignKey('bd_team.id')),
        sa.Column('leader_id', sa.Integer, sa.ForeignKey('users.id')),
        sa.Column('status', sa.String(16), nullable=False), sa.Column('progress', sa.Integer, nullable=False),
        sa.Column('revision', sa.Integer, nullable=False), sa.Column('created_at', sa.DateTime, nullable=False),
        sa.UniqueConstraint('main_task_id','region_id',name='uq_bd_region_task_region'))
    op.create_index('ix_bd_region_task_main_task_id','bd_region_task',['main_task_id'])
    op.create_table('bd_grid_task',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('region_task_id', sa.Integer, sa.ForeignKey('bd_region_task.id'), nullable=False),
        sa.Column('grid_id', sa.Integer, sa.ForeignKey('bd_work_grid.id'), nullable=False),
        sa.Column('snapshot', sa.JSON, nullable=False),
        sa.Column('assignee_id', sa.Integer, sa.ForeignKey('users.id')),
        sa.Column('status', sa.String(16), nullable=False), sa.Column('progress', sa.Integer, nullable=False),
        sa.Column('revision', sa.Integer, nullable=False), sa.Column('created_at', sa.DateTime, nullable=False),
        sa.UniqueConstraint('region_task_id','grid_id',name='uq_bd_grid_task_grid'))
    op.create_index('ix_bd_grid_task_region_task_id','bd_grid_task',['region_task_id'])
    op.create_table('bd_store_task',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('grid_task_id', sa.Integer, sa.ForeignKey('bd_grid_task.id'), nullable=False),
        sa.Column('store_id', sa.String(128), nullable=False),
        sa.Column('snapshot', sa.JSON, nullable=False),
        sa.Column('status', sa.String(16), nullable=False), sa.Column('result', sa.JSON, nullable=False),
        sa.Column('actor_id', sa.Integer, sa.ForeignKey('users.id')),
        sa.Column('updated_at', sa.DateTime, nullable=False),
        sa.Column('revision', sa.Integer, nullable=False), sa.Column('request_id', sa.String(64), unique=True),
        sa.UniqueConstraint('grid_task_id','store_id',name='uq_bd_store_task_store'))
    op.create_index('ix_bd_store_task_grid_task_id','bd_store_task',['grid_task_id'])
    for table, columns in {'bd_region_task':['region_id','team_id','leader_id'],
                           'bd_grid_task':['grid_id','assignee_id'], 'bd_store_task':['store_id']}.items():
        for column in columns: op.create_index(f'ix_{table}_{column}',table,[column])


def downgrade():
    if op.get_bind().execute(sa.text('SELECT COUNT(*) FROM bd_main_task')).scalar():
        raise RuntimeError('已有巡店批次历史，禁止删除四级任务表')
    op.drop_table('bd_store_task'); op.drop_table('bd_grid_task'); op.drop_table('bd_region_task'); op.drop_table('bd_main_task')
