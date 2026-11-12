"""Geographic search, immutable task history, editing guards and verified AI plans."""
import copy
from types import SimpleNamespace

import pytest
from shapely.geometry import Point, box, mapping

import app.db as appdb
from app.models import BdWorkGrid, BdRegion, BdGridTask, User, BdTaskEvent, BdLog
from app.services import bd_grid_metadata as metadata, bd_grid_enrichment as enrichment
from app.services import bd_hierarchical_tasks as tasks, bd_zone_operations as ops, bd_log
from tests.helpers import form_token
from tests_web.test_bd_hierarchical_tasks import batch
from tests_web.test_bd_zone_operations import seed, login, csrf  # noqa:F401


def enrich(db,seed):
    grid=db.get(BdWorkGrid,seed['grid'])
    grid.name='東京駅丸の内';grid.stations=['東京駅'];grid.keywords=['東京国際フォーラム','A_100%']
    db.flush()
    return grid


def test_region_creation_search_has_verified_terms_and_escaped_html(seed,client):
    with appdb.SessionLocal() as db:
        grid=enrich(db,seed);grid.keywords=grid.keywords+['<script>alert(1)</script>'];db.commit()
    login(client,'ops-admin')
    page=client.get('/zone-tasks')
    assert page.status_code==200
    assert 'id="batch-region-search"' in page.text
    assert '東京駅' in page.text and '東京国際フォーラム' in page.text
    assert '<script>alert(1)</script>' not in page.text
    assert 'id="batch-region-empty"' in page.text


def test_search_current_and_snapshot_metadata_with_state_and_literal_arrays(seed):
    with appdb.SessionLocal() as db:
        grid=enrich(db,seed)
        main,rt,gt,_=batch(db,seed)
        admin=db.get(User,seed['users']['ops-admin'])
        original=copy.deepcopy(gt.snapshot)
        grid.stations=['大手町駅'];grid.name='大手町';db.get(BdRegion,seed['zone']).name='丸の内片区';db.flush()
        for keyword in ('東京駅','大手町駅','東京国際フォーラム','丸の内片区','大手町',str(grid.id)):
            _,tree=tasks.task_tree(db,admin,main.id,keyword=keyword)
            assert len(tree[0]['grids'])==1,keyword
        assert not tasks.task_tree(db,admin,main.id,keyword='no-such-place')[1][0]['grids']
        assert not tasks.task_tree(db,admin,main.id,keyword='東京駅',task_state='completed')[1][0]['grids']
        assert gt.snapshot==original
        assert db.query(BdWorkGrid).filter(metadata.grid_match('東京国際フォーラム')).count()==1
        assert db.query(BdWorkGrid).filter(metadata.grid_match('A_100%')).count()==1
        assert db.query(BdWorkGrid).filter(metadata.grid_match('A_1000')).count()==0


def test_geographic_log_search_keeps_batch_and_permission_scope(seed):
    with appdb.SessionLocal() as db:
        enrich(db,seed);main,rt,gt,st=batch(db,seed)
        worker=db.get(User,seed['users']['worker']);admin=db.get(User,seed['users']['ops-admin'])
        tasks.complete_store(db,worker,st.id,st.revision,str(__import__('uuid').uuid4()),'normal','');db.flush()
        for keyword in ('東京駅','東京国際フォーラム'):
            events=tasks.task_events(db,admin,main.id,keyword=keyword)
            assert any(e.event_type=='complete_store_task' for e in events[1])
            assert events[2]==len(events[1])
            assert any(e.action=='complete_store_task' for e in bd_log.recent(db,kw=keyword))
            assert bd_log.recent_count(db,kw=keyword)==len(bd_log.recent(db,kw=keyword))
        assert not tasks.task_events(db,admin,main.id,keyword='missing-station')[1]
        with pytest.raises(ops.OperationError):
            tasks.task_events(db,db.get(User,seed['users']['outsider']),main.id,keyword='東京駅')


def test_region_rename_guards_and_preserves_task_snapshots(seed,client):
    with appdb.SessionLocal() as db:
        main,rt,_,_=batch(db,seed);db.commit();original=copy.deepcopy(rt.snapshot)
        zone=db.get(BdRegion,seed['zone']);revision=zone.revision
        with pytest.raises(ops.OperationError):
            metadata.rename_region(db,db.get(User,seed['users']['captain']),zone.id,'x',revision)
        with pytest.raises(ops.OperationError):
            metadata.rename_region(db,db.get(User,seed['users']['ops-admin']),zone.id,'  ',revision)
    login(client,'ops-admin');panel=client.get(f"/zones/{seed['zone']}/panel")
    url=f"/zones/{seed['zone']}/rename"
    data={'_ft':form_token(client),'csrf_token':csrf(panel.text),'revision':revision,'name':'  丸の内片区  '}
    saved=client.post(url,data=data,follow_redirects=False)
    assert saved.status_code==303
    assert saved.headers['location'].startswith('/zones') and 'msg=' in saved.headers['location']
    assert '片区名称已保存' in client.get(saved.headers['location']).text
    with appdb.SessionLocal() as db:
        zone=db.get(BdRegion,seed['zone']);assert zone.name=='丸の内片区' and zone.revision==revision+1
        assert db.get(type(rt),rt.id).snapshot==original
        assert db.query(BdLog).filter_by(domain='zone',action='rename',ref_id=zone.id).count()==1
    data['_ft']=form_token(client)
    assert client.post(url,data=data).status_code==409
    data.update(_ft=form_token(client),csrf_token='bad',revision=revision+1)
    assert client.post(url,data=data).status_code in (403,409)


def test_geographic_assignment_uses_boundaries_and_deduplicates():
    grids=[SimpleNamespace(id=1,geometry=mapping(box(0,0,1,1))),SimpleNamespace(id=2,geometry=mapping(box(1,0,2,1)))]
    def feature(x,y,**tags):return {'geometry':mapping(Point(x,y)),'properties':tags}
    values=enrichment.geographic_metadata(grids,[feature(1,.5,name='東京駅',railway='station'),
        feature(.5,.5,name='博物館',tourism='museum'),feature(.5,.5,name='博物館',building='museum'),
        feature(3,3,name='外部駅',railway='station'),feature(.5,.5,name='住宅',building='apartments')])
    assert values[1]['stations']==values[2]['stations']==['東京駅']
    assert values[1]['keywords']==['博物館'] and not values[2]['keywords']
    assert metadata.clean_terms(['Ａ駅','A駅','  ','A駅'])==['Ａ駅']
    with pytest.raises(ValueError):metadata.clean_terms('東京駅')


def test_ai_names_are_complete_and_no_ai_keywords_are_trusted(monkeypatch):
    monkeypatch.setattr(enrichment.ai_chat,'configured',lambda:True)
    monkeypatch.setattr(enrichment.ai_chat,'chat',lambda *a,**kw:'{"region_name":"丸の内","grids":[{"id":1,"name":"東京駅周辺"}]}')
    result=enrichment.generate_names({'grids':[{'id':1,'stations':['東京駅'],'keywords':[]}]})
    assert result['grids'][0]['name']=='東京駅周辺'
    for invalid in ({'region_name':'x','grids':[]},{'region_name':'x','grids':[{'id':1,'name':'x'},{'id':1,'name':'y'}]},
                    {'region_name':'x','grids':[{'id':2,'name':'x'}]}):
        with pytest.raises(ValueError):enrichment.validate_names(invalid,[1],include_region=True)


def test_apply_plan_is_atomic_on_invalid_or_stale_data_and_leaves_business_state(seed):
    with appdb.SessionLocal() as db:
        regions=db.query(BdRegion).order_by(BdRegion.id).all();grids=db.query(BdWorkGrid).order_by(BdWorkGrid.id).all()
        plan={'fingerprint':enrichment.fingerprint(regions,grids),
              'regions':[{'id':r.id,'name':'丸の内'} for r in regions],
              'grids':[{'id':g.id,'name':'東京駅周辺','stations':['東京駅'],'keywords':['博物館']} for g in grids]}
        admin=db.get(User,seed['users']['ops-admin'])
        invalid=copy.deepcopy(plan);invalid['grids'][0]['stations']='駅'
        with pytest.raises(ValueError):enrichment.apply_plan(db,admin,invalid)
        assert regions[0].name=='synthetic'
        assert enrichment.apply_plan(db,admin,plan)==(len(regions),len(grids))
        assert grids[0].stations==['東京駅'] and grids[0].keywords==['博物館']
        assert grids[0].state=='ready' and grids[0].progress==0
        with pytest.raises(ops.OperationError,match='已变更'):enrichment.apply_plan(db,admin,plan)
