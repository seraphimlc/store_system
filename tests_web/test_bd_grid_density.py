"""当前商户坐标落格、阈值与重跑守门；只使用隔离库。"""
import json

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from shapely.geometry import Point, box

import app.db as appdb
from app.models import BdGridStore, BdWorkGrid, BdZone, User
from app.services import bd_grid_density as density, bd_grid_regions as regions
from app.services import bd_zone_merchants as merchants, bd_zone_operations as ops, bd_zones as z
from tests_web.test_bd_zone_operations import seed  # noqa: F401


@pytest.fixture
def source_db(monkeypatch):
    engine = create_engine('sqlite://', connect_args={'check_same_thread':False},poolclass=StaticPool)
    with engine.begin() as c:
        c.execute(text('CREATE TABLE merchant_source (place_id TEXT, name TEXT, lon FLOAT, lat FLOAT)'))
    monkeypatch.setattr(merchants, 'SOURCE_TABLE', 'merchant_source')
    yield engine
    engine.dispose()


def refresh(db, user, **kwargs):
    return density.refresh(db,user,source_table='merchant_source',**kwargs)


def rows(engine, left=4, right=5):
    values = []
    for side, count in enumerate((left, right)):
        for i in range(count):
            lon, lat = z.geographic(Point(side * 1000 + 100 + i, 500))['coordinates']
            values.append(dict(id=f'{side}-{i}', lon=lon, lat=lat))
    with engine.begin() as c:
        c.exec_driver_sql('PRAGMA query_only=OFF')
        c.execute(text('DELETE FROM merchant_source'))
        c.execute(text("INSERT INTO merchant_source VALUES (:id, 'shop', :lon, :lat)"), values)


def grids(db, seed):
    left = db.get(BdWorkGrid, seed['grid'])
    left.geometry = z.geographic(box(0, 0, 1000, 1000))
    left.area_km2 = 1
    right = BdWorkGrid(release_id=left.release_id, zone_id=left.zone_id, source_id='right',
                       geometry=z.geographic(box(1000, 0, 2000, 1000)), area_km2=1,
                       store_count=0, state='ready')
    db.add(right)
    db.commit()
    return left, right


def test_preview_apply_and_replacement_are_repeatable(client, seed, source_db):
    rows(source_db)
    with appdb.SessionLocal() as db:
        left, right = grids(db, seed)
        admin = db.get(User, seed['users']['ops-admin'])
        geometry = json.loads(json.dumps(left.geometry))
        report = refresh(db, admin, source_engine=source_db)
        assert (report['disabled_count'], report['enabled_count'], report['mapped_stores']) == (1, 1, 9)
        assert left.state == 'ready' and left.store_count == 1
        assert db.query(BdGridStore).count() == 1 and not db.dirty
        refresh(db, admin, source_engine=source_db, apply=True)
        db.commit()
        assert left.state == 'disabled' and left.zone_id is None and left.store_count == 4
        assert right.state == 'ready' and right.store_count == 5
        assert left.geometry == geometry
        parent = db.get(BdZone, seed['zone'])
        assert parent.store_count == 5 and parent.area_km2 == pytest.approx(1)
        assert z.projected(parent.geometry).equals(z.projected(right.geometry))
        assert db.query(BdGridStore).count() == 9
        revisions = (left.revision, right.revision, parent.revision)
        refresh(db, admin, source_engine=source_db, apply=True)
        db.commit()
        assert revisions == (left.revision, right.revision, parent.revision)
        disabled = next(row for row in regions.management_grids(db, admin) if row['id'] == left.id)
        assert disabled['disabled'] and disabled['locked']
        with pytest.raises(ops.OperationError, match='禁用网格'):
            regions.selection_plan(db, admin, [dict(id=left.id, revision=left.revision, zone_id=None)])
        rows(source_db, left=5, right=4)
        refresh(db, admin, source_engine=source_db, apply=True)
        db.commit()
        assert left.state == 'ready' and left.store_count == 5 and left.zone_id is None
        assert right.state == 'disabled' and right.zone_id is None
        assert parent.state == 'archived' and parent.area_km2 == 0 and parent.store_count == 0
        assert db.query(BdGridStore).count() == 9


def test_zero_invalid_coordinates_and_boundary_have_one_owner(client, seed, source_db):
    rows(source_db, left=1, right=1)
    lon, lat = z.geographic(Point(1000, 500))['coordinates']
    with source_db.begin() as c:
        c.execute(text("INSERT INTO merchant_source VALUES ('edge', 'shop', :lon, :lat)"), dict(lon=lon, lat=lat))
        c.execute(text("INSERT INTO merchant_source VALUES ('bad', 'shop', NULL, NULL)"))
    with appdb.SessionLocal() as db:
        grids(db, seed)
        admin = db.get(User, seed['users']['ops-admin'])
        report = refresh(db, admin, source_engine=source_db, apply=True)
        db.commit()
        assert report['mapped_stores'] == 3 and report['invalid_coordinates'] == 1
        assert report['disabled_count'] == 2
        assert db.query(BdGridStore).filter_by(source_id='edge').count() == 1
        with source_db.begin() as c:
            c.exec_driver_sql('PRAGMA query_only=OFF')
            c.execute(text('DELETE FROM merchant_source'))
        report = refresh(db, admin, source_engine=source_db, apply=True)
        db.commit()
        assert report['mapped_stores'] == 0 and report['disabled_count'] == 2
        assert db.query(BdGridStore).count() == 0


def test_active_work_blocks_writes_but_allows_preview(client, seed, source_db):
    rows(source_db)
    with appdb.SessionLocal() as db:
        left, right = grids(db, seed)
        left.assignee_id = seed['users']['worker']
        db.commit()
        admin = db.get(User, seed['users']['ops-admin'])
        assert refresh(db, admin, source_engine=source_db)['blocked_by_work']
        with pytest.raises(ops.OperationError, match='已派发'):
            refresh(db, admin, source_engine=source_db, apply=True)
        assert left.state == 'ready' and right.store_count == 0


def test_duplicate_source_aborts_without_mutation(client, seed, source_db):
    rows(source_db)
    with source_db.begin() as c:
        c.execute(text("INSERT INTO merchant_source SELECT * FROM merchant_source WHERE place_id='0-0'"))
    with appdb.SessionLocal() as db:
        left, right = grids(db, seed)
        admin = db.get(User, seed['users']['ops-admin'])
        with pytest.raises(ops.OperationError, match='重复标识'):
            refresh(db, admin, source_engine=source_db, apply=True)
        assert left.state == 'ready' and db.query(BdGridStore).count() == 1


def test_disabled_grid_is_excluded_from_new_batches(client, seed):
    from app.services.bd_hierarchical_tasks import create_main_task
    with appdb.SessionLocal() as db:
        grid = db.get(BdWorkGrid, seed['grid'])
        grid.state = 'disabled'
        db.commit()
        admin = db.get(User, seed['users']['ops-admin'])
        with pytest.raises(ops.OperationError, match='空片区'):
            create_main_task(db, admin, 'density-test', [seed['zone']])


def test_cli_help_and_database_guard():
    import os
    import subprocess
    import sys
    from pathlib import Path
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'bd_refresh_grid_density.py'
    help_result = subprocess.run([sys.executable, str(script), '--help'], capture_output=True, text=True)
    assert help_result.returncode == 0 and '--apply' in help_result.stdout
    env = dict(os.environ)
    env.pop('DATABASE_URL', None)
    result = subprocess.run([sys.executable, str(script), '--admin-user', 'admin'],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 2 and '必须显式设置DATABASE_URL' in result.stderr


def test_automatic_partition_routes_and_page_entry_are_removed(client, seed):
    from app.main import app
    assert not {'/zones/grid-auto-preview', '/zones/grid-auto-apply'} & {
        route.path for route in app.routes
    }
    client.post('/login', data={'username':'ops-admin', 'password':'pw123456'})
    page = client.get('/zones?mode=manage&region=tokyo23')
    assert page.status_code == 200
    assert 'grid-region-form' in page.text
    assert 'bd_grid_auto.js' not in page.text and 'grid-auto-open' not in page.text
