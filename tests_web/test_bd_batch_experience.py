"""新任务体验的业务契约：数量加权、批量权限、新店核实及真实迁移。"""
import base64
import uuid
import pytest
from sqlalchemy import event
import app.db as appdb
from app.models import User,BdMainTask,BdRegionTask,BdGridTask,BdStoreTask,BdStoreProposal,BdWorkStore,BdGridStore
from app.services import bd_hierarchical_tasks as tasks,bd_store_proposals as proposals,bd_zone_operations as ops
from tests_web.test_bd_zone_operations import seed,login,csrf
from tests_web.test_bd_hierarchical_tasks import batch
from tests.helpers import form_token
PHOTO=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWQAAAABJRU5ErkJggg==')


def submit(db,seed,grid,name='新发现门店',lat=None,request_id=None):
    return proposals.submit(db,db.get(User,seed['users']['worker']),grid.id,name,'新店地址',
        lat if lat is not None else seed['lat']+0.001,seed['lon'],PHOTO,request_id or str(uuid.uuid4()))


def test_proposal_approval_adds_unvisited_store_once_and_changes_denominator(seed):
    with appdb.SessionLocal() as db:
        main,rt,grid,store=batch(db,seed)
        request_id=str(uuid.uuid4());p=submit(db,seed,grid,request_id=request_id)
        assert submit(db,seed,grid,request_id=request_id).id==p.id
        assert db.query(BdStoreTask).count()==1
        captain=db.get(User,seed['users']['captain'])
        assert proposals.review(db,captain,main.id,[f'{p.id}:{p.revision}'],'approve')==1
        db.flush()
        added=db.query(BdStoreTask).filter_by(store_id=p.store_id).one()
        assert added.status=='pending' and p.state=='approved'
        assert db.get(BdWorkStore,p.store_id) and db.query(BdGridStore).filter_by(source_id=p.store_id).count()==1
        assert tasks.grid_progress_stats(db,grid.id)['total']==2
        assert (grid.total_stores,grid.completed_stores)==(2,0)
        with pytest.raises(ops.OperationError):proposals.review(db,captain,main.id,[f'{p.id}:0'],'approve')
        assert db.query(BdStoreTask).count()==2


def test_rejection_resubmit_permissions_and_closed_batch(seed):
    with appdb.SessionLocal() as db:
        main,rt,grid,store=batch(db,seed);p=submit(db,seed,grid)
        with pytest.raises(ops.OperationError):proposals.review(db,db.get(User,seed['users']['worker']),main.id,[f'{p.id}:0'],'approve')
        captain=db.get(User,seed['users']['captain'])
        with pytest.raises(ops.OperationError):proposals.review(db,captain,main.id,[f'{p.id}:0'],'reject','')
        proposals.review(db,captain,main.id,[f'{p.id}:0'],'reject','照片不清晰')
        assert p.state=='rejected' and db.query(BdStoreTask).count()==1
        p=proposals.submit(db,db.get(User,seed['users']['worker']),grid.id,'修正后的新店','地址',
            seed['lat']+0.001,seed['lon'],PHOTO,str(uuid.uuid4()),p.id)
        assert p.state=='pending' and p.reason==''
        main.status='completed';db.flush()
        with pytest.raises(ops.OperationError,match='已完成'):proposals.review(db,captain,main.id,[f'{p.id}:{p.revision}'],'approve')


def test_proposal_rejects_out_of_grid_invalid_photo_duplicate_and_stale_batch(seed):
    with appdb.SessionLocal() as db:
        main,rt,grid,store=batch(db,seed);worker=db.get(User,seed['users']['worker'])
        with pytest.raises(ops.OperationError,match='网格'):submit(db,seed,grid,lat=0)
        with pytest.raises(ops.OperationError,match='照片'):proposals.submit(db,worker,grid.id,'x','address',seed['lat'],seed['lon'],b'<svg/>',str(uuid.uuid4()))
        p=submit(db,seed,grid,name=store.snapshot['name'])
        with pytest.raises(ops.OperationError,match='重复'):proposals.review(db,db.get(User,seed['users']['captain']),main.id,[f'{p.id}:0'],'approve')
        assert p.state=='pending' and db.query(BdStoreTask).count()==1


def test_rollup_weights_merchants_not_grids(seed):
    with appdb.SessionLocal() as db:
        main,rt,grid,store=batch(db,seed)
        # A second task region/grid with many stores: 1/10 must be 10%, not 50%.
        rt2=BdRegionTask(main_task_id=main.id,region_id=rt.region_id+999,snapshot=rt.snapshot)
        db.add(rt2);db.flush()
        gt2=BdGridTask(region_task_id=rt2.id,grid_id=grid.grid_id+999,snapshot=grid.snapshot)
        gt2.total_stores=9
        db.add(gt2);db.flush()
        for i in range(9):db.add(BdStoreTask(grid_task_id=gt2.id,store_id=f'weight-{i}',snapshot={'name':str(i)}))
        db.flush();tasks.complete_store(db,db.get(User,seed['users']['worker']),store.id,store.revision,str(uuid.uuid4()),'normal','')
        assert main.progress==10 and main.status=='active'
        _,tree=tasks.task_tree(db,db.get(User,seed['users']['ops-admin']),main.id)
        assert tasks.summarize(tree)=={'total':10,'done':1,'pct':10}


def test_bulk_assignment_validates_entire_selection_before_writes(seed):
    with appdb.SessionLocal() as db:
        main,rt,grid,store=batch(db,seed);captain=db.get(User,seed['users']['captain'])
        rev=grid.revision
        with pytest.raises(ops.OperationError):tasks.bulk_assign(db,captain,rt.id,[f'{grid.id}:{rev}','999999:0'],None)
        assert grid.assignee_id==seed['users']['worker'] and grid.revision==rev
        tasks.bulk_assign(db,captain,rt.id,[f'{grid.id}:{rev}'],None)
        assert grid.assignee_id is None
        with pytest.raises(ops.OperationError):tasks.bulk_assign(db,captain,rt.id,[f'{grid.id}:{grid.revision}'],seed['users']['outsider'])


def test_global_sort_and_map_focus_find_store_beyond_first_page(seed):
    with appdb.SessionLocal() as db:
        main,rt,grid,store=batch(db,seed);worker=db.get(User,seed['users']['worker'])
        for i in range(35):db.add(BdStoreTask(grid_task_id=grid.id,store_id=f'pagination-{i}',snapshot={'name':str(i)},status='completed' if i==34 else 'pending'))
        db.flush();last=db.query(BdStoreTask).order_by(BdStoreTask.id.desc()).first()
        result=tasks.store_page(db,worker,grid.id,sort='visit',direction='desc')
        assert result[3][0].id==last.id
        result=tasks.store_page(db,worker,grid.id,focus=last.id)
        assert last.id in [s.id for s in result[3]] and result[5]['page']==2


def test_http_new_store_review_private_photo_and_role_navigation(client,seed):
    with appdb.SessionLocal() as db:
        main,rt,grid,store=batch(db,seed);db.commit();mid,rid,gid=main.id,rt.id,grid.id
    login(client,'worker');page=client.get(f'/my/work/batch-grids/{gid}/new-store?kw=abc&task_state=pending&status=normal&page=2')
    assert page.status_code==200 and 'data-new-store-page' in page.text
    grid_page=client.get(f'/my/work/batch-grids/{gid}')
    assert '按店铺任务自动统计' not in grid_page.text
    assert 'id="new-store"' not in grid_page.text
    assert f'/my/work/batch-grids/{gid}/new-store' in grid_page.text
    response=client.post(f'/my/work/batch-grids/{gid}/new-store',data={'_ft':form_token(client),
        'csrf_token':csrf(page.text),'request_id':str(uuid.uuid4()),'name':'HTTP新门店','address':'HTTP地址',
        'kw':'abc','task_state':'pending','status_filter':'normal','page':'2',
        'lat':seed['lat']+0.001,'lon':seed['lon']},files={'photo':('photo.png',PHOTO,'image/png')},follow_redirects=False)
    assert response.status_code==303,response.text
    assert response.headers['location'].startswith(f'/my/work/batch-grids/{gid}?')
    assert 'kw=abc' in response.headers['location'] and 'status=normal' in response.headers['location']
    assert '新店已提交' in client.get(response.headers['location']).text
    with appdb.SessionLocal() as db:pid=db.query(BdStoreProposal).one().id
    assert client.get(f'/my/work/new-stores/{pid}/photo').content==PHOTO
    worker_page=client.get(f'/my/work/batches/{mid}').text
    assert f'/my/work/batch-grids/{gid}' in worker_page and '任务树' not in worker_page
    login(client,'outsider')
    assert client.get(f'/my/work/new-stores/{pid}/photo').status_code==403
    assert client.get(f'/my/work/batch-grids/{gid}/new-store').status_code==403
    login(client,'captain');page=client.get(f'/my/work/batches/{mid}/new-stores')
    assert page.status_code==200 and '批量通过' in page.text
    result=client.post(f'/my/work/batches/{mid}/new-stores/review',data={'_ft':form_token(client),
        'csrf_token':csrf(page.text),'selections':[f'{pid}:0'],'action':'approve'},follow_redirects=False)
    assert result.status_code==303,result.text
    captain_page=client.get(f'/my/work/batches/{mid}/regions/{rid}')
    assert captain_page.status_code==200 and 'grid-assignment' in captain_page.text
    login(client,'ops-admin')
    assert '团队完成数量排名' in client.get(f'/zone-tasks/{mid}').text


def test_proposal_migration_runs_and_reverts():
    import importlib.util
    from sqlalchemy import create_engine,inspect
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from app.db import Base
    spec=importlib.util.spec_from_file_location('proposal_migration','migrations/versions/e1b2c3d4e5f6_bd_store_proposals.py')
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    engine=create_engine('sqlite://');Base.metadata.create_all(engine)
    with engine.begin() as connection:
        BdStoreProposal.__table__.drop(connection)
        migration.op=Operations(MigrationContext.configure(connection));migration.upgrade()
        assert {c['name'] for c in inspect(connection).get_columns('bd_store_proposal')}==set(BdStoreProposal.__table__.columns.keys())
        migration.downgrade();assert 'bd_store_proposal' not in inspect(connection).get_table_names()


def test_create_modal_navigation_and_inline_region_dispatch(client,seed):
    with appdb.SessionLocal() as db:
        main,rt,grid,store=batch(db,seed);db.commit();mid,rid,gid,revision=main.id,rt.id,grid.id,grid.revision
    login(client,'ops-admin')
    page=client.get('/zone-tasks')
    assert '<dialog id="create-batch"' in page.text and 'data-create-batch' in page.text
    assert '<details id="create-batch"' not in page.text
    header=page.text.split('<header class="topbar">',1)[1].split('</header>',1)[0]
    assert 'topbar-brand' in header and '巡店结算系统' in header
    assert 'work-nav-title' not in header
    login(client,'captain')
    page=client.get(f'/my/work/batches/{mid}/regions/{rid}?owner={seed["users"]["worker"]}')
    assert page.status_code==200
    header=page.text.split('<header class="topbar">',1)[1].split('</header>',1)[0]
    assert 'dispatch-title' in header and f'/my/work/batches/{mid}' in header
    assert 'name="owner"' in page.text and 'name="state"' in page.text
    assert 'selection-mode' not in page.text and 'name="selections"' not in page.text
    assert '召回网格' not in page.text and '确认派发' not in page.text
    assert 'dispatch-columns' in page.text and f'grid-detail-{gid}' in page.text
    assert f'action="/my/work/batch-grids/{gid}/assign"' in page.text
    assert 'name="target_id" required' in page.text
    result=client.post(f'/my/work/batch-grids/{gid}/assign',data={'_ft':form_token(client),
        'csrf_token':csrf(page.text),'revision':revision,'target_id':seed['users']['worker'],
        'region_back':'1','state_filter':'assigned','owner_filter':str(seed['users']['worker'])},follow_redirects=False)
    assert result.status_code==303,result.text
    assert result.headers['location']==f'/my/work/batches/{mid}/regions/{rid}?state=assigned&owner={seed["users"]["worker"]}'


def test_batch_ui_interaction_contracts():
    import subprocess
    result=subprocess.run(['node','tests_web/bd_batch_ui_mock.cjs'],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_admin_region_team_handover_is_inline_and_names_reset_effect(client,seed):
    with appdb.SessionLocal() as db:
        main,rt,grid,store=batch(db,seed);db.commit();mid,rid=main.id,rt.id
    login(client,'ops-admin')
    html=client.get(f'/zone-tasks/{mid}').text
    row=html.split(f'<article class="batch-admin-region">',1)[1].split('</article>',1)[0]
    assert f'action="/zone-tasks/regions/{rid}/assign"' in row
    assert 'data-current-team="' in row and 'data-confirm-message=' in row
    assert 'batch-region-handover' in row and 'name="revision"' in row
    assert '取消团队归属' in row and '更新团队' in row
    content=row.split('<div class="batch-region-content"',1)[1]
    assert 'batch-region-handover' not in content
    assert '清除该片区所有网格的现有队员分配' in row
    result=__import__('subprocess').run(['node','tests_web/bd_region_handover_mock.cjs'],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_assignment_filters_follow_team_for_admin_and_worker_for_captain(client, seed):
    with appdb.SessionLocal() as db:
        main, rt, grid, store = batch(db, seed)
        grid.assignee_id = None
        grid.status = 'pending'
        db.commit()
        mid, rid = main.id, rt.id
        admin = db.get(User, seed['users']['ops-admin'])
        captain = db.get(User, seed['users']['captain'])
        assert tasks.task_tree(db, admin, mid, task_state='unassigned')[1] == []
        assigned = tasks.task_tree(db, admin, mid, task_state='assigned')[1]
        assert [item['task'].id for item in assigned] == [rid]
        assert [child['task'].id for child in assigned[0]['grids']] == [grid.id]
        unassigned = tasks.task_tree(db, captain, mid, task_state='unassigned')[1]
        assert [child['task'].id for child in unassigned[0]['grids']] == [grid.id]
        assert tasks.task_tree(db, captain, mid, task_state='assigned')[1][0]['grids'] == []
    login(client, 'ops-admin')
    assert 'batch-admin-region' not in client.get(f'/zone-tasks/{mid}?state=unassigned').text.split('<body', 1)[1]
    assert f'action="/zone-tasks/regions/{rid}/assign"' in client.get(f'/zone-tasks/{mid}?state=assigned').text
    with appdb.SessionLocal() as db:
        rt = db.get(BdRegionTask, rid)
        tasks.assign_region_task(db, db.get(User, seed['users']['ops-admin']), rid, None, rt.revision)
        db.commit()
    assert f'action="/zone-tasks/regions/{rid}/assign"' in client.get(f'/zone-tasks/{mid}?state=unassigned').text
    assert f'action="/zone-tasks/regions/{rid}/assign"' not in client.get(f'/zone-tasks/{mid}?state=assigned').text
