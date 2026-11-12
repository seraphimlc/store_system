import ast
import uuid
from pathlib import Path

import app.db as appdb
from app.models import BdTaskEvent, BdStoreTask, User
from app.services import bd_hierarchical_tasks as tasks
from tests_web.test_bd_visit_page import multi_batch, complete
from tests_web.test_bd_zone_operations import login, seed


def _action_literals(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.IfExp):
        return _action_literals(node.body) + _action_literals(node.orelse)
    return []


def test_bd_log_action_holds_audit_names():
    """bd_log.action 必须装下 audit_log / log_op 的操作名。SQLite 不截 VARCHAR。"""
    from app.models import BdLog
    limit = BdLog.__table__.c.action.type.length
    found = []
    for path in Path("app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            attr = func.attr if isinstance(func, ast.Attribute) else None
            ident = func.id if isinstance(func, ast.Name) else None
            if attr not in {"audit_log", "log_op"} and ident not in {"audit_log", "log_op"}:
                continue
            action = node.args[3] if len(node.args) >= 4 else None
            for kw in node.keywords:
                if kw.arg == "action":
                    action = kw.value
            found.extend(_action_literals(action) if action is not None else [])
    assert "region_auto_completed_empty" in found
    too_long = sorted({name for name in found if len(name) > limit})
    assert too_long == []


def test_batch_events_capture_store_and_parent_completion(client, seed):
    with appdb.SessionLocal() as db:
        batch, region, grid, first = multi_batch(db, seed)
        store_tasks = db.query(BdStoreTask).filter_by(grid_task_id=grid.id).order_by(BdStoreTask.id).all()
        for store in store_tasks:
            complete(db, seed, store)
        db.commit()
        batch_id = batch.id
        events = db.query(BdTaskEvent).filter_by(main_task_id=batch_id).all()
        kinds = {event.event_type for event in events}
        assert 'create_batch' in kinds
        assert kinds >= {'complete_store_task', 'grid_status_changed',
                         'region_status_changed', 'batch_status_changed'}
        assert all(event.actor_id and event.actor_label for event in events if event.event_type == 'complete_store_task')
        summary = tasks.completion_summary(db, batch_id)
        assert summary == {'first_completed': len(store_tasks), 'completion_events': len(store_tasks)}

    login(client, 'worker')
    response = client.get(f'/my/work/batches/{batch_id}/logs')
    assert response.status_code == 200
    assert '批次日志' in response.text and '商户首次完成' in response.text
    assert '期间首次完成商户数' in response.text and '期间完成事件数' in response.text


def test_batch_event_visibility_follows_current_scope(client, seed):
    with appdb.SessionLocal() as db:
        batch, region, grid, store = multi_batch(db, seed)
        complete(db, seed, store)
        db.commit()
        batch_id = batch.id

    login(client, 'worker')
    assert client.get(f'/my/work/batches/{batch_id}/logs').status_code == 200
    login(client, 'captain')
    assert client.get(f'/my/work/batches/{batch_id}/logs').status_code == 200
    login(client, 'ops-admin')
    assert client.get(f'/my/work/batches/{batch_id}/logs').status_code == 200
    assert client.get('/my/work/batches/999999/logs').status_code == 403


def test_report_counts_first_completion_and_repeat_completion_separately(seed):
    with appdb.SessionLocal() as db:
        batch, region, grid, store = multi_batch(db, seed)
        complete(db, seed, store)
        store.revision += 1
        db.flush()
        worker=db.get(User,seed['users']['worker'])
        tasks.complete_store(db,worker,store.id,store.revision,str(uuid.uuid4()),'abnormal','结果修订')
        db.commit()
        summary = tasks.completion_summary(db, batch.id)
        assert summary == {'first_completed': 1, 'completion_events': 1}


def test_batch_audit_filters_and_hierarchy_snapshots(client,seed):
    with appdb.SessionLocal() as db:
        batch, region, grid, first = multi_batch(db, seed)
        first=next(store for store in db.query(BdStoreTask).filter_by(grid_task_id=grid.id).all()
                   if store.snapshot.get('name')=='store-alpha')
        complete(db, seed, first)
        db.flush()
        admin=db.get(User,seed['users']['ops-admin'])
        result=tasks.task_events(db,admin,batch.id,keyword='store-alpha',
            actor_id=str(seed['users']['worker']),region_task_id=str(region.id),
            grid_task_id=str(grid.id),event_type='complete_store_task')
        (_task,rows,total,_page,_limit,metrics,actors,event_types,teams,regions,grids,filters)=result
        assert total==1 and len(rows)==1
        event=rows[0]
        assert event.main_label==batch.title
        assert event.region_label==region.snapshot['name']
        assert event.grid_label==str(grid.grid_id)
        assert event.store_label=='store-alpha'
        assert event.store_source_id==first.store_id
        assert metrics['current_total']==1 and metrics['current_done']==1
        assert metrics['first_completed']==1 and metrics['completion_events']==1
        assert metrics['performers'][0][1]
        assert filters['actor_id']==str(seed['users']['worker'])
        assert len(actors)>=1 and event_types and teams and regions and grids
        batch_id=batch.id
        db.commit()

        assignment=tasks.task_events(db,admin,batch.id,event_type='assign_grid_task')
        assert assignment[2]>=1
        assert assignment[5]['completion_events']==1
        assert assignment[5]['current_done']==1

        future=tasks.task_events(db,admin,batch.id,dfrom='2099-01-01')
        assert future[2]==0
        assert future[5]['first_completed']==0 and future[5]['completion_events']==0

    login(client,'ops-admin')
    page=client.get(f'/my/work/batches/{batch_id}/logs?kw=store-alpha&actor_id={seed["users"]["worker"]}')
    assert page.status_code==200
    assert 'store-alpha' in page.text and '当前已完成商户任务' in page.text
    assert page.text.count('data-testid="task-event-row"')==1
