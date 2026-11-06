"""当前商户坐标落格、阈值、重跑与归组守门；只使用隔离库。"""
import json

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from shapely.geometry import MultiPolygon, Point, box

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


def free_grids(db, seed):
    left,right=grids(db,seed)
    left.zone_id=right.zone_id=None
    db.commit()
    return left,right


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
        left, right = free_grids(db, seed)
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
        assert applied['applied'] and len(zones) == 2 and applied['zone_count'] == 1
        created=db.get(BdZone,left.zone_id)
        assert created.store_count == 10
        assert created.area_km2 == pytest.approx(2, abs=.01)
        assert left.zone_id == right.zone_id == created.id
        revisions = (created.revision, left.revision, right.revision)
        preview_again=builder.plan(db, admin, target_stores=10, max_area_km2=2.1,
                                   source_engine=source_db,source_table='merchant_source')
        assert preview_again['zone_count']==0
        with pytest.raises(ops.OperationError,match='未划分网格'):
            builder.plan(db, admin, target_stores=10, max_area_km2=2.1,
                         apply=True, expected_signature=preview_again['signature'],
                         source_engine=source_db,source_table='merchant_source')
        assert db.query(BdZone).filter_by(state='ready').count() == 2
        assert revisions == (created.revision, left.revision, right.revision)


def test_preview_reactivates_previously_disabled_grid_when_current_count_recovers(client, seed, source_db):
    rows(source_db, left=5, right=5)
    with appdb.SessionLocal() as db:
        left, right = free_grids(db, seed)
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
        left, right = free_grids(db, seed)
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


def test_auto_region_preview_returns_json_on_unexpected_geometry_error(client,seed,monkeypatch):
    from app.routers import bd_zone_ops_r
    from app.services import bd_grid_region_builder
    monkeypatch.setattr(bd_zone_ops_r,'csrf_ok',lambda request,token:token=='valid')
    monkeypatch.setattr(bd_grid_region_builder,'plan',
                        lambda *args,**kwargs: (_ for _ in ()).throw(AttributeError('MultiPolygon exterior')))
    token=client.post('/login',data={'username':'ops-admin','password':'pw123456'},follow_redirects=False)
    assert token.status_code in (200,302,303)
    response=client.post('/zones/grid-auto-preview',json={'csrf_token':'valid','region':'tokyo23'})
    assert response.status_code==500
    assert response.headers['content-type'].startswith('application/json')
    assert response.json()['error']=='自动分区计算失败，请稍后重试'


def test_multipart_grid_is_reported_unassigned_without_losing_merchants(client,seed,source_db):
    rows(source_db,left=5,right=5)
    with appdb.SessionLocal() as db:
        left,right=free_grids(db,seed)
        left.geometry=z.geographic(MultiPolygon([box(0,0,1000,1000),box(-100,0,-50,50)]))
        db.commit()
        before=json.loads(json.dumps(left.geometry))
        admin=db.get(User,seed['users']['ops-admin'])
        report=builder.plan(db,admin,target_basis='grids',target_per_zone=1,max_area_km2=3,
            source_engine=source_db,source_table='merchant_source')
        assert report['zone_count']==1
        assert report['zones'][0]['grid_ids']==[right.id]
        assert report['unassigned_grid_ids']==[left.id]
        assert report['geometry_unassigned'][0]['store_count']==5
        assert report['density']['mapped_stores']==10
        assert json.loads(json.dumps(left.geometry))==before
        assert left.state=='ready'


def test_target_tolerance_is_a_warning_not_a_reason_to_drop_connected_grid(client,seed,source_db):
    rows(source_db,left=5,right=5)
    with appdb.SessionLocal() as db:
        left,right=free_grids(db,seed)
        admin=db.get(User,seed['users']['ops-admin'])
        report=builder.plan(db,admin,target_basis='grids',target_per_zone=3,tolerance_pct=0,
            max_area_km2=3,source_engine=source_db,source_table='merchant_source')
        assert report['out_of_tolerance']
        assert report['zone_count']==1 and report['zones'][0]['grid_count']==2
        assert set(report['zones'][0]['grid_ids'])=={left.id,right.id}


def test_auto_partition_hard_caps_leave_oversized_single_grids_unassigned(client,seed,source_db):
    rows(source_db,left=5,right=5)
    with appdb.SessionLocal() as db:
        free_grids(db,seed)
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


def test_small_zone_merge_updates_preview_signature_and_preserves_grid_identity(client,seed,source_db):
    rows(source_db,left=5,right=100)
    with appdb.SessionLocal() as db:
        left,right=free_grids(db,seed)
        before={g.id:json.loads(json.dumps(g.geometry)) for g in (left,right)}
        admin=db.get(User,seed['users']['ops-admin'])
        params=dict(target_basis='grids',target_per_zone=1,max_area_km2=3,
                    source_engine=source_db,source_table='merchant_source')
        off=builder.plan(db,admin,small_zone_threshold=0,**params)
        assert off['zone_count']==2
        report=builder.plan(db,admin,**params)
        assert report['zone_count']==1
        assert report['small_zone_merge']=={'threshold':15,'before':1,'merged':1,'remaining':[]}
        assert report['zones'][0]['store_count']==105
        assert report['zones'][0]['grid_ids']==[left.id,right.id]
        assert report['signature']!=off['signature']
        with pytest.raises(ops.OperationError,match='预览已失效'):
            builder.plan(db,admin,expected_signature=report['signature'],small_zone_threshold=20,**params)
        applied=builder.plan(db,admin,apply=True,expected_signature=report['signature'],**params)
        assert applied['zone_count']==1
        assert left.zone_id==right.zone_id
        assert {g.id:json.loads(json.dumps(g.geometry)) for g in (left,right)}==before
        assert db.query(BdGridStore).count()==105


@pytest.mark.parametrize('caps',[
    {'max_area':1_500_000}, {'max_stores':100}, {'max_members':1}])
def test_small_zone_merge_respects_every_hard_limit(caps):
    groups=[{'geom':box(0,0,1000,1000),'members':{0},'count':5},
            {'geom':box(1000,0,2000,1000),'members':{1},'count':100}]
    options={'max_area':3_000_000,'max_stores':None,'max_members':None,**caps}
    result,report=builder._merge_small_groups(groups,15,target_basis='stores',target_per_zone=100,**options)
    assert len(result)==2 and report['merged']==0
    assert report['remaining']==[{'members':[0],'store_count':5,'reason':'hard_limit'}]
    assert sum(g['count'] for g in result)==105


@pytest.mark.parametrize('other', [box(1000.001,0,2000,1000),box(1000,1000,2000,2000)])
def test_small_zone_merge_never_bridges_gaps_or_corner_contacts(other):
    result,report=builder._merge_small_groups([
        {'geom':box(0,0,1000,1000),'members':{0},'count':5},
        {'geom':other,'members':{1},'count':100}],15,3_000_000,
        target_basis='stores',target_per_zone=100)
    assert len(result)==2 and report['merged']==0
    assert report['remaining'][0]['reason']=='no_shared_edge'


def test_small_zone_merge_aggregates_tails_and_stops_at_threshold():
    groups=[{'geom':box(i*1000,0,(i+1)*1000,1000),'members':{i},'count':5} for i in range(3)]
    result,report=builder._merge_small_groups(groups,15,4_000_000,
        target_basis='stores',target_per_zone=100)
    assert len(result)==1 and report=={'before':3,'merged':2,'remaining':[]}
    assert result[0]['members']=={0,1,2} and result[0]['count']==15
    assert result[0]['geom'].area==3_000_000


def test_auto_partition_preserves_assigned_grids_even_with_visits_and_low_density(client,seed,source_db):
    rows(source_db,left=1,right=5)
    with appdb.SessionLocal() as db:
        left,right=grids(db,seed)
        right.zone_id=None
        left.assignee_id=seed['users']['worker']
        left.progress=20
        parent=db.get(BdZone,left.zone_id)
        parent.team_id=seed['team']
        db.commit()
        db.refresh(left)
        db.refresh(parent)
        before={c.name:getattr(left,c.name) for c in BdWorkGrid.__table__.columns}
        before_parent={c.name:getattr(parent,c.name) for c in BdZone.__table__.columns}
        mappings=list(db.query(BdGridStore.source_id).filter_by(grid_id=left.id).all())
        admin=db.get(User,seed['users']['ops-admin'])
        params=dict(source_engine=source_db,source_table='merchant_source',small_zone_threshold=15)
        report=builder.plan(db,admin,**params)
        assert report['density']['preserved_grid_ids']==[left.id]
        assert report['zones'][0]['grid_ids']==[right.id]
        assert report['small_zone_merge']['merged']==0
        builder.plan(db,admin,apply=True,expected_signature=report['signature'],**params)
        db.flush()
        assert {c.name:getattr(left,c.name) for c in BdWorkGrid.__table__.columns}==before
        assert {c.name:getattr(parent,c.name) for c in BdZone.__table__.columns}==before_parent
        assert list(db.query(BdGridStore.source_id).filter_by(grid_id=left.id).all())==mappings
        assert right.zone_id is not None and right.zone_id!=left.zone_id


def test_auto_partition_rejects_preview_if_a_grid_has_since_been_partitioned(client,seed,source_db):
    rows(source_db,left=5,right=5)
    with appdb.SessionLocal() as db:
        left,right=free_grids(db,seed)
        admin=db.get(User,seed['users']['ops-admin'])
        params=dict(source_engine=source_db,source_table='merchant_source')
        report=builder.plan(db,admin,**params)
        left.zone_id=seed['zone']
        db.commit()
        with pytest.raises(ops.OperationError,match='预览已失效'):
            builder.plan(db,admin,apply=True,expected_signature=report['signature'],**params)
        db.rollback()
        assert db.get(BdWorkGrid,left.id).zone_id==seed['zone']
        assert db.get(BdWorkGrid,right.id).zone_id is None


@pytest.mark.parametrize('small_zone_threshold', [0, 15])
def test_auto_partition_recovers_multipart_grid_with_neighbor(client, seed, source_db, small_zone_threshold):
    rows(source_db, left=20, right=20)
    with appdb.SessionLocal() as db:
        left, right = free_grids(db, seed)
        left.geometry = z.geographic(MultiPolygon([
            box(0, 0, 1000, 1000), box(2000, 0, 2100, 1000)]))
        db.commit()
        original = json.loads(json.dumps(left.geometry))
        admin = db.get(User, seed['users']['ops-admin'])
        params = dict(target_basis='grids', target_per_zone=1, max_area_km2=3,
                      small_zone_threshold=small_zone_threshold,
                      source_engine=source_db, source_table='merchant_source')
        report = builder.plan(db, admin, **params)
        assert report['zone_count'] == 1
        assert set(report['zones'][0]['grid_ids']) == {left.id, right.id}
        assert report['zones'][0]['store_count'] == 40
        assert report['unassigned_grid_ids'] == report['geometry_unassigned'] == []
        assert left.zone_id is right.zone_id is None
        applied = builder.plan(db, admin, apply=True, expected_signature=report['signature'], **params)
        db.commit()
        assert applied['grids_assigned'] == 2
        assert left.zone_id == right.zone_id and left.zone_id is not None
        assert json.loads(json.dumps(left.geometry)) == original
        assert db.query(BdGridStore).count() == 40


def test_geometry_recovery_combines_multiple_neighbors_without_splitting_grids():
    groups = [
        {'geom': MultiPolygon([box(0, 0, 1, 1), box(3, 0, 4, 1)]), 'members': {0}, 'count': 20},
        {'geom': box(1, 0, 2, 1), 'members': {1}, 'count': 20},
        {'geom': box(2, 0, 3, 1), 'members': {2}, 'count': 20},
    ]
    result = builder._merge_geometry_groups(groups, 4, max_stores=60, max_members=3)
    assert len(result) == 1
    assert result[0]['members'] == {0, 1, 2} and result[0]['count'] == 60
    assert result[0]['geom'].equals(box(0, 0, 4, 1))
    assert [group['members'] for group in groups] == [{0}, {1}, {2}]


@pytest.mark.parametrize('limits', [dict(max_area=2), dict(max_area=4, max_stores=59),
                                  dict(max_area=4, max_members=2)])
def test_geometry_recovery_keeps_original_groups_when_hard_limits_block_completion(limits):
    groups = [
        {'geom': MultiPolygon([box(0, 0, 1000, 1000), box(3000, 0, 4000, 1000)]), 'members': {0}, 'count': 20},
        {'geom': box(1000, 0, 2000, 1000), 'members': {1}, 'count': 20},
        {'geom': box(2000, 0, 3000, 1000), 'members': {2}, 'count': 20},
    ]
    limits = {**limits, 'max_area': limits['max_area'] * 1_000_000}
    result = builder._merge_geometry_groups(groups, **limits)
    assert [(g['members'], g['count']) for g in result] == [({0}, 20), ({1}, 20), ({2}, 20)]


@pytest.mark.parametrize('neighbor', [box(1.01, 0, 2, 1), box(1, 1, 2, 2)])
def test_geometry_recovery_does_not_bridge_real_gaps_or_corner_contacts(neighbor):
    groups = [
        {'geom': MultiPolygon([box(0, 0, 1, 1), box(2, 0, 3, 1)]), 'members': {0}, 'count': 20},
        {'geom': neighbor, 'members': {1}, 'count': 20},
    ]
    result = builder._merge_geometry_groups(groups, 10)
    assert len(result) == 2
    assert result[0]['geom'].equals(groups[0]['geom'])


def test_auto_partition_does_not_persist_an_i_shaped_grid(client, seed, source_db):
    from tests_web.test_bd_grid_shapes import dumbbell
    rows(source_db, left=5, right=5)
    with appdb.SessionLocal() as db:
        left, right = free_grids(db, seed)
        left.geometry = z.geographic(dumbbell())
        right.geometry = z.geographic(box(3000, 0, 4000, 1000))
        db.commit()
        original = json.loads(json.dumps(left.geometry))
        admin = db.get(User, seed['users']['ops-admin'])
        report = builder.plan(db, admin, target_basis='grids', target_per_zone=8,
                              source_engine=source_db, source_table='merchant_source')
        assert report['zone_count'] == 0
        assert report['unassigned_grid_ids'] == [left.id]
        assert report['shape_unassigned'][0]['grid_ids'] == [left.id]
        assert left.zone_id is None
        assert json.loads(json.dumps(left.geometry)) == original
