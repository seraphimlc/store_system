"""Legacy station tasks and new batch tasks remain separate executable systems."""
import pytest
import app.db as appdb
from app.models import User, BdMainTask, BdStoreTask, BdStoreVisitLog
from app.services.home import landing_home
from tests_web.test_bd_zone_operations import seed, login
from tests_web.test_bd_hierarchical_tasks import batch


def test_station_task_entry_and_batch_entry_are_independent(client, seed):
    login(client, 'ops-admin')
    assert client.get('/stations').status_code == 200
    assert client.get('/tasks').status_code == 200
    login(client, 'worker')
    assert client.get('/my/tasks').status_code == 200
    assert 'data-testid="tab-tasks"' in client.get('/my/tasks').text
    assert client.get('/my/work').status_code == 200


@pytest.mark.parametrize('username', ['ops-admin', 'captain', 'worker'])
def test_direct_grid_execution_remains_retired(client, seed, username):
    login(client, username)
    paths = [f"/my/work/zones/{seed['zone']}",
             f"/my/work/grids/{seed['grid']}", f"/my/work/grids/{seed['grid']}/visit",
             f"/my/work/grids/{seed['grid']}/assign"]
    for path in paths:
        response = client.get(path, follow_redirects=False)
        # Removed routes return 404; unsupported methods return 405.
        assert response.status_code in (303, 404, 405), (path, response.text)
        response = client.post(path, data={'pct': 100}, follow_redirects=False)
        assert response.status_code in (410, 404, 405), (path, response.text)
    # 手填作业进展入口已下线：作业进展按巡店留痕自动算。
    retired = f"/my/work/grids/{seed['grid']}/progress"
    assert client.get(retired, follow_redirects=False).status_code == 404
    assert client.post(retired, data={'pct': 100}, follow_redirects=False).status_code == 404
    with appdb.SessionLocal() as db:
        assert db.query(BdMainTask).count() == 0
        assert db.query(BdStoreVisitLog).count() == 0


def test_batch_home_is_only_employee_task_entry(client, seed):
    with appdb.SessionLocal() as db:
        main, rt, grid, store = batch(db, seed, 'current-batch')
        identity = main.id
        db.commit()
    assert landing_home(db, db.get(User, seed['users']['captain'])) == '/my/tasks'
    login(client, 'worker')
    html = client.get('/my/work').text
    assert 'current-batch' in html and f'/my/work/batches/{identity}' in html
    assert 'active' not in html and '尚无分配给你的片区或网格' not in html
    assert 'data-testid="tab-work"' in html and 'data-testid="tab-tasks"' in html
    assert 'href="/my/tasks' in html and '/my/work/zones/' not in html
    with appdb.SessionLocal() as db:
        assert db.query(BdStoreTask).count() == 1
    login(client, 'ops-admin')
    html = client.get('/zone-tasks').text
    for entry in ('href="/stations"', 'href="/tasks"', 'href="/zones"', 'href="/zone-tasks"'):
        assert entry in html


def test_empty_batch_home(client, seed):
    login(client, 'worker')
    html = client.get('/my/work').text
    assert '暂无巡店批次' in html
    assert '尚无分配给你的片区或网格' not in html


def test_daily_report_keeps_station_progress_submission(client, seed):
    from app.services import self_report
    login(client, 'worker')
    page = client.get('/my/report')
    assert page.status_code == 200
    assert 'action="/my/report"' in page.text
    assert self_report.view is not None
    assert client.get('/my/tasks').status_code == 200
    assert 'data-testid="tab-tasks"' in page.text
