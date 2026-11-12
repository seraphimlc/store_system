"""Bound work on visit writes and keep network I/O outside task-row locks."""
import importlib.util
import uuid

import pytest
from sqlalchemy import create_engine, event, inspect, text
from alembic.migration import MigrationContext
from alembic.operations import Operations

import app.db as appdb
from app.models import BdGridTask, BdRegionTask, BdStoreTask, User
from app.services import bd_hierarchical_tasks as tasks, bd_merchant_media as media
from tests_web.test_bd_hierarchical_tasks import batch
from tests_web.test_bd_zone_operations import seed, login  # noqa: F401
from tests_web.test_bd_merchant_media import photo


def test_visit_only_changes_current_branch_and_never_counts_batch_stores(seed):
    with appdb.SessionLocal() as db:
        main, region, grid, store = batch(db, seed)
        neighbour_region = BdRegionTask(main_task_id=main.id, region_id=region.region_id+1,
                                        snapshot={}, status='pending', revision=17)
        db.add(neighbour_region); db.flush()
        neighbour = BdGridTask(region_task_id=neighbour_region.id, grid_id=grid.grid_id+1,
                               snapshot={}, status='pending', total_stores=9,
                               completed_stores=0, revision=19)
        db.add(neighbour); db.flush()
        statements = []
        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement.lower())
        event.listen(db.bind, 'before_cursor_execute', capture)
        try:
            tasks.complete_store(db, db.get(User, seed['users']['worker']), store.id,
                                 store.revision, str(uuid.uuid4()), 'normal', '')
            db.flush()
        finally:
            event.remove(db.bind, 'before_cursor_execute', capture)
        assert (grid.total_stores, grid.completed_stores, grid.progress)==(1, 1, 100)
        assert region.progress==100 and main.progress==10 and main.status=='active'
        assert (neighbour.revision, neighbour_region.revision)==(19, 17)
        assert not any('count(' in sql and 'bd_store_task' in sql for sql in statements)


def test_map_view_skips_management_grids_but_manage_mode_loads_them(client, seed, monkeypatch):
    from app.services import bd_grid_regions
    calls = []
    original = bd_grid_regions.management_grids
    def tracked(*args, **kwargs):
        calls.append(kwargs.get('region'))
        return original(*args, **kwargs)
    monkeypatch.setattr(bd_grid_regions, 'management_grids', tracked)
    login(client, 'ops-admin')
    assert client.get('/zones?mode=assign').status_code==200
    assert calls==[]
    assert client.get('/zones?mode=manage').status_code==200
    assert calls==['tokyo23']


def test_grid_counter_migration_backfills_existing_and_empty_grids_and_reverts():
    spec = importlib.util.spec_from_file_location('p1_counts',
        'migrations/versions/c1d2e3f4a5b6_bd_grid_task_progress_counts.py')
    migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
    engine = create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE bd_grid_task (id INTEGER PRIMARY KEY)'))
        conn.execute(text('CREATE TABLE bd_store_task (id INTEGER PRIMARY KEY,grid_task_id INTEGER,status TEXT)'))
        conn.execute(text('INSERT INTO bd_grid_task VALUES (1),(2),(3)'))
        conn.execute(text("INSERT INTO bd_store_task VALUES (1,1,'completed'),(2,1,'pending'),(3,3,'completed')"))
        migration.op = Operations(MigrationContext.configure(conn))
        migration.upgrade()
        assert conn.execute(text('SELECT id,total_stores,completed_stores FROM bd_grid_task ORDER BY id')).all()==[
            (1,2,1),(2,0,0),(3,1,1)]
        migration.downgrade()
        assert [col['name'] for col in inspect(conn).get_columns('bd_grid_task')]==['id']
        assert conn.execute(text('SELECT COUNT(*) FROM bd_store_task')).scalar()==3


def test_upload_rechecks_assignment_after_network_without_completing_visit(seed, monkeypatch):
    with appdb.SessionLocal() as db:
        main, region, grid, store = batch(db, seed)
        worker = db.get(User, seed['users']['worker'])
        def change_assignment(*args):
            grid.assignee_id = seed['users']['next-worker']
            db.flush()
            return 'https://cdn.test/store-system-files/synthetic.jpg'
        monkeypatch.setattr(media, 'upload_photo', change_assignment)
        with pytest.raises(media.ops.OperationError, match='负责人'):
            media.save_visit(db, worker, store.id, store.revision, str(uuid.uuid4()),
                             'normal', '', 'completed', [], [photo()])
        assert store.status=='pending' and grid.completed_stores==0
