"""批次四级任务：隔离、权限、快照及店铺完成后的逐级汇总。"""
import uuid
from datetime import date
import json
from types import SimpleNamespace
import pytest
from shapely.geometry import box, mapping
from app.models import (User,BdRegion,BdWorkGrid,BdMainTask,BdRegionTask,BdGridTask,BdStoreTask,BdWorkStore)
from app.models import BdTaskEvent, BdTeamMember, BdTeam
import app.db as appdb
from app.services import bd_hierarchical_tasks as tasks,bd_zone_operations as ops
from app.services import bd_batch_auto_assign as auto_assign
from tests_web.test_bd_zone_operations import seed,login,csrf
from tests.helpers import form_token


def batch(db,seed,title='巡店批次'):
    admin=db.get(User,seed['users']['ops-admin'])
    task=tasks.create_main_task(db,admin,title,[seed['zone']]);db.flush()
    rt=db.query(BdRegionTask).filter_by(main_task_id=task.id).one()
    tasks.assign_region_task(db,admin,rt.id,seed['team'],rt.revision)
    gt=db.query(BdGridTask).filter_by(region_task_id=rt.id).one()
    tasks.assign_grid_task(db,db.get(User,seed['users']['captain']),gt.id,seed['users']['worker'],gt.revision)
    db.flush()
    st=db.query(BdStoreTask).filter_by(grid_task_id=gt.id).order_by(BdStoreTask.id).first()
    return task,rt,gt,st


@pytest.fixture
def mock_assign_ai(monkeypatch):
    calls=[]
    monkeypatch.setattr(auto_assign.ai_chat, 'configured', lambda: True)
    def chat(prompt, **kwargs):
        calls.append(prompt)
        payload=json.loads(prompt.split('\n',1)[1])
        return json.dumps({'weights': {str(m['member_id']):1.0 for m in payload['members']},
                           'reason':'综合当前负载和空间分布，保持均衡容量。'})
    monkeypatch.setattr(auto_assign.ai_chat, 'chat', chat)
    return calls


def test_auto_assign_preview_apply_keeps_batch_isolated_and_rejects_stale_plan(seed,mock_assign_ai):
    with appdb.SessionLocal() as db:
        main,rt,gt,_=batch(db,seed)
        captain=db.get(User,seed['users']['captain'])
        other_member=seed['users']['next-worker']
        gt.assignee_id=other_member
        gt.revision+=1
        preview=auto_assign.plan(db,captain,rt.id,include_batch=True,use_efficiency=False,
                                 member_ids=[seed['users']['worker'],other_member])
        assert preview['grid_count']==1
        assert preview['assignments'][str(gt.id)]==seed['users']['worker']
        assert sum(member['grid_count'] for member in preview['members'])==1
        gt.revision+=1
        with pytest.raises(ops.OperationError,match='预览数据已变化'):
            auto_assign.apply(db,captain,rt.id,preview['signature'],ai_decision=preview['ai_decision'],include_batch=True,
                              use_efficiency=False,
                              member_ids=[seed['users']['worker'],other_member])
        preview=auto_assign.plan(db,captain,rt.id,member_ids=[seed['users']['worker'],other_member])
        auto_assign.apply(db,captain,rt.id,preview['signature'],ai_decision=preview['ai_decision'],member_ids=[seed['users']['worker'],other_member])
        assert len(mock_assign_ai)==2  # Applying a preview never invokes the model.
        assert gt.assignee_id==seed['users']['worker']
        assert db.get(BdWorkGrid,seed['grid']).assignee_id is None
        assert db.query(BdTaskEvent).filter_by(event_type='auto_assign_grid').count()==1
        db.commit()


def test_auto_assign_keeps_each_members_grids_edge_connected():
    rows=[(SimpleNamespace(id=identity,snapshot={'geometry':mapping(box(identity,0,identity+1,1))}),
           None,10) for identity in range(6)]
    members=[SimpleNamespace(id=101),SimpleNamespace(id=102)]
    assignments=auto_assign._solve(rows,members,{101:0,102:0},{101:1,102:1},False)
    for owner in (101,102):
        owned=sorted(identity for identity,assigned_to in assignments.items() if assigned_to==owner)
        assert owned and owned==list(range(owned[0],owned[-1]+1))
    assert abs(sum(owner==101 for owner in assignments.values())-
               sum(owner==102 for owner in assignments.values()))<=1


def test_batch_isolated_results_rollup_immutable_snapshots_and_retry(seed):
    with appdb.SessionLocal() as db:
        first,rt,gt,st=batch(db,seed,'第一轮')
        second,rt2,gt2,st2=batch(db,seed,'第二轮')
        region=db.get(BdRegion,seed['zone']);region.name='基础片区改名'
        db.get(BdWorkStore,'shop-synthetic').name='基础门店改名';db.flush()
        assert rt.snapshot['name']=='synthetic' and st.snapshot['name']=='synthetic-shop'
        worker=db.get(User,seed['users']['worker'])
        request=str(uuid.uuid4())
        tasks.complete_store(db,worker,st.id,0,request,'abnormal','第一轮结果');db.flush()
        assert st.status==gt.status==rt.status==first.status=='completed'
        assert gt.progress==rt.progress==first.progress==100 and first.completed_at is not None
        assert second.status=='active' and gt2.progress==0 and st2.result=={}
        assert db.get(BdWorkGrid,seed['grid']).progress==0
        assert db.get(BdWorkStore,'shop-synthetic').status=='normal'
        assert (gt.total_stores,gt.completed_stores)==(1,1)
        assert (rt.progress,first.progress,rt.status,first.status)==(100,100,'completed','completed')
        assert tasks.complete_store(db,worker,st.id,0,request,'abnormal','第一轮结果')==gt.id
        with pytest.raises(ops.OperationError,match='冲突'):
            tasks.complete_store(db,worker,st.id,0,request,'normal','different')


def test_completed_store_result_edit_does_not_increment_cached_progress(seed):
    from sqlalchemy import event
    with appdb.SessionLocal() as db:
        main,rt,grid,store=batch(db,seed)
        db.add(BdStoreTask(grid_task_id=grid.id,store_id='second-store',snapshot={'name':'second'}))
        grid.total_stores=2
        db.flush()
        worker=db.get(User,seed['users']['worker'])
        tasks.complete_store(db,worker,store.id,store.revision,str(uuid.uuid4()),'normal','first')
        before=(grid.total_stores,grid.completed_stores,rt.progress,main.progress)
        statements=[]
        def capture(conn,cursor,statement,parameters,context,executemany):
            statements.append(statement.lower())
        event.listen(db.bind,'before_cursor_execute',capture)
        try:
            tasks.complete_store(db,worker,store.id,store.revision,str(uuid.uuid4()),'abnormal','edit')
        finally:
            event.remove(db.bind,'before_cursor_execute',capture)
        assert before==(2,1,50,50)
        assert (grid.total_stores,grid.completed_stores,rt.progress,main.progress)==before
        assert not any('count(bd_store_task' in statement for statement in statements)


def test_tree_and_store_tasks_deny_other_users_and_stale_assignments(seed):
    with appdb.SessionLocal() as db:
        main,rt,gt,st=batch(db,seed)
        outsider=db.get(User,seed['users']['outsider'])
        assert tasks.my_tasks(db,outsider)==[]
        with pytest.raises(ops.OperationError):tasks.task_tree(db,outsider,main.id)
        with pytest.raises(ops.OperationError):tasks.store_page(db,outsider,gt.id)
        with pytest.raises(ops.OperationError):tasks.complete_store(db,outsider,st.id,0,str(uuid.uuid4()),'normal','')
        with pytest.raises(ops.OperationError,match='已变更'):
            tasks.assign_grid_task(db,db.get(User,seed['users']['captain']),gt.id,seed['users']['next-worker'],0)
        with pytest.raises(ops.OperationError):
            tasks.assign_grid_task(db,db.get(User,seed['users']['captain']),gt.id,seed['users']['outsider'],gt.revision)
        assert tasks.my_tasks(db,db.get(User,seed['users']['worker']))[0].id==main.id
        assert tasks.my_tasks(db,db.get(User,seed['users']['captain']))[0].id==main.id


def test_large_batch_bulk_writes_keep_snapshots_defaults_and_atomicity(seed):
    from sqlalchemy import event, insert
    from app.models import BdGridStore
    with appdb.SessionLocal() as db:
        grid=db.get(BdWorkGrid,seed['grid'])
        db.execute(insert(BdWorkStore),[
            {'id':f'bulk-{i}','name':f'shop-{i}','address':'bulk-address',
             'lat':seed['lat'],'lon':seed['lon']} for i in range(2500)])
        db.execute(insert(BdGridStore),[
            {'release_id':grid.release_id,'grid_id':grid.id,'source_id':f'bulk-{i}'}
            for i in range(2500)])
        db.commit()
        sizes=[]
        def capture(conn,cursor,statement,parameters,context,executemany):
            if statement.startswith('INSERT INTO bd_store_task '):
                sizes.append(len(parameters) if executemany else 1)
        event.listen(db.bind,'before_cursor_execute',capture)
        try:
            task=tasks.create_main_task(db,db.get(User,seed['users']['ops-admin']),'bulk',[seed['zone']])
        finally:
            event.remove(db.bind,'before_cursor_execute',capture)
        assert sizes==[1000,1000,501]
        assert db.query(BdStoreTask).count()==2501
        row=db.query(BdStoreTask).filter_by(store_id='bulk-2499').one()
        assert row.snapshot=={'name':'shop-2499','address':'bulk-address','lat':seed['lat'],'lon':seed['lon']}
        assert row.status=='pending' and row.revision==0 and row.result=={}
        assert row.updated_at is not None and row.request_id is None
        assert db.query(BdTaskEvent).filter_by(main_task_id=task.id,event_type='create_batch').one().new_value['stores']==2501
        db.rollback()
        attempts=[]
        def fail_second_chunk(conn,cursor,statement,parameters,context,executemany):
            if statement.startswith('INSERT INTO bd_store_task '):
                attempts.append(1)
                if len(attempts)==2:raise RuntimeError('chunk failed')
        event.listen(db.bind,'before_cursor_execute',fail_second_chunk)
        try:
            with pytest.raises(RuntimeError,match='chunk failed'):
                tasks.create_main_task(db,db.get(User,seed['users']['ops-admin']),'failed',[seed['zone']])
        finally:
            event.remove(db.bind,'before_cursor_execute',fail_second_chunk)
        db.rollback()
        assert db.query(BdMainTask).count()==0 and db.query(BdStoreTask).count()==0


def test_creation_missing_store_fails_before_any_task_write(seed):
    from app.models import BdGridStore
    with appdb.SessionLocal() as db:
        admin=db.get(User,seed['users']['ops-admin'])
        source=db.query(BdGridStore).one();source.source_id='source-does-not-exist';db.flush()
        with pytest.raises(ops.OperationError,match='缺失'):
            tasks.create_main_task(db,admin,'missing',[seed['zone']])
        assert db.query(BdMainTask).count()==0


def test_http_admin_create_leader_dispatch_worker_complete(client,seed,mock_assign_ai):
    login(client,'ops-admin')
    page=client.get('/zone-tasks')
    assert page.status_code==200 and 'name="region_ids"' in page.text
    created=client.post('/zone-tasks',data={'_ft':form_token(client),'csrf_token':csrf(page.text),
        'title':'HTTP批次','region_ids':[seed['zone']]},follow_redirects=False)
    assert created.status_code==303,created.text
    with appdb.SessionLocal() as db:
        task=db.query(BdMainTask).one();identity=task.id
        rt=db.query(BdRegionTask).one();rid=rt.id
    page=client.get(f'/zone-tasks/{identity}')
    assigned=client.post(f'/zone-tasks/regions/{rid}/assign',data={'_ft':form_token(client),
        'csrf_token':csrf(page.text),'revision':0,'team_id':seed['team']},follow_redirects=False)
    assert assigned.status_code==303
    login(client,'captain')
    page=client.get(f'/my/work/batches/{identity}')
    assert page.status_code==200 and f'/regions/{rid}' in page.text
    region_page=client.get(f'/my/work/batches/{identity}/regions/{rid}')
    assert region_page.status_code==200 and 'name="target_id"' in region_page.text
    assert '智能分配' in region_page.text and 'batch-auto-drawer' in region_page.text
    body={'csrf_token':csrf(region_page.text),'member_ids':[seed['users']['worker'],seed['users']['next-worker']],
          'include_batch':True,'use_efficiency':False}
    preview=client.post(f'/my/work/batch-regions/{rid}/auto-preview',json=body)
    assert preview.status_code==200 and preview.json()['grid_count']==1
    assert preview.json()['assignments']
    applied=client.post(f'/my/work/batch-regions/{rid}/auto-apply',
        json={**body,'signature':preview.json()['signature'],'ai_decision':preview.json()['ai_decision']})
    assert applied.status_code==200 and applied.json()['ok']
    with appdb.SessionLocal() as db:
        grid_task=db.query(BdGridTask).one();gid=grid_task.id;grid_revision=grid_task.revision
    dispatched=client.post(f'/my/work/batch-grids/{gid}/assign',data={'_ft':form_token(client),
        'csrf_token':csrf(page.text),'revision':grid_revision,'target_id':seed['users']['worker']},follow_redirects=False)
    assert dispatched.status_code==303,dispatched.text
    login(client,'worker')
    assert 'HTTP批次' in client.get('/my/work').text
    page=client.get(f'/my/work/batch-grids/{gid}')
    assert page.status_code==200 and 'synthetic-shop' in page.text
    with appdb.SessionLocal() as db:sid=db.query(BdStoreTask).one().id
    done=client.post(f'/my/work/batch-stores/{sid}/complete',data={'_ft':form_token(client),
        'csrf_token':csrf(page.text),'revision':0,'request_id':str(uuid.uuid4()),'status':'normal','note':'done'},follow_redirects=False)
    assert done.status_code==303,done.text
    with appdb.SessionLocal() as db:assert db.get(BdMainTask,identity).status=='completed'
    login(client,'outsider')
    assert client.get(f'/my/work/batches/{identity}').status_code==403
    assert client.get(f'/my/work/batch-grids/{gid}').status_code==403


def test_revoked_membership_hides_batch_and_blocks_execution(seed):
    from app.models import BdTeamMember
    from datetime import date
    with appdb.SessionLocal() as db:
        main,rt,gt,st=batch(db,seed)
        worker=db.get(User,seed['users']['worker'])
        membership=db.query(BdTeamMember).filter_by(person_code=worker.person_code,role='member',end_date=None).one()
        membership.end_date=date.today();db.flush()
        assert tasks.my_tasks(db,worker)==[]
        with pytest.raises(ops.OperationError):tasks.task_tree(db,worker,main.id)
        with pytest.raises(ops.OperationError):tasks.complete_store(db,worker,st.id,0,str(uuid.uuid4()),'normal','')


def test_disabled_team_cannot_execute_by_direct_task_url(seed):
    from app.models import BdTeam
    with appdb.SessionLocal() as db:
        main,rt,gt,st=batch(db,seed)
        db.get(BdTeam,seed['team']).status='disabled';db.flush()
        worker=db.get(User,seed['users']['worker'])
        assert tasks.my_tasks(db,worker)==[]
        with pytest.raises(ops.OperationError,match='停用'):
            tasks.complete_store(db,worker,st.id,0,str(uuid.uuid4()),'normal','')


def test_task_tree_excludes_other_workers_grids_and_store_results(seed):
    from app.models import BdGridStore
    from shapely.geometry import box
    from app.services import bd_zones as z
    with appdb.SessionLocal() as db:
        other=BdWorkGrid(release_id=db.get(BdRegion,seed['zone']).release_id,zone_id=seed['zone'],
                         source_id='other-grid',geometry=z.geographic(box(3000,0,4000,1000)),
                         area_km2=1,store_count=0)
        db.add(other);db.flush()
        admin=db.get(User,seed['users']['ops-admin'])
        main=tasks.create_main_task(db,admin,'multiple',[seed['zone']]);db.flush()
        rt=db.query(BdRegionTask).filter_by(main_task_id=main.id).one()
        tasks.assign_region_task(db,admin,rt.id,seed['team'],rt.revision)
        assigned=db.query(BdGridTask).filter_by(region_task_id=rt.id,grid_id=seed['grid']).one()
        tasks.assign_grid_task(db,db.get(User,seed['users']['captain']),assigned.id,seed['users']['worker'],assigned.revision)
        _,tree=tasks.task_tree(db,db.get(User,seed['users']['worker']),main.id)
        assert len(tree)==1 and [g['grid'].id for g in tree[0]['grids']]==[seed['grid']]
        other_task=db.query(BdGridTask).filter_by(region_task_id=rt.id,grid_id=other.id).one()
        assert other_task.progress==100 and other_task.status=='completed'
        with pytest.raises(ops.OperationError):tasks.store_page(db,db.get(User,seed['users']['worker']),other_task.id)


def test_task_tree_query_count_does_not_grow_with_region_rows(seed):
    from sqlalchemy import event
    with appdb.SessionLocal() as db:
        main,rt,gt,st=batch(db,seed)
        admin=db.get(User,seed['users']['ops-admin'])
        statements=[]
        def count(*args):statements.append(args[2])
        event.listen(db.bind,'before_cursor_execute',count)
        try:
            tasks.task_tree(db,admin,main.id)
        finally:event.remove(db.bind,'before_cursor_execute',count)
        assert len(statements)<=4


def test_batch_history_prevents_grid_regeneration(seed):
    from app.services.bd_grid_geometry import repair_grids
    from app.services import bd_zones as z
    from shapely.geometry import box
    with appdb.SessionLocal() as db:
        main,rt,gt,st=batch(db,seed)
        base=db.get(BdWorkGrid,gt.grid_id);base.zone_id=None
        outer=BdWorkGrid(release_id=base.release_id,zone_id=None,source_id='history-outer',
            geometry=z.geographic(box(-100,-100,2100,1100).difference(box(0,0,2000,1000))),
            area_km2=.64,store_count=0)
        db.add(outer);db.flush()
        with pytest.raises(ops.OperationError,match='批次任务引用'):
            repair_grids(db,db.get(User,seed['users']['ops-admin']),apply=True)
        with pytest.raises(ops.OperationError,match='批次历史'):
            ops.replace_unused_zone_release(db,db.get(User,seed['users']['ops-admin']),{}, {})
        assert db.get(BdWorkGrid,gt.grid_id) is not None


def test_hierarchy_migration_matches_model_schema_and_reverts():
    import importlib.util
    from sqlalchemy import create_engine,inspect
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from app.db import Base
    spec=importlib.util.spec_from_file_location('hierarchy_migration','migrations/versions/e0a1b2c3d4e5_bd_hierarchical_tasks.py')
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    engine=create_engine('sqlite://')
    Base.metadata.create_all(engine)
    tables=['bd_store_task','bd_grid_task','bd_region_task','bd_main_task']
    with engine.begin() as connection:
        for table in tables:Base.metadata.tables[table].drop(connection)
        operations=Operations(MigrationContext.configure(connection))
        migration.op=operations
        migration.upgrade()
        follow_spec=importlib.util.spec_from_file_location(
            'single_atlas_migration','migrations/versions/b5c6d7e8f901_single_batch_atlas.py')
        follow=importlib.util.module_from_spec(follow_spec);follow_spec.loader.exec_module(follow)
        follow.op=operations
        follow.upgrade()
        progress_spec=importlib.util.spec_from_file_location(
            'grid_progress_migration','migrations/versions/c1d2e3f4a5b6_bd_grid_task_progress_counts.py')
        progress=importlib.util.module_from_spec(progress_spec);progress_spec.loader.exec_module(progress)
        progress.op=operations
        progress.upgrade()
        for table in tables:
            columns={col['name'] for col in inspect(connection).get_columns(table)}
            assert columns==set(Base.metadata.tables[table].columns.keys())
            assert {idx['name'] for idx in inspect(connection).get_indexes(table)}=={
                index.name for index in Base.metadata.tables[table].indexes}
        for column in ('total_stores','completed_stores'):
            assert column in {item['name'] for item in inspect(connection).get_columns('bd_grid_task')}
        progress.downgrade()
        follow.downgrade()
        migration.downgrade()
        assert not set(tables)&set(inspect(connection).get_table_names())


def test_ai_weights_change_actual_contiguous_assignment():
    rows=[(SimpleNamespace(id=i,snapshot={'geometry':mapping(box(i,0,i+1,1))}),None,10)
          for i in range(12)]
    members=[SimpleNamespace(id=101),SimpleNamespace(id=102)]
    base=auto_assign._solve(rows,members,{101:0,102:0},{101:1,102:1},False)
    tilted=auto_assign._solve(rows,members,{101:0,102:0},{101:1,102:1},False,
                              {'101':.75,'102':1.25})
    assert tilted!=base
    assert sum(owner==102 for owner in tilted.values())>sum(owner==102 for owner in base.values())
    for owner in (101,102):
        owned=sorted(grid for grid,assigned in tilted.items() if assigned==owner)
        assert owned==list(range(owned[0],owned[-1]+1))


def test_ai_missing_config_and_provider_failure_never_silently_fallback(seed,monkeypatch):
    with appdb.SessionLocal() as db:
        _,rt,_,_=batch(db,seed)
        user=db.get(User,seed['users']['captain'])
        monkeypatch.setattr(auto_assign.ai_chat,'configured',lambda:False)
        with pytest.raises(ops.OperationError,match='AI未配置'):
            auto_assign.plan(db,user,rt.id)
        monkeypatch.setattr(auto_assign.ai_chat,'configured',lambda:True)
        def fail(*args,**kwargs):raise RuntimeError('secret-provider-details')
        monkeypatch.setattr(auto_assign.ai_chat,'chat',fail)
        with pytest.raises(ops.OperationError,match='未自动回退') as exc:
            auto_assign.plan(db,user,rt.id)
        assert 'secret-provider-details' not in str(exc.value)
        assert db.query(BdTaskEvent).filter_by(event_type='auto_assign_grid').count()==0


@pytest.mark.parametrize('bad_weights',[{'999':1}, {}, {'3':True}, {'3':float('nan')}, {'3':4}])
def test_ai_rejects_invalid_member_or_weight(bad_weights):
    with pytest.raises(ops.OperationError,match='格式无效'):
        auto_assign._validate_decision({'weights':bad_weights,'reason':'test',
                                       'expires_at':int(auto_assign.time.time())+900},[3])


def test_ai_preview_cannot_be_tampered_or_applied_after_pending_count_changes(seed,mock_assign_ai):
    from copy import deepcopy
    with appdb.SessionLocal() as db:
        _,rt,gt,st=batch(db,seed)
        user=db.get(User,seed['users']['captain'])
        preview=auto_assign.plan(db,user,rt.id)
        tampered=deepcopy(preview['ai_decision'])
        tampered['weights'][str(seed['users']['worker'])]=1.25
        with pytest.raises(ops.OperationError,match='预览数据已变化'):
            auto_assign.apply(db,user,rt.id,preview['signature'],ai_decision=tampered)
        assert len(mock_assign_ai)==1
        st.status='completed';db.flush()  # Even if grid revision has not been rolled up yet.
        with pytest.raises(ops.OperationError,match='预览数据已变化'):
            auto_assign.apply(db,user,rt.id,preview['signature'],ai_decision=preview['ai_decision'])
        assert db.query(BdTaskEvent).filter_by(event_type='auto_assign_grid').count()==0
        with pytest.raises(ops.OperationError,match='缺少AI'):
            auto_assign.apply(db,user,rt.id,preview['signature'])


def test_ai_member_and_completed_batch_permissions_checked_before_model_call(seed,mock_assign_ai):
    with appdb.SessionLocal() as db:
        main,rt,gt,_=batch(db,seed)
        with pytest.raises(ops.OperationError):
            auto_assign.plan(db,db.get(User,seed['users']['worker']),rt.id)
        user=db.get(User,seed['users']['captain'])
        with pytest.raises(ops.OperationError):
            auto_assign.plan(db,user,rt.id,member_ids=[seed['users']['outsider']])
        main.status='completed'
        with pytest.raises(ops.OperationError,match='已完成'):
            auto_assign.plan(db,user,rt.id)
        assert not mock_assign_ai


def test_ai_preview_expires_and_malformed_json_is_rejected(seed,mock_assign_ai,monkeypatch):
    with appdb.SessionLocal() as db:
        _,rt,_,_=batch(db,seed)
        user=db.get(User,seed['users']['captain'])
        preview=auto_assign.plan(db,user,rt.id)
        now=auto_assign.time.time()
        monkeypatch.setattr(auto_assign.time,'time',lambda:now+901)
        with pytest.raises(ops.OperationError,match='已过期'):
            auto_assign.apply(db,user,rt.id,preview['signature'],ai_decision=preview['ai_decision'])
        assert len(mock_assign_ai)==1
        monkeypatch.setattr(auto_assign.ai_chat,'chat',lambda *args,**kwargs:'{"weights":')
        with pytest.raises(ops.OperationError,match='未自动回退'):
            auto_assign.plan(db,user,rt.id)


@pytest.mark.parametrize('account_role',['leader','staff'])
def test_captain_can_select_self_assign_and_complete_own_grid_http(client,seed,account_role):
    with appdb.SessionLocal() as db:
        main,rt,grid,store=batch(db,seed)
        captain=db.get(User,seed['users']['captain']);captain.role=account_role
        mid,rid,gid,sid,revision=main.id,rt.id,grid.id,store.id,grid.revision
        db.commit()
    login(client,'captain')
    page=client.get(f"/my/work/batches/{mid}/regions/{rid}")
    assert page.status_code==200
    import re
    select=re.search(r'<select name="target_id".*?</select>',page.text,re.S).group(0)
    assert f'value="{seed["users"]["captain"]}"' in select
    assert f'name="auto_member" value="{seed["users"]["captain"]}" checked' in page.text
    response=client.post(f"/my/work/batch-grids/{gid}/assign",data={
        '_ft':form_token(client),'csrf_token':csrf(page.text),'revision':revision,
        'target_id':seed['users']['captain']},follow_redirects=False)
    assert response.status_code==303,response.text
    page=client.get(f"/my/work/batch-grids/{gid}")
    assert page.status_code==200 and '保存巡店' in page.text
    response=client.post(f"/my/work/batch-stores/{sid}/complete",data={
        '_ft':form_token(client),'csrf_token':csrf(page.text),'revision':0,
        'request_id':str(uuid.uuid4()),'status':'normal','note':'队长巡店'},follow_redirects=False)
    assert response.status_code==303,response.text
    with appdb.SessionLocal() as db:
        assert db.get(BdGridTask,gid).assignee_id==seed['users']['captain']
        assert db.get(BdStoreTask,sid).actor_id==seed['users']['captain']
        for model,identity in [(BdGridTask,gid),(BdRegionTask,rid),(BdMainTask,mid)]:
            row=db.get(model,identity);assert row.status=='completed' and row.progress==100
        assert db.query(BdTaskEvent).filter_by(event_type='complete_store_task',
            actor_id=seed['users']['captain'],store_task_id=sid).count()==1


@pytest.mark.parametrize('only_captain',[False,True])
def test_smart_assignment_includes_captain_and_can_apply_self(seed,mock_assign_ai,only_captain):
    with appdb.SessionLocal() as db:
        main,rt,grid,store=batch(db,seed)
        captain=db.get(User,seed['users']['captain'])
        if only_captain:
            db.query(BdTeamMember).filter_by(team_id=seed['team'],role='member').update({'end_date':date.today()})
            db.flush()
        preview=auto_assign.plan(db,captain,rt.id)
        assert captain.id in {member['member_id'] for member in preview['members']}
        if only_captain:assert len(preview['members'])==1
        preview=auto_assign.plan(db,captain,rt.id,member_ids=[captain.id])
        assert preview['assignments']=={str(grid.id):captain.id}
        auto_assign.apply(db,captain,rt.id,preview['signature'],ai_decision=preview['ai_decision'],member_ids=[captain.id])
        assert len(mock_assign_ai)==2
        assert grid.assignee_id==captain.id
        tasks.complete_store(db,captain,store.id,store.revision,str(uuid.uuid4()),'normal','')
        assert main.progress==100


@pytest.mark.parametrize('restriction',['inactive','off_duty','left_team','handover','not_assignee','outsider','disabled_team'])
def test_captain_visit_permissions_remain_bound_to_current_membership_and_owner(seed,restriction):
    with appdb.SessionLocal() as db:
        main,rt,grid,store=batch(db,seed)
        captain=db.get(User,seed['users']['captain'])
        tasks.assign_grid_task(db,captain,grid.id,captain.id,grid.revision)
        actor=captain
        membership=db.query(BdTeamMember).filter_by(team_id=seed['team'],role='leader',end_date=None).one()
        if restriction=='inactive':captain.is_active=False
        elif restriction=='off_duty':captain.status='inactive'
        elif restriction=='left_team':membership.end_date=date.today()
        elif restriction=='handover':
            membership.role='member'
            db.add(BdTeamMember(team_id=seed['team'],person_code='next-captain',role='leader',start_date=date.today()))
        elif restriction=='not_assignee':
            tasks.assign_grid_task(db,captain,grid.id,seed['users']['worker'],grid.revision)
        elif restriction=='outsider':actor=db.get(User,seed['users']['outsider'])
        elif restriction=='disabled_team':db.get(BdTeam,seed['team']).status='inactive'
        db.flush()
        with pytest.raises(ops.OperationError):
            tasks.complete_store(db,actor,store.id,store.revision,str(uuid.uuid4()),'normal','')
        assert store.status!='completed' and main.progress==0
        if restriction in ('inactive','off_duty','left_team','disabled_team'):
            assert captain.id not in {account.id for account,_ in tasks.assignment_members(db,[seed['team']])}
        if restriction=='handover':
            assert tasks.my_tasks(db,captain)==[]
            with pytest.raises(ops.OperationError):tasks.store_page(db,captain,grid.id)
