"""Merchant map counts, rolling coverage, response bounds, and admin access."""
from datetime import datetime, timedelta
import json
import re

import pytest
from sqlalchemy import text

import app.db as appdb
from app.models import BdTaskEvent, User
from app.services import bd_merchant_map as maps
from app.services.bd_zone_operations import OperationError
from tests_web.test_bd_zone_operations import seed, login  # noqa: F401

NOW = datetime(2026, 11, 5, 12)
VIEW = dict(west=139.6, south=35.6, east=139.9, north=35.9)


@pytest.fixture
def merchants(seed):
    with appdb.SessionLocal() as db:
        db.execute(text('''CREATE TABLE kanto_merchants_v20261029 (
            place_id TEXT PRIMARY KEY,name TEXT NOT NULL,lat FLOAT NOT NULL,lon FLOAT NOT NULL,
            address TEXT DEFAULT '',phone TEXT DEFAULT '',website TEXT DEFAULT '',sources TEXT DEFAULT 'osm',
            prefecture TEXT DEFAULT '',municipality TEXT DEFAULT '',location_accuracy TEXT DEFAULT 'pending')'''))
        db.execute(text('''INSERT INTO kanto_merchants_v20261029 (place_id,name,lat,lon)
            VALUES (:id,:name,:lat,:lon)'''),[
                {'id':identity,'name':name,'lat':lat,'lon':lon} for identity,name,lat,lon in [
                    ('osm:node/12','<img src=x onerror=alert(1)>',35.68,139.7),
                    ('b','shop b',35.69,139.71),('c','shop c',35.70,139.72),
                    ('d','shop d',35.71,139.73),('outside','outside',38,141)]])
        for identity, kind, delta in [
                ('osm:node/12','complete_store_task',1),('osm:node/12','complete_store_task',2),
                ('b','update_store_result',3),('c','complete_store_task',31),
                ('c','complete_store_task',-1),('c','verify_store_location',1),
                ('d','complete_store_task',30)]:
            db.add(BdTaskEvent(main_task_id=999,store_source_id=identity,event_type=kind,
                               created_at=NOW-timedelta(days=delta)))
        db.commit()
    yield
    with appdb.SessionLocal() as db:
        db.execute(text('DROP TABLE kanto_merchants_v20261029'))
        db.commit()


def admin(db, seed):
    return db.get(User, seed['users']['ops-admin'])


def test_coverage_counts_unique_visits_and_exact_rolling_window(merchants,seed):
    with appdb.SessionLocal() as db:
        clusters=maps.viewport(db,admin(db,seed),**VIEW,zoom=10,now=NOW)
        points=maps.viewport(db,admin(db,seed),**VIEW,zoom=18,now=NOW)
        assert clusters['mode']=='clusters' and points['mode']=='points'
        assert clusters['summary']==points['summary']=={
            'merchant_count':4,'covered_count':3,'coverage_pct':75.0}
        assert sum(c['merchant_count'] for c in clusters['cells'])==4
        assert sum(c['covered_count'] for c in clusters['cells'])==3
        assert next(p for p in points['points'] if p['id']=='c')['covered'] is False
        assert all('name' not in point for point in points['points'])


def test_dense_high_zoom_falls_back_to_complete_aggregation(merchants,seed):
    with appdb.SessionLocal() as db:
        db.execute(text('''INSERT INTO kanto_merchants_v20261029 (place_id,name,lat,lon)
            VALUES (:id,'dense',35.68,139.7)'''),[{'id':f'dense-{i}'} for i in range(maps.POINT_LIMIT)])
        result=maps.viewport(db,admin(db,seed),**VIEW,zoom=22,now=NOW)
        assert result['mode']=='clusters' and result['dense']
        assert result['summary']['merchant_count']==maps.POINT_LIMIT+4
        assert len(result['cells'])<=441 and result['points']==[]


def test_antimeridian_and_empty_views(merchants,seed):
    with appdb.SessionLocal() as db:
        db.execute(text("INSERT INTO kanto_merchants_v20261029 (place_id,name,lat,lon) VALUES ('dateline','dateline',35,179.5)"))
        result=maps.viewport(db,admin(db,seed),west=179,south=34,east=-179,north=36,zoom=8,now=NOW)
        assert result['summary']['merchant_count']==1
        result=maps.viewport(db,admin(db,seed),west=0,south=0,east=1,north=1,zoom=18,now=NOW)
        assert result['summary']['merchant_count']==0 and result['points']==[]


@pytest.mark.parametrize('changes',[{'west':float('nan')},{'north':90},{'east':181},{'zoom':23},{'south':36}])
def test_invalid_view_rejected_before_query(merchants,seed,changes):
    with appdb.SessionLocal() as db:
        with pytest.raises(OperationError,match='地图范围无效'):
            maps.viewport(db,admin(db,seed),**{**VIEW,'zoom':10,**changes},now=NOW)


def test_world_view_still_bounds_cluster_resolution():
    params=maps.validate_view(-180,-85,180,85,22)
    assert params['scale']<=16


def test_detail_is_lazy_and_source_ids_remain_unchanged(merchants,seed):
    with appdb.SessionLocal() as db:
        result=maps.merchant(db,admin(db,seed),'osm:node/12',now=NOW)
        assert result['place_id']=='osm:node/12'
        assert result['name']=='<img src=x onerror=alert(1)>'
        assert result['covered'] and result['last_visited_at'].endswith('Z')
        assert result['location_accuracy']=='pending'
        with pytest.raises(OperationError,match='商户不存在'):
            maps.merchant(db,admin(db,seed),"' OR 1=1 --",now=NOW)


def test_routes_admin_menu_and_no_payload_for_staff(client,seed,merchants):
    login(client,'ops-admin')
    html=client.get('/merchant-map').text
    assert 'id="merchant-map"' in html and 'id="merchant-map-config"' in html
    assert 'href="/merchant-map"' in html and '覆盖率' in html
    result=client.get('/merchant-map/data',params={**VIEW,'zoom':18})
    assert result.status_code==200 and result.headers['cache-control']=='no-store'
    assert result.json()['summary']['merchant_count']==4
    result=client.get('/merchant-map/detail',params={'identity':'osm:node/12'})
    assert result.status_code==200 and result.json()['place_id']=='osm:node/12'
    assert client.get('/merchant-map/detail?identity=missing').status_code==404
    login(client,'worker')
    for url in ('/merchant-map','/merchant-map/data?west=139&south=35&east=140&north=36&zoom=10',
                '/merchant-map/detail?identity=b'):
        assert client.get(url,follow_redirects=False).status_code in (302,403)


def test_no_map_key_has_explicit_recovery_copy(client,seed,monkeypatch):
    from app.config import get_settings
    monkeypatch.delenv('GOOGLE_PUBLIC_API_KEY',raising=False)
    get_settings.cache_clear()
    login(client,'ops-admin')
    html=client.get('/merchant-map').text
    config=json.loads(re.search(r'<script id="merchant-map-config"[^>]*>(.*?)</script>',html,re.S).group(1))
    assert config['key']=='' and config['texts']['missingKey']=='尚未配置地图密钥，请联系管理员。'
    get_settings.cache_clear()


def test_map_frontend_interactions():
    import subprocess
    result=subprocess.run(['node','tests_web/bd_merchant_map_mock.cjs'],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr
