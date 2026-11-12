"""旧的片区直达页已退役（批次制接管）；这里守门退役行为，UI 断言在批次网格页。"""
import re
import subprocess
from tests_web.test_bd_zone_operations import seed,login,csrf,assigned
from tests.helpers import form_token
import app.db as appdb
from app.models import BdWorkGrid


def test_retired_zone_page_redirects_and_batch_entry_still_works(client,seed):
    with appdb.SessionLocal() as db:
        assigned(db,seed);db.commit()
    login(client,'captain')
    retired=client.get(f'/my/work/zones/{seed["zone"]}',follow_redirects=False)
    assert retired.status_code==303 and retired.headers['location']=='/my/work'
    # 旧的网格派发写操作不再执行
    response=client.post(f'/my/work/grids/{seed["grid"]}/assign',data={
        '_ft':form_token(client),'csrf_token':'bad','revision':1,
        'target_id':seed['users']['next-worker']},follow_redirects=False)
    assert response.status_code in (303,410)
    with appdb.SessionLocal() as db:
        assert db.get(BdWorkGrid,seed['grid']).assignee_id==seed['users']['worker']


def test_member_sees_only_own_execution_action_in_batch_page(client,seed):
    """队员在批次网格页只看到自己的执行入口，看不到派发/召回。"""
    from tests_web.test_bd_hierarchical_tasks import batch
    with appdb.SessionLocal() as db:
        task,rt,gt,st=batch(db,seed);db.commit()
    login(client,'worker')
    page=client.get(f'/my/work/batch-grids/{gt.id}')
    assert page.status_code==200
    assert 'visit-row' in page.text
    assert f'action="/my/work/batch-stores/{st.id}/complete"' in page.text
    assert 'name="target_id"' not in page.text


def test_dispatch_row_and_map_interaction():
    result=subprocess.run(['node','tests_web/bd_dispatch_mock.cjs'],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr
