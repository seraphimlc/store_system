"""All grids, including ungrouped/empty/disabled grids, get truthful searchable identities."""
import copy
from types import SimpleNamespace

import pytest
from shapely.geometry import Point, box, mapping

import app.db as appdb
from app.models import BdWorkGrid, BdRegion, User, BdWorkStore
from app.services import bd_grid_address_names as naming, bd_grid_enrichment as enrichment
from app.services import bd_zones as zones
from scripts.bd_name_all_grids import protected_state, verify_once
from tests_web.test_bd_hierarchical_tasks import batch
from tests_web.test_bd_zone_operations import seed  # noqa:F401


def test_address_evidence_does_not_invent_chome_or_buildings():
    parts=naming.address_parts('東京都練馬区西大泉６－３－１０','東京都','練馬区')
    assert parts['town']=='東京都練馬区西大泉' and not parts['buildings']
    assert '6丁目' not in parts['town']
    parts=naming.address_parts('〒250-0011 小田原市 Sakaecho, 2 Chome−8−35 宮澤商事第２ビル', '神奈川県','小田原市')
    assert parts['buildings']==['宮澤商事第2ビル']
    assert parts['locality']=='Sakaecho'
    assert naming.address_parts('神奈川県横浜市中区山下町12-3','神奈川県','横浜市')['town']=='神奈川県横浜市中区山下町'
    assert naming.address_parts('', '東京都','新宿区')['city']=='東京都新宿区'
    assert naming.address_parts('中原区田尻町14番地橘ビル','神奈川県','川崎市中原区')['town']=='神奈川県川崎市中原区田尻町'
    assert naming.address_parts('千葉県市原市八幡1059番地2東照ビル','千葉県','市原市')['buildings']==['東照ビル']
    assert naming.address_parts('250-0011 末広2丁目124番地杉浦ビル','埼玉県','熊谷市')['buildings']==['杉浦ビル']


def test_nearby_stations_are_distinct_from_inside_array():
    geometry=zones.geographic(box(0,0,1000,1000))
    grid=SimpleNamespace(id=1,geometry=geometry,stations=['内側'])
    def station(name,x):
        point=zones.geographic(Point(x,500))['coordinates']
        return {'name':name,'lon':point[0],'lat':point[1],'group':name}
    result=naming.station_context([grid],[station('内側',500),station('近隣',1200),station('遠方',3000)])
    assert result[1]==[{'name':'近隣','distance_m':200}]
    assert grid.stations==['内側']


def test_ai_can_only_choose_complete_real_candidates(monkeypatch):
    records=[{'key':'g1','candidates':['新宿','西新宿'],'basis':'address'}]
    monkeypatch.setattr(naming.ai_chat,'configured',lambda:True)
    monkeypatch.setattr(naming.ai_chat,'chat',lambda *a,**kw:'{"choices":{"g1":1}}')
    assert naming.select_names(records)=={'g1':1}
    for invalid in ({},{'g1':2},{'g1':True},{'g1':'架空地名'},{'g1':0,'g2':0}):
        with pytest.raises(ValueError):naming.validate_choices(records,invalid)
    assert len(naming.compose_name('住所'*100,'G',123))==128
    assert naming.compose_name('住所'*100,'G',123).endswith('・G123')


def test_full_plan_covers_empty_disabled_ungrouped_and_one_validation_pass(seed):
    with appdb.SessionLocal() as db:
        store=db.get(BdWorkStore,'shop-synthetic');store.address='東京都新宿区西新宿2-1-1新宿三井ビル'
        current=db.get(BdWorkGrid,seed['grid']);current.stations=['新宿'];current.keywords=['旧検索語']
        main,rt,gt,st=batch(db,seed)
        empty=BdWorkGrid(release_id=current.release_id,zone_id=None,source_id='empty-grid',
            geometry=copy.deepcopy(current.geometry),area_km2=2,store_count=0,state='disabled')
        db.add(empty);db.flush()
        old_snapshot=copy.deepcopy(gt.snapshot)
        plan=naming.build_plan(db)
        by_id={g['id']:g for g in plan['grids']}
        assert set(by_id)=={current.id,empty.id}
        assert by_id[current.id]['basis']=='address' and '新宿三井ビル' in by_id[current.id]['keywords']
        assert by_id[empty.id]['basis']=='area_fallback' and by_id[empty.id]['keywords']
        assert by_id[current.id]['stations']==['新宿']
        choices={r['key']:0 for r in naming.choice_records(plan)}
        naming.apply_choices(plan,choices)
        before=protected_state(db)
        admin=db.get(User,seed['users']['ops-admin'])
        enrichment.apply_plan(db,admin,plan);db.flush()
        report=verify_once(db,admin,plan,before)
        assert report['named_grids']==2 and report['named_regions']==1
        assert all(report['checks'].values())
        assert gt.snapshot==old_snapshot and empty.state=='disabled' and empty.zone_id is None
        assert db.get(BdRegion,seed['zone']).name.endswith(f"・R{seed['zone']}")


def test_validation_detects_a_business_data_change(seed):
    with appdb.SessionLocal() as db:
        plan=naming.build_plan(db)
        before=protected_state(db)
        user=db.get(User,seed['users']['ops-admin'])
        enrichment.apply_plan(db,user,plan);db.flush()
        db.get(BdWorkGrid,seed['grid']).store_count+=1;db.flush()
        with pytest.raises(AssertionError,match='数据发生变更'):
            verify_once(db,user,plan,before)
