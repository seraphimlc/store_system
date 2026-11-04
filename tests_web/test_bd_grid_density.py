"""当前商户坐标落格、阈值、重跑与归组守门；只使用隔离库。"""
import json

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from shapely.geometry import Point, box

import app.db as appdb
from app.models import BdGridStore, BdWorkGrid, BdZone, User
from app.services import bd_grid_density as density, bd_grid_regions as regions
from app.services import bd_grid_region_builder as builder
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


def test_regions_are_built_from_rebound_current_merchant_counts(client, seed, source_db):
    rows(source_db, left=5, right=5)
    with appdb.SessionLocal() as db:
        left, right = grids(db, seed)
        admin = db.get(User, seed['users']['ops-admin'])
        preview = builder.plan(db, admin, target_stores=10, max_area_km2=2.1,
                               source_engine=source_db,source_table='merchant_source')
        assert preview['zone_count'] == 1
        assert preview['zones'][0]['store_count'] == 10
        assert set(preview['zones'][0]['grid_ids']) == {left.id, right.id}
        assert left.state == 'ready' and right.zone_id == left.zone_id
        applied = builder.plan(db, admin, target_stores=10, max_area_km2=2.1,
                               apply=True, expected_signature=preview['signature'],
                               source_engine=source_db,source_table='merchant_source')
        db.commit()
        zones = db.query(BdZone).filter_by(state='ready').order_by(BdZone.id).all()
        assert applied['applied'] and len(zones) == 1 and applied['zone_count'] == 1
        assert zones[0].store_count == 10
        assert zones[0].area_km2 == pytest.approx(2, abs=.01)
        assert left.zone_id == right.zone_id == zones[0].id
        revisions = (zones[0].revision, left.revision, right.revision)
        preview_again=builder.plan(db, admin, target_stores=10, max_area_km2=2.1,
                                   source_engine=source_db,source_table='merchant_source')
        builder.plan(db, admin, target_stores=10, max_area_km2=2.1,
                     apply=True, expected_signature=preview_again['signature'],
                     source_engine=source_db,source_table='merchant_source')
        db.commit()
        assert db.query(BdZone).filter_by(state='ready').count() == 1
        assert revisions == (zones[0].revision, left.revision, right.revision)


def test_preview_reactivates_previously_disabled_grid_when_current_count_recovers(client, seed, source_db):
    rows(source_db, left=5, right=5)
    with appdb.SessionLocal() as db:
        left, right = grids(db, seed)
        left.state = 'disabled'
        db.commit()
        admin = db.get(User, seed['users']['ops-admin'])
        preview = builder.plan(db, admin, target_stores=10, max_area_km2=2,
                               source_engine=source_db,source_table='merchant_source')
        assert preview['zone_count'] == 1
        assert set(preview['zones'][0]['grid_ids']) == {left.id, right.id}
        assert left.state == 'disabled'


def test_auto_region_preview_and_apply_routes_require_csrf_and_signature(client, seed, source_db, monkeypatch):
    from app.routers import bd_zone_ops_r
    from app.services import bd_grid_region_builder
    monkeypatch.setattr(bd_zone_ops_r, 'csrf_ok', lambda request, token: token == 'valid')
    real_plan=bd_grid_region_builder.plan
    def route_plan(db,user,**kwargs):
        kwargs['source_engine']=source_db
        kwargs['source_table']='merchant_source'
        return real_plan(db,user,**kwargs)
    monkeypatch.setattr(bd_grid_region_builder,'plan',route_plan)
    rows(source_db, left=5, right=5)
    with appdb.SessionLocal() as db:
        left, right = grids(db, seed)
        admin = db.get(User, seed['users']['ops-admin'])
        from app.services import bd_grid_region_builder as builder
        report = builder.plan(db, admin, target_basis='grids', target_per_zone=2,
                              max_area_km2=3, source_engine=source_db,source_table='merchant_source')
        assert report['zone_count'] == 1 and report['zones'][0]['grid_count'] == 2
    token = client.post('/login', data={'username':'ops-admin','password':'pw123456'}, follow_redirects=False)
    assert token.status_code in (200, 302, 303)
    preview = client.post('/zones/grid-auto-preview', json={
        'csrf_token':'valid','region':'tokyo23','target_basis':'grids','target_per_zone':2,
        'max_area_km2':3,'min_stores':5,'source_table':'merchant_source'})
    assert preview.status_code == 200, preview.text
    assert preview.json()['zones'][0]['grid_count'] == 2
    payload={'csrf_token':'valid','region':'tokyo23','target_basis':'grids','target_per_zone':2,
             'max_area_km2':3,'min_stores':5,
             'tolerance_pct':15,'max_grids_per_zone':None,'max_stores_per_zone':None,
             'signature':preview.json()['signature']}
    applied=client.post('/zones/grid-auto-apply',json=payload)
    assert applied.status_code==200 and applied.json()['ok']
    missing_signature=client.post('/zones/grid-auto-apply',json={k:v for k,v in payload.items() if k!='signature'})
    assert missing_signature.status_code==409


def test_target_tolerance_is_a_warning_not_a_reason_to_drop_connected_grid(client,seed,source_db):
    rows(source_db,left=5,right=5)
    with appdb.SessionLocal() as db:
        left,right=grids(db,seed)
        admin=db.get(User,seed['users']['ops-admin'])
        report=builder.plan(db,admin,target_basis='grids',target_per_zone=3,tolerance_pct=0,
            max_area_km2=3,source_engine=source_db,source_table='merchant_source')
        assert report['out_of_tolerance']
        assert report['zone_count']==1 and report['zones'][0]['grid_count']==2
        assert set(report['zones'][0]['grid_ids'])=={left.id,right.id}


def test_auto_partition_hard_caps_leave_oversized_single_grids_unassigned(client,seed,source_db):
    rows(source_db,left=5,right=5)
    with appdb.SessionLocal() as db:
        grids(db,seed)
        admin=db.get(User,seed['users']['ops-admin'])
        report=builder.plan(db,admin,target_basis='stores',target_per_zone=10,max_stores_per_zone=4,
                            max_area_km2=3,source_engine=source_db,source_table='merchant_source')
        assert report['zone_count']==0
        assert sorted(report['unassigned_grid_ids'])==[seed['grid'],2]
        assert db.query(BdZone).filter_by(state='ready').count()==1


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
