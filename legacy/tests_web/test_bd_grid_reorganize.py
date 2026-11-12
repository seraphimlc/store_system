"""手动重组：整网格拆分、元数据、回跳与有证据的窄河连接。"""
import json
from types import SimpleNamespace

import pytest
from shapely.geometry import box, shape

import app.db as appdb
from app.models import User, BdZone, BdWorkGrid, BdGridStore, BdStoreVisitLog, BdLog
from app.services import bd_grid_regions as regions, bd_zone_operations as ops, bd_zone_rules as rules, bd_zones as z
from tests_web.test_bd_zone_operations import seed, login, csrf  # noqa: F401
from tests_web.test_bd_natural_grids import add_grid, selected
from tests.helpers import form_token


def test_split_preserves_grids_and_merchants_and_allows_individual_regrouping(seed):
    with appdb.SessionLocal() as db:
        admin = db.get(User, seed['users']['ops-admin'])
        zone = db.get(BdZone, seed['zone'])
        first = db.get(BdWorkGrid, seed['grid'])
        first.geometry = z.geographic(box(0, 0, 1000, 1000))
        first.area_km2 = 1
        other = add_grid(db, first.release_id, 'split-other', box(1000, 0, 2000, 1000), zone.id)
        db.commit()
        geometry = json.loads(json.dumps(first.geometry)), json.loads(json.dumps(other.geometry))
        mappings = [(r.id, r.grid_id, r.source_id) for r in db.query(BdGridStore).all()]
        regions.split_zone(db, admin, zone.id, zone.revision)
        db.commit()
        assert zone.state == 'archived' and zone.store_count == 0 and zone.revision == 1
        assert first.zone_id is None and other.zone_id is None
        assert first.revision == other.revision == 1
        assert (first.geometry, other.geometry) == geometry
        assert [(r.id, r.grid_id, r.source_id) for r in db.query(BdGridStore).all()] == mappings
        assert db.query(BdLog).filter_by(action='split_zone').count() == 1
        rebuilt = regions.merge(db, admin, [selected(first)], '重新划分')
        assert first.zone_id == rebuilt.id and other.zone_id is None
        assert rebuilt.area_km2 == pytest.approx(1)


@pytest.mark.parametrize('protection', ['team', 'assignee', 'progress', 'task_state', 'history', 'revision', 'role'])
def test_split_rejects_protected_or_stale_request_without_changes(seed, protection):
    with appdb.SessionLocal() as db:
        admin = db.get(User, seed['users']['ops-admin'])
        zone = db.get(BdZone, seed['zone'])
        grid = db.get(BdWorkGrid, seed['grid'])
        if protection == 'team': zone.team_id = seed['team']
        if protection == 'assignee': grid.assignee_id = seed['users']['worker']
        if protection == 'progress': grid.progress = 20
        if protection == 'task_state': grid.task_state = 'visited'
        if protection == 'history':
            db.add(BdStoreVisitLog(request_id='split-history', store_id='shop-synthetic', grid_id=grid.id,
                actor_id=admin.id, operation='visit', before={}, after={}, note=''))
        db.commit()
        user = db.get(User, seed['users']['captain']) if protection == 'role' else admin
        revision = -1 if protection == 'revision' else zone.revision
        with pytest.raises(ops.OperationError):
            regions.split_zone(db, user, zone.id, revision)
        assert grid.zone_id == zone.id and grid.revision == 0
        assert zone.state == 'ready' and zone.revision == 0
        assert db.query(BdLog).filter_by(action='split_zone').count() == 0


def test_merge_json_patches_affected_zones_without_reload_or_consuming_token(client, seed):
    import re
    from shapely.geometry import box

    from app.services import bd_zones as z
    from tests_web.test_bd_natural_grids import add_grid

    login(client, 'ops-admin')
    editor = client.get('/zones?mode=manage')
    assert 'data-ajax="1"' in editor.text and '"saving"' in editor.text
    page = client.get('/zones?mode=manage&region=all')
    token = re.search(r'name="_ft" value="([^"]+)"', page.text).group(1)
    with appdb.SessionLocal() as db:
        grid, zone = db.get(BdWorkGrid, seed['grid']), db.get(BdZone, seed['zone'])
        other = add_grid(db, grid.release_id, 'remain', box(2000, 0, 3000, 1000), zone.id)
        db.commit()
        choices = selected(grid, zone)
        other_id, zone_revision = other.id, zone.revision
    response = client.post('/zones/merge-grids', json={
        'csrf_token': csrf(page.text), 'region': 'all', 'name': '就地组合',
        'merge_boundaries': True, 'selections': [choices]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['archived_zone_ids'] == []
    assert body['zones'][0]['id'] == seed['zone'] and body['zones'][0]['revision'] == zone_revision + 1
    assert body['zone']['name'] == '就地组合' and body['zone']['geometry']['type'] == 'Polygon'
    rows = {row['id']: row for row in body['grids']}
    assert rows[seed['grid']]['zone_id'] == body['zone']['id'] and rows[seed['grid']]['revision'] == 1
    assert rows[other_id]['zone_id'] == seed['zone'] and rows[other_id]['revision'] == 0
    assert rows[other_id]['zone_revision'] == zone_revision + 1
    assert {row['id'] for row in body['grids']} == {seed['grid'], other_id}
    split = client.post('/zones/split', data={'_ft': token, 'csrf_token': csrf(page.text),
        'region': 'all', 'zone_id': body['zone']['id'], 'revision': body['zone']['revision']},
        follow_redirects=False)
    assert split.status_code == 303 and 'mode=manage' in split.headers['location']


def test_composition_split_redirects_keep_region_and_manage_mode(client, seed):
    login(client, 'ops-admin')
    page = client.get('/zones?mode=manage&region=all')
    with appdb.SessionLocal() as db:
        grid, zone = db.get(BdWorkGrid, seed['grid']), db.get(BdZone, seed['zone'])
        choices = selected(grid, zone)
    response = client.post('/zones/merge-grids', data={'_ft': form_token(client),
        'csrf_token': csrf(page.text), 'region': 'all', 'selections': json.dumps(choices),
        'name': '重新组合'}, follow_redirects=False)
    assert response.status_code == 303
    assert 'mode=manage' in response.headers['location'] and 'region=all' in response.headers['location']
    page = client.get(response.headers['location'])
    with appdb.SessionLocal() as db:
        grid = db.get(BdWorkGrid, seed['grid'])
        zone = db.get(BdZone, grid.zone_id)
        identity, revision = zone.id, zone.revision
    detail = client.get(f'/zones/{identity}/panel')
    assert '拆分片区' in detail.text and 'action="/zones/split"' in detail.text
    response = client.post('/zones/split', data={'_ft': form_token(client), 'csrf_token': csrf(page.text),
        'region': 'all', 'zone_id': identity, 'revision': revision}, follow_redirects=False)
    assert response.status_code == 303 and 'region=all' in response.headers['location']
    assert '片区已拆分，网格可重新组合' in client.get(response.headers['location']).text
    response = client.post('/zones/split', data={'_ft': form_token(client), 'csrf_token': csrf(page.text),
        'region': 'all', 'zone_id': identity, 'revision': revision}, follow_redirects=False)
    assert response.status_code == 303 and 'err=' in response.headers['location']


def test_grid_metadata_and_tags_are_preserved_and_escaped(client, seed):
    with appdb.SessionLocal() as db:
        grid = db.get(BdWorkGrid, seed['grid'])
        grid.name = '<新的网格名>'
        grid.keywords = ['商店街', '<关键词>']
        grid.stations = ['新宿駅']
        db.commit()
        admin = db.get(User, seed['users']['ops-admin'])
        row = next(r for r in regions.management_grids(db, admin) if r['id'] == grid.id)
        assert row['name'] == grid.name and row['keywords'] == grid.keywords and row['stations'] == grid.stations
    login(client, 'ops-admin')
    page = client.get('/zones?mode=manage')
    assert '&lt;新的网格名&gt;' in page.text and '&lt;关键词&gt;' in page.text
    assert 'grid-tag-zone' in page.text and 'grid-tag-station' in page.text
    assert 'data-split-zone=' in page.text


@pytest.mark.parametrize('width,allowed', [(30, True), (100, True), (101, False)])
def test_manual_merge_across_known_narrow_river_keeps_water_excluded(monkeypatch, width, allowed):
    river = box(1000, -100, 1000 + width, 1100)
    monkeypatch.setattr(rules, 'load_river_geometry', lambda root: river)
    grids = [SimpleNamespace(geometry=z.geographic(box(0, 0, 1000, 1000))),
             SimpleNamespace(geometry=z.geographic(box(1000 + width, 0, 2000 + width, 1000)))]
    if not allowed:
        with pytest.raises(ops.OperationError, match='已知河道'):
            regions.merged_geometry(grids)
        return
    geometry = regions.merged_geometry(grids)
    assert geometry.geom_type == 'MultiPolygon'
    assert geometry.area == pytest.approx(2e6, abs=.01)
    assert geometry.intersection(river).area < .01


def test_narrow_river_evidence_does_not_bridge_land_gaps_or_partial_crossings(monkeypatch):
    grids = [SimpleNamespace(geometry=z.geographic(box(0, 0, 1000, 1000))),
             SimpleNamespace(geometry=z.geographic(box(1060, 0, 2060, 1000)))]
    for river in (box(3000, 0, 3060, 1000), box(1000, -100, 1030, 1100), shape({'type':'Polygon','coordinates':[]})):
        monkeypatch.setattr(rules, 'load_river_geometry', lambda root: river)
        with pytest.raises(ops.OperationError, match='已知河道'):
            regions.merged_geometry(grids)


def test_river_loader_accepts_only_tagged_river_faces(tmp_path):
    def feature(identity, tags, xmin):
        coordinates = z.geographic(box(xmin, 0, xmin + 60, 1000))['coordinates'][0]
        return {'type':'way', 'id':identity, 'tags':tags,
                'geometry':[{'lon':p[0], 'lat':p[1]} for p in coordinates]}
    path = tmp_path / 'synthetic.roads-v1.source.json'
    path.write_text(json.dumps({'elements':[feature(1, {'natural':'water','water':'river'}, 1000),
        feature(2, {'natural':'water','water':'lake'}, 2000),feature(3, {'natural':'wood'}, 3000)]}))
    geometry = rules.load_river_geometry(tmp_path)
    assert geometry.area == pytest.approx(60000, abs=.01)
    assert geometry.bounds[0] == pytest.approx(1000, abs=.001)


def test_river_connected_preview_and_save_keep_original_multipart_boundary(seed, monkeypatch):
    monkeypatch.setattr(rules, 'load_river_geometry', lambda root: box(1000, -100, 1060, 1100))
    with appdb.SessionLocal() as db:
        admin = db.get(User, seed['users']['ops-admin'])
        grid = db.get(BdWorkGrid, seed['grid'])
        grid.zone_id = None
        grid.geometry = z.geographic(box(0, 0, 1000, 1000))
        grid.area_km2 = 1
        other = add_grid(db, grid.release_id, 'river-right', box(1060, 0, 2060, 1000))
        choices = [selected(grid), selected(other)]
        preview = regions.preview(db, admin, choices)
        assert preview['geometry']['type'] == 'MultiPolygon'
        assert preview['properties']['area_km2'] == pytest.approx(2, abs=.000001)
        zone = regions.merge(db, admin, choices, '两岸片区')
        db.commit()
        assert zone.geometry['type'] == 'MultiPolygon'
        assert zone.area_km2 == pytest.approx(2, abs=.000001)
        assert grid.zone_id == other.zone_id == zone.id


def test_river_collection_is_bounded_to_existing_grids_and_requests_only_river_faces(seed, tmp_path, monkeypatch):
    from scripts import bd_collect_grid_rivers, bd_collect_zones
    captured = {}
    def fetch(query, path):
        captured.update(query=query, path=path)
        return {'elements':[{'type':'way', 'id':1}]}
    monkeypatch.setattr(bd_collect_zones, 'fetch', fetch)
    with appdb.SessionLocal() as db:
        report = bd_collect_grid_rivers.collect(db, tmp_path / 'rivers.json')
    assert report['elements'] == 1 and report['bbox'] in captured['query']
    assert '["water"="river"]' in captured['query'] and '["waterway"="riverbank"]' in captured['query']
    assert 'lake' not in captured['query'] and captured['path'] == tmp_path / 'rivers.json'
