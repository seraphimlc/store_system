"""Merchant enrichment: all providers mocked; no live paid API/OSS calls."""
import io
import uuid
from unittest.mock import Mock
import pytest
from PIL import Image
import app.db as appdb
from app.models import User, BdStoreTask, BdMerchantDetail
from app.services import bd_hierarchical_tasks as tasks, bd_merchant_media as media
from tests_web.test_bd_zone_operations import seed, login, csrf
from tests_web.test_bd_visit_page import multi_batch
from tests.helpers import form_token


def photo():
    output=io.BytesIO(); Image.new('RGB',(20,20),'blue').save(output,'PNG');return output.getvalue()


@pytest.mark.parametrize('outcome', media.OUTCOMES)
def test_every_saved_outcome_counts_as_completed_visit(seed,outcome):
    with appdb.SessionLocal() as db:
        main,rt,grid,store=multi_batch(db,seed)
        user=db.get(User,seed['users']['worker'])
        media.save_visit(db,user,store.id,store.revision,str(uuid.uuid4()),'normal','memo',outcome,[],[])
        assert store.status=='completed' and store.result['outcome']==outcome
        assert tasks.grid_progress_stats(db,grid.id)['done']==1
        assert store.result['note']=='memo'
        assert main.status!='completed'


def test_three_photos_stored_on_merchant_and_result_not_source_identity(seed,monkeypatch):
    original_context=tasks.context
    lock_states=[]
    def tracked_context(*args,**kwargs):
        lock_states.append(kwargs.get('lock',False))
        return original_context(*args,**kwargs)
    monkeypatch.setattr(tasks,'context',tracked_context)
    upload=Mock(side_effect=[f'https://cdn.test/store-system-files/{i}.jpg' for i in range(3)])
    def upload_without_task_lock(*args):
        assert lock_states[-1] is False
        return upload(*args)
    monkeypatch.setattr(media,'upload_photo',upload_without_task_lock)
    with appdb.SessionLocal() as db:
        _,_,_,store=multi_batch(db,seed); original=store.store_id
        user=db.get(User,seed['users']['worker'])
        lock_states.clear()
        media.save_visit(db,user,store.id,store.revision,str(uuid.uuid4()),'normal','', 'undecided',[],[photo()]*3)
        assert store.store_id==original
        profile=db.get(BdMerchantDetail,original)
        assert len(profile.photo_urls)==3 and store.result['photo_urls']==profile.photo_urls
        assert lock_states[:2]==[False,True]
        assert all(args[0][1].startswith(b'\xff\xd8') for args in upload.call_args_list)
        keep=profile.photo_urls[:2]
        media.save_visit(db,user,store.id,store.revision,str(uuid.uuid4()),'normal','', 'incomplete',keep,[])
        assert profile.photo_urls==keep and store.status=='completed'
        assert upload.call_count==3


@pytest.mark.parametrize('kind',['too_many','spoof','invalid_bytes'])
def test_photo_validation_happens_before_upload_or_completion(seed,monkeypatch,kind):
    upload=Mock();monkeypatch.setattr(media,'upload_photo',upload)
    with appdb.SessionLocal() as db:
        _,_,_,store=multi_batch(db,seed);user=db.get(User,seed['users']['worker'])
        files=[photo()]*4 if kind=='too_many' else [b'not-an-image'] if kind=='invalid_bytes' else []
        keep=['https://attacker.test/x.jpg'] if kind=='spoof' else []
        with pytest.raises(media.ops.OperationError):
            media.save_visit(db,user,store.id,store.revision,str(uuid.uuid4()),'normal','', 'completed',keep,files)
        assert store.status=='pending';upload.assert_not_called()


def test_photo_upload_failure_keeps_visit_unsaved(seed,monkeypatch):
    def fail(*args):raise media.ops.OperationError('照片上传失败')
    monkeypatch.setattr(media,'upload_photo',fail)
    with appdb.SessionLocal() as db:
        _,_,_,store=multi_batch(db,seed);user=db.get(User,seed['users']['worker'])
        with pytest.raises(media.ops.OperationError,match='照片上传失败'):
            media.save_visit(db,user,store.id,store.revision,str(uuid.uuid4()),'normal','', 'completed',[],[photo()])
        assert store.status=='pending'


def test_coordinate_lookup_saved_separately_and_provider_failure_does_not_lose_location(seed,monkeypatch):
    original_context=tasks.context
    lock_states=[]
    def tracked_context(*args,**kwargs):
        lock_states.append(kwargs.get('lock',False))
        return original_context(*args,**kwargs)
    monkeypatch.setattr(tasks,'context',tracked_context)
    lookup=Mock(return_value={'google_place_id':'ChIJ-test','place_lookup_state':'candidate'})
    def lookup_without_task_lock(*args):
        assert lock_states[-1] is False
        return lookup(*args)
    monkeypatch.setattr(media,'lookup_place_id',lookup_without_task_lock)
    with appdb.SessionLocal() as db:
        _,_,_,store=multi_batch(db,seed);user=db.get(User,seed['users']['worker']); original=store.store_id
        lock_states.clear()
        result=tasks.update_location_accuracy(db,user,store.id,'accurate',35.712345,139.812345)
        assert result['google_place_id']=='ChIJ-test'
        assert lock_states[:2]==[False,True]
        assert db.get(BdMerchantDetail,original).google_place_id=='ChIJ-test'
        assert store.store_id==original
        tasks.update_location_accuracy(db,user,store.id,'accurate',35.712345,139.812345)
        assert lookup.call_count==1
        lookup.return_value={'google_place_id':None,'place_lookup_state':'failed'}
        result=tasks.update_location_accuracy(db,user,store.id,'accurate',35.712346,139.812346)
        assert result['place_lookup_state']=='failed' and result['lat']==35.712346
        assert db.get(BdMerchantDetail,original).google_place_id is None


def test_text_search_uses_id_only_mask_and_does_not_choose_first_ambiguous_result(monkeypatch):
    monkeypatch.setenv('GOOGLE_PLACES_API_KEY','synthetic')
    response=Mock();response.json.return_value={'places':[{'id':'one'},{'id':'two'}]}
    client=Mock();client.post.return_value=response
    context=Mock();context.__enter__=Mock(return_value=client);context.__exit__=Mock(return_value=False)
    monkeypatch.setattr(media.httpx,'Client',lambda **kwargs:context)
    result=media.lookup_place_id('synthetic shop',35.1,139.1)
    assert result=={'google_place_id':None,'place_lookup_state':'ambiguous'}
    kwargs=client.post.call_args.kwargs
    assert kwargs['headers']['X-Goog-FieldMask']=='places.id'
    assert kwargs['json']['textQuery']=='synthetic shop'
    assert kwargs['json']['locationBias']['circle']['center']['latitude']==35.1
    response.json.return_value={'places':[{'id':'one'}]}
    assert media.lookup_place_id('synthetic shop',35.1,139.1)['google_place_id']=='one'


def test_http_visit_layout_upload_and_safe_location(seed,client,monkeypatch):
    monkeypatch.setattr(media,'upload_photo',lambda *args:'https://cdn.test/store-system-files/saved.jpg')
    monkeypatch.setattr(media,'lookup_place_id',lambda *args:{'google_place_id':None,'place_lookup_state':'no_match'})
    with appdb.SessionLocal() as db:
        _,_,grid,store=multi_batch(db,seed);gid,sid,revision=grid.id,store.id,store.revision;db.commit()
    login(client,'worker');page=client.get(f'/my/work/batch-grids/{gid}')
    assert 'visit-store-id' not in page.text and 'visit-detail-address' in page.text
    assert 'data-location-editor' in page.text and 'name="outcome"' in page.text
    assert 'data-photo-camera' in page.text and 'capture="environment"' in page.text
    saved=client.post(f'/my/work/batch-stores/{sid}/complete',data={'_ft':form_token(client),
        'csrf_token':csrf(page.text),'revision':revision,'request_id':str(uuid.uuid4()),
        'status':'normal','outcome':'incomplete'},files={'photos':('shop.png',photo(),'image/png')},follow_redirects=False)
    assert saved.status_code==303,saved.text
    with appdb.SessionLocal() as db:
        task=db.get(BdStoreTask,sid)
        assert task.status=='completed' and task.result['outcome']=='incomplete'
        assert db.get(BdMerchantDetail,task.store_id).photo_urls==['https://cdn.test/store-system-files/saved.jpg']
    login(client,'outsider')
    rejected=client.post(f'/my/work/batch-stores/{sid}/complete',data={'_ft':form_token(client),
        'csrf_token':csrf(client.get('/my/work').text),'revision':revision,'request_id':str(uuid.uuid4()),
        'status':'normal','outcome':'completed'},follow_redirects=False)
    assert rejected.status_code==409


def test_oss_upload_uses_fixed_folder_and_retry_stable_key(monkeypatch):
    import oss2
    monkeypatch.setenv('STORE_FILES_OSS_ENDPOINT','https://oss-cn-shenzhen.aliyuncs.com')
    monkeypatch.setenv('STORE_FILES_OSS_BUCKET','xy-fe')
    monkeypatch.setenv('STORE_FILES_CDN_BASE','https://static.xyb2b.com')
    monkeypatch.setenv('OSS_ACCESS_KEY_ID','synthetic')
    monkeypatch.setenv('OSS_ACCESS_KEY_SECRET','synthetic')
    bucket=Mock();bucket.put_object.return_value.status=200
    monkeypatch.setattr(oss2,'Bucket',lambda *args,**kwargs:bucket)
    first=media.upload_photo('merchant/../../id',b'jpeg-bytes')
    second=media.upload_photo('merchant/../../id',b'jpeg-bytes')
    assert first==second and first.startswith('https://static.xyb2b.com/store-system-files/')
    assert '..' not in first and 'merchant' not in first
    assert bucket.put_object.call_args.args[0].startswith('store-system-files/')
    assert bucket.put_object.call_args.kwargs['headers']['Content-Type']=='image/jpeg'


def test_completed_store_location_audit_does_not_reuse_completion_request_id(seed,monkeypatch):
    monkeypatch.setattr(media,'lookup_place_id',lambda *args:{'google_place_id':'candidate','place_lookup_state':'candidate'})
    with appdb.SessionLocal() as db:
        _,_,_,store=multi_batch(db,seed);user=db.get(User,seed['users']['worker'])
        tasks.complete_store(db,user,store.id,store.revision,str(uuid.uuid4()),'normal','')
        snapshot=dict(store.snapshot)
        tasks.update_location_accuracy(db,user,store.id,'accurate',35.7,139.8)
        db.commit()
        assert store.snapshot==snapshot  # completed batch snapshot remains historical
        assert media.detail_map(db,[store.store_id])[store.store_id].lat==35.7


def test_cancelled_photo_selection_does_not_submit_an_empty_photo(seed,client,monkeypatch):
    upload=Mock();monkeypatch.setattr(media,'upload_photo',upload)
    with appdb.SessionLocal() as db:
        _,_,grid,store=multi_batch(db,seed);gid,sid,revision=grid.id,store.id,store.revision;db.commit()
    login(client,'worker');page=client.get(f'/my/work/batch-grids/{gid}')
    saved=client.post(f'/my/work/batch-stores/{sid}/complete',data={'_ft':form_token(client),
        'csrf_token':csrf(page.text),'revision':revision,'request_id':str(uuid.uuid4()),
        'status':'normal','outcome':'completed'},files={'photos':('',b'','application/octet-stream')},follow_redirects=False)
    assert saved.status_code==303,saved.text
    upload.assert_not_called()
    with appdb.SessionLocal() as db:
        assert db.get(BdStoreTask,sid).status=='completed'
