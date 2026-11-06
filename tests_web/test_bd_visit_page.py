"""批次网格页（队员端巡店）：网格标题 + 返回、地图/商户列表三段式、商户表四列、
列表头筛选同一行且改动即搜、点行展开可编辑详情、导航跳谷歌地图、作业进展自动算。"""
import re
import pathlib
import uuid
import app.db as appdb
from app.models import BdGridStore, BdWorkStore, BdWorkGrid, BdStoreTask, User
from app.services import bd_hierarchical_tasks as tasks
from tests_web.test_bd_zone_operations import seed, login, csrf
from tests_web.test_bd_hierarchical_tasks import batch
from tests.helpers import form_token

CSS=pathlib.Path(__file__).resolve().parents[1]/'app/static/bd_visit.css'
DISPATCH_CSS=pathlib.Path(__file__).resolve().parents[1]/'app/static/bd_dispatch.css'


def multi_batch(db,seed):
    """批次 + 3 个店铺任务（名称各不相同，便于测筛选）。"""
    grid=db.get(BdWorkGrid,seed['grid'])
    for sid,name in [('shop-a','store-alpha'),('shop-b','store-beta')]:
        db.add(BdGridStore(release_id=grid.release_id,grid_id=grid.id,source_id=sid))
        db.add(BdWorkStore(id=sid,name=name,address='addr-'+sid,lat=seed['lat'],lon=seed['lon'],status='normal'))
    db.flush()
    return batch(db,seed)


def complete(db,seed,store_task,status='normal'):
    worker=db.get(User,seed['users']['worker'])
    tasks.complete_store(db,worker,store_task.id,store_task.revision,str(uuid.uuid4()),status,'备注')
    db.flush()


def test_batch_grid_page_navigation_title_back_and_three_blocks(client,seed):
    with appdb.SessionLocal() as db:
        task,rt,gt,st=multi_batch(db,seed);db.commit()
    login(client,'worker')
    page=client.get(f"/my/work/batch-grids/{gt.id}")
    assert page.status_code==200
    html=page.text
    # 导航标题 = 网格名称；左边是返回（回到批次任务树）
    header=re.search(r'<header class="topbar">(.*?)</header>',html,re.S).group(1)
    assert 'dispatch-back' in header and f'/my/work/batches/{task.id}' in header
    assert f'网格 #{seed["grid"]}' in header
    assert html.count('<h1')==1
    # 三段式：地图 → 商户列表（导航栏来自 base.html）
    assert 'id="work-map"' in html and 'visit-list' in html
    assert 'bd_visit.css' in html and 'bd_visit.js' in html


def test_batch_grid_table_columns_are_name_status_visit_state_and_navigation(client,seed):
    with appdb.SessionLocal() as db:
        task,rt,gt,st=multi_batch(db,seed);db.commit()
    login(client,'worker')
    html=client.get(f"/my/work/batch-grids/{gt.id}").text
    head=re.search(r'<thead>(.*?)</thead>',html,re.S).group(1)
    assert [re.sub(r'<[^>]+>|[↑↓↕]','',c).strip() for c in re.findall(r'<th[^>]*>(.*?)</th>',head)]==['商户名称','状态','巡店状态','定位']
    # 定位列 → 谷歌地图搜索位置，不进入路线导航
    nav=re.search(r'<a class="visit-nav" href="([^"]+)"',html).group(1)
    assert 'https://www.google.com/maps/search/?api=1&amp;query=' in nav
    assert 'query_place_id=' not in nav
    assert '/maps/dir/' not in nav
    assert 'data-location-status' in html and '待核实' in html
    assert 'id="location-check-dialog"' in html
    assert '不准确，更新坐标' in html


def test_batch_grid_row_click_opens_editable_store_detail(client,seed):
    with appdb.SessionLocal() as db:
        task,rt,gt,st=multi_batch(db,seed);db.commit()
    login(client,'worker')
    html=client.get(f"/my/work/batch-grids/{gt.id}").text
    assert 'class="visit-row"' in html and 'aria-expanded="false"' in html
    assert f'id="store-{st.id}"' in html
    assert f'id="store-detail-{st.id}"' in html
    # 详情里可编辑门店状态与备注
    detail=re.search(r'<tr class="visit-detail-row".*?</tr>',html,re.S).group(0)
    for field in ('name="status"','name="note"'):
        assert field in detail, field
    assert f'action="/my/work/batch-stores/{st.id}/complete"' in detail


def test_location_editor_uses_readonly_coordinates_and_manual_gps_button(client,seed):
    with appdb.SessionLocal() as db:
        _,_,gt,_=multi_batch(db,seed);db.commit()
    login(client,'worker')
    html=client.get(f"/my/work/batch-grids/{gt.id}").text
    detail=re.search(r'<section class="visit-location-info".*?</section>',html,re.S).group(0)
    assert 'data-coordinate-preview' in detail and 'data-get-location' in detail
    assert 'type="hidden"' in detail and 'name="lat"' in detail and 'name="lon"' in detail
    assert 'type="number"' not in detail and '保存位置并查询Google地点' in detail


def test_batch_grid_filters_are_one_row_and_autosubmit(client,seed):
    with appdb.SessionLocal() as db:
        task,rt,gt,st=multi_batch(db,seed);db.commit()
    login(client,'worker')
    html=client.get(f"/my/work/batch-grids/{gt.id}").text
    filters=re.search(r'<form class="(?:bd-filters|task-filters)[^"]*".*?</form>',html,re.S).group(0)
    assert 'method="get"' in filters and 'data-autosubmit="1"' in filters
    assert 'name="task_state"' in filters and 'name="status"' in filters and 'name="kw"' in filters
    assert 'bd_filters.js' in html
    # 三个控件**同一行**（CSS 固定单行 flex），不再是一列
    css=DISPATCH_CSS.read_text()
    assert '.bd-filters {display:flex' in css
    assert '.bd-filters {display:grid' not in css


def test_batch_grid_progress_is_computed_from_store_tasks(client,seed):
    """作业进展 = 已完成店铺任务数 / 店铺任务总数，自动算，不再让队员手填。"""
    with appdb.SessionLocal() as db:
        task,rt,gt,st=multi_batch(db,seed);db.commit()
    login(client,'worker')
    html=client.get(f"/my/work/batch-grids/{gt.id}").text
    assert re.search(r'data-testid="visit-progress-state"[^>]*>未巡店<',html)
    assert re.search(r'data-testid="visit-progress-count"[^>]*>0 / 3 家<',html)
    # 页面里**没有**手填进度的表单
    assert 'name="pct"' not in html and 'name="task_state"' not in html.split('data-testid="visit-filters"')[0]
    with appdb.SessionLocal() as db:
        store=db.query(BdStoreTask).filter_by(grid_task_id=gt.id).first()
        complete(db,seed,store,'abnormal')
        db.commit()
    html=client.get(f"/my/work/batch-grids/{gt.id}").text
    assert re.search(r'data-testid="visit-progress-state"[^>]*>巡店中<',html)
    assert re.search(r'data-testid="visit-progress-count"[^>]*>1 / 3 家<',html)
    assert '33%' in html


def test_batch_grid_filters_narrow_the_store_list(client,seed):
    with appdb.SessionLocal() as db:
        task,rt,gt,st=multi_batch(db,seed)
        done=db.query(BdStoreTask).filter_by(grid_task_id=gt.id).order_by(BdStoreTask.id).first()
        complete(db,seed,done,'abnormal')
        db.commit()
    login(client,'worker')
    base=f"/my/work/batch-grids/{gt.id}"
    def names(query):
        html=client.get(base+query).text
        body=re.search(r'<tbody>(.*?)</tbody>',html,re.S).group(1)
        return set(re.findall(r'<tr class="visit-row" id="store-([^"]+)"',body))
    assert len(names(''))==3
    assert len(names('?task_state=completed'))==1                # 作业状态筛选
    assert len(names('?task_state=pending'))==2
    assert len(names('?status=abnormal'))==1                    # 门店状态筛选
    assert len(names('?kw=alpha'))==1                           # 搜索
    assert '没有匹配门店' in client.get(base+'?kw=不存在的店名').text


def test_batch_grid_submit_keeps_the_active_filters(client,seed):
    with appdb.SessionLocal() as db:
        task,rt,gt,st=multi_batch(db,seed);db.commit()
    login(client,'worker')
    html=client.get(f"/my/work/batch-grids/{gt.id}?task_state=pending&kw=alpha").text
    response=client.post(f"/my/work/batch-stores/{st.id}/complete",data={
        '_ft':form_token(client),'csrf_token':csrf(html),'request_id':str(uuid.uuid4()),
        'revision':st.revision,'status':'normal','note':'完成',
        'kw':'alpha','task_state':'pending','status_filter':''},follow_redirects=False)
    assert response.status_code==303
    location=response.headers['location']
    assert '/my/work/batch-grids/' in location and 'task_state=pending' in location and 'kw=alpha' in location


def test_worker_can_update_location_accuracy_and_coordinates(client,seed):
    with appdb.SessionLocal() as db:
        task,rt,gt,st=multi_batch(db,seed);db.commit()
    login(client,'worker')
    page=client.get(f"/my/work/batch-grids/{gt.id}")
    response=client.post(f"/my/work/batch-stores/{st.id}/location",data={
        '_ft':form_token(client),'csrf_token':csrf(page.text),
        'location_accuracy':'accurate','lat':'35.7','lon':'139.8'})
    assert response.status_code==200
    assert response.json()['location_accuracy']=='accurate'
    assert response.json()['lat']==35.7 and response.json()['lon']==139.8
    with appdb.SessionLocal() as db:
        saved=db.get(BdStoreTask,st.id)
        assert saved.snapshot['lat']==35.7 and saved.snapshot['lon']==139.8
        assert saved.snapshot['location_accuracy']=='accurate'


def test_location_update_rejects_invalid_coordinate_range(client,seed):
    with appdb.SessionLocal() as db:
        task,rt,gt,st=multi_batch(db,seed);db.commit()
    login(client,'worker')
    page=client.get(f"/my/work/batch-grids/{gt.id}")
    response=client.post(f"/my/work/batch-stores/{st.id}/location",data={
        '_ft':form_token(client),'csrf_token':csrf(page.text),
        'location_accuracy':'accurate','lat':'35.7','lon':'181'})
    assert response.status_code==409
    with appdb.SessionLocal() as db:
        saved=db.get(BdStoreTask,st.id)
        assert saved.snapshot['lon']==seed['lon']


def test_batch_tree_page_filters_grids_by_state_and_keyword(client,seed):
    with appdb.SessionLocal() as db:
        task,rt,gt,st=multi_batch(db,seed);db.commit()
    login(client,'captain')
    html=client.get(f"/my/work/batches/{task.id}").text
    filters=re.search(r'<form class="(?:bd-filters|task-filters)[^"]*".*?</form>',html,re.S).group(0)
    assert 'data-autosubmit="1"' in filters and 'name="state"' in filters and 'name="kw"' in filters
    assert f'/my/work/batches/{task.id}/regions/{rt.id}' in html
    # 作业状态筛选：未指派筛不出已派发的网格
    filtered=client.get(f"/my/work/batches/{task.id}?state=unassigned").text
    assert f'/my/work/batches/{task.id}/regions/{rt.id}' not in filtered
    # 搜索：按网格编号
    found=client.get(f"/my/work/batches/{task.id}?kw={seed['grid']}").text
    assert f'/my/work/batches/{task.id}/regions/{rt.id}' in found


def test_batch_grid_location_requires_coordinates_and_never_uses_place_id(client,seed):
    with appdb.SessionLocal() as db:
        task,rt,gt,st=multi_batch(db,seed)
        st.store_id='ChIJ-test-place'
        st.snapshot={**st.snapshot, 'lat':None, 'lon':None, 'name':'Google shop'}
        db.commit()
    login(client,'worker')
    html=client.get(f"/my/work/batch-grids/{gt.id}").text
    assert 'query_place_id=ChIJ-test-place' not in html
    assert f'data-store-task-id="{st.id}"' not in html
    assert 'visit-nav-none' in html


def test_google_maps_location_fallbacks():
    from urllib.parse import urlparse, parse_qs
    from app.templating import gmaps_url
    assert parse_qs(urlparse(gmaps_url(35,139)).query)=={'api':['1'],'query':['35.000000,139.000000']}
    assert parse_qs(urlparse(gmaps_url(35,139,'new-local-id')).query)=={'api':['1'],'query':['35.000000,139.000000']}
    params=parse_qs(urlparse(gmaps_url(35,139,'ChIJ&special','shop')).query)
    assert params['query_place_id']==['ChIJ&special']
    for lat,lon in [(None,None),('bad',139),(91,139),(35,181),(float('nan'),139)]:
        assert gmaps_url(lat,lon)==''
    assert gmaps_url(None,None,'new-local-id','shop')==''
