"""Disabled batches freeze mutations; visit age follows the merchant across batches."""
import subprocess
import uuid
from datetime import datetime, timedelta

import pytest
import app.db as appdb
from app.models import User, BdTaskEvent, BdRegion, BdWorkGrid
from app.services import bd_hierarchical_tasks as tasks, bd_zone_operations as ops
from app.services import bd_batch_auto_assign as auto, bd_store_proposals as proposals
from app.services import bd_merchant_media as media
from tests_web.test_bd_zone_operations import seed, login, csrf
from tests_web.test_bd_hierarchical_tasks import batch
from tests.helpers import form_token


@pytest.mark.parametrize('operation', ['region', 'grid', 'bulk', 'preview', 'apply', 'visit', 'location', 'photo', 'proposal', 'review', 'refresh'])
def test_disabled_batch_rejects_all_mutations(seed, operation):
    with appdb.SessionLocal() as db:
        main, rt, grid, store = batch(db, seed)
        admin = db.get(User, seed['users']['ops-admin'])
        captain = db.get(User, seed['users']['captain'])
        worker = db.get(User, seed['users']['worker'])
        with pytest.raises(ops.OperationError, match='管理员'):
            tasks.disable_batch(db, captain, main.id, main.revision)
        tasks.disable_batch(db, admin, main.id, main.revision)
        db.flush()
        actions = {
            'region': lambda: tasks.assign_region_task(db, admin, rt.id, None, rt.revision),
            'grid': lambda: tasks.assign_grid_task(db, captain, grid.id, None, grid.revision),
            'bulk': lambda: tasks.bulk_assign(db, captain, rt.id, [f'{grid.id}:{grid.revision}'], None),
            'preview': lambda: auto.plan(db, captain, rt.id),
            'apply': lambda: auto.apply(db, captain, rt.id, '', ai_decision={}),
            'visit': lambda: tasks.complete_store(db, worker, store.id, store.revision, str(uuid.uuid4()), 'normal', ''),
            'location': lambda: tasks.update_location_accuracy(db, worker, store.id, 'accurate', 35.6, 139.7),
            'photo': lambda: media.save_visit(db, worker, store.id, store.revision, str(uuid.uuid4()), 'normal', '', 'completed', [], []),
            'proposal': lambda: proposals.submit(db, worker, grid.id, 'name', 'addr', 35.6, 139.7, b'', str(uuid.uuid4())),
            'review': lambda: proposals.review(db, captain, main.id, [], 'approve'),
            'refresh': lambda: tasks.refresh_progress(db, main),
        }
        with pytest.raises(ops.OperationError, match='已禁用'):
            actions[operation]()
        assert main.status == 'disabled'
        assert grid.assignee_id == worker.id
        assert tasks.store_page(db, worker, grid.id)[0].id == main.id


def test_cancel_team_clears_grid_assignment_but_preserves_history_and_base(seed):
    with appdb.SessionLocal() as db:
        main, rt, grid, store = batch(db, seed)
        admin = db.get(User, seed['users']['ops-admin'])
        base_region = db.get(BdRegion, rt.region_id)
        base_grid = db.get(BdWorkGrid, grid.grid_id)
        baseline = (base_region.team_id, base_region.leader_id, base_grid.assignee_id)
        store.result = {'note': 'keep history'}
        store.status = 'completed'; grid.status = 'completed'
        old_revision = grid.revision
        tasks.assign_region_task(db, admin, rt.id, None, rt.revision)
        db.flush()
        assert rt.team_id is None and rt.leader_id is None
        assert grid.assignee_id is None and grid.revision > old_revision
        assert grid.status == 'completed' and store.result == {'note': 'keep history'}
        assert (base_region.team_id, base_region.leader_id, base_grid.assignee_id) == baseline
        with pytest.raises(ops.OperationError):
            tasks.store_page(db, db.get(User, seed['users']['worker']), grid.id)
        assert db.query(BdTaskEvent).filter_by(event_type='assign_region_task').order_by(BdTaskEvent.id.desc()).first().new_value['team_id'] is None


@pytest.mark.parametrize('days, expected', [(None, False), (59, False), (60, False), (60.001, True), (90, True)])
def test_visit_age_cross_batch_boundary_and_future_events(seed, monkeypatch, days, expected):
    now = datetime(2026, 11, 5, 12)
    class Clock(datetime):
        @classmethod
        def utcnow(cls):return now
    monkeypatch.setattr(tasks, 'datetime', Clock)
    with appdb.SessionLocal() as db:
        main, rt, grid, store = batch(db, seed)
        if days is not None:
            db.add(BdTaskEvent(main_task_id=main.id + 100, store_source_id=store.store_id,
                event_type='complete_store_task', created_at=now-timedelta(days=days)))
        # Location edits and impossible future visits must not reset the visit age.
        db.add_all([
            BdTaskEvent(main_task_id=main.id, store_source_id=store.store_id,
                event_type='update_store_location', created_at=now),
            BdTaskEvent(main_task_id=main.id, store_source_id=store.store_id,
                event_type='update_store_result', created_at=now+timedelta(days=1)),
        ])
        db.flush()
        rows = tasks.store_page(db, db.get(User, seed['users']['worker']), grid.id)[3]
        assert rows[0].overdue_60d is expected
        if days is not None:
            db.add(BdTaskEvent(main_task_id=main.id+101, store_source_id=store.store_id,
                event_type='update_store_result', created_at=now-timedelta(days=1)))
            db.flush()
            assert not tasks.store_page(db, db.get(User, seed['users']['worker']), grid.id)[3][0].overdue_60d


def test_http_disable_readonly_and_permissions(client, seed):
    with appdb.SessionLocal() as db:
        main, rt, grid, store = batch(db, seed)
        mid, rid, gid, revision = main.id, rt.id, grid.id, main.revision
        db.commit()
    login(client, 'captain')
    page = client.get(f'/my/work/batches/{mid}/regions/{rid}')
    rejected = client.post(f'/zone-tasks/{mid}/disable', data={
        '_ft': form_token(client), 'csrf_token': csrf(page.text), 'revision': revision}, follow_redirects=False)
    assert rejected.status_code == 302
    with appdb.SessionLocal() as db:
        assert db.get(tasks.BdMainTask, mid).status != 'disabled'
    login(client, 'ops-admin')
    page = client.get(f'/zone-tasks/{mid}')
    disabled = client.post(f'/zone-tasks/{mid}/disable', data={
        '_ft': form_token(client), 'csrf_token': csrf(page.text), 'revision': revision})
    assert disabled.status_code == 200 and '批次已禁用' in disabled.text
    assert f'action="/zone-tasks/regions/{rid}/assign"' not in disabled.text
    login(client, 'worker')
    page = client.get(f'/my/work/batch-grids/{gid}')
    assert page.status_code == 200
    assert 'class="visit-form"' not in page.text and 'class="visit-location-editor"' not in page.text
    assert 'work-location' in page.text


def test_http_cancel_team_and_render_old_visit_tag(client, seed):
    with appdb.SessionLocal() as db:
        main, rt, grid, store = batch(db, seed)
        mid, rid, gid, revision = main.id, rt.id, grid.id, rt.revision
        db.add(BdTaskEvent(main_task_id=main.id, store_source_id=store.store_id,
            event_type='complete_store_task', created_at=datetime.utcnow()-timedelta(days=61)))
        db.commit()
    login(client, 'worker')
    page = client.get(f'/my/work/batch-grids/{gid}')
    assert 'class="visit-overdue-tag"' in page.text and '>60d</span>' in page.text
    assert 'class="visit-status"' in page.text.split('class="visit-overdue-tag"')[0]
    login(client, 'ops-admin')
    page = client.get(f'/zone-tasks/{mid}')
    response = client.post(f'/zone-tasks/regions/{rid}/assign', data={
        '_ft': form_token(client), 'csrf_token': csrf(page.text), 'revision': revision, 'team_id': ''})
    assert response.status_code == 200, response.text
    with appdb.SessionLocal() as db:
        assert db.get(tasks.BdRegionTask, rid).team_id is None
        assert db.get(tasks.BdGridTask, gid).assignee_id is None


def test_location_watcher_contract():
    result = subprocess.run(['node', 'tests_web/bd_work_location_mock.cjs'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
