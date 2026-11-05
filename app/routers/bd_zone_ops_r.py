"""正式片区管理与H5网格执行；角色/团队/归属/CSRF均在服务端校验。"""
import uuid
import json
from urllib.parse import quote, urlencode
from typing import Optional
from fastapi import APIRouter,Depends,Request,Form,HTTPException,File,UploadFile
from fastapi.responses import HTMLResponse,RedirectResponse,JSONResponse
from sqlalchemy.orm import Session
from app.db import get_db
from app.models import User,BdZone,BdWorkGrid,BdTeam,BdTeamMember,BdZoneRelease,BdStoreVisitLog,BdMainTask,BdRegionTask
from app.routers.auth_r import require_login,csrf_ok
from app.forms import require_form_token
from app.config import get_settings
from app.services import bd_zone_operations as ops,bd_zones
from app.services import bd_grid_regions
from app.services import bd_hierarchical_tasks as task_ops
from app.templating import get_templates

router=APIRouter(dependencies=[Depends(require_form_token)])
templates=get_templates()


def work_login(user:Optional[User]=Depends(require_login)):
    if user is None:raise HTTPException(status_code=302,headers={'Location':'/login'})
    return user


def error(exc,status=403):return HTMLResponse(str(exc),status_code=status)


def render(request,user,template,**data):
    return templates.TemplateResponse(template,{'request':request,'current_user':user,
        'maps_key':get_settings().google_public_api_key,**data})


def _grid_query(grid_id,kw='',visit='',status=''):
    """巡店页回跳地址：**保留列表筛选**（提交后不该把筛选条件丢掉）。"""
    pairs=[('kw',kw),('visit',visit),('status',status)]
    query=('?'+urlencode([(k,v) for k,v in pairs if v])) if any(v for _,v in pairs) else ''
    return f'/my/work/grids/{grid_id}{query}'


@router.get('/my/work/batches/{task_id}/logs', response_class=HTMLResponse)
def batch_logs(task_id:int,request:Request,page:int=1,kw:str='',actor_id:str='',
               team_id:str='',region_task_id:str='',grid_task_id:str='',event_type:str='',
               dfrom:str='',dto:str='',
               user:Optional[User]=Depends(work_login),db:Session=Depends(get_db)):
    try:
        (task,rows,total,page,limit,metrics,actors,event_types,teams,regions,grids,
         filters)=task_ops.task_events(db,user,task_id,page,keyword=kw,actor_id=actor_id,
            team_id=team_id,
            region_task_id=region_task_id,grid_task_id=grid_task_id,event_type=event_type,
            dfrom=dfrom,dto=dto)
        return render(request,user,'bd_task_events.html',task=task,rows=rows,total=total,
                      page=page,limit=limit,metrics=metrics,actors=actors,
                      event_types=event_types,teams=teams,regions=regions,grids=grids,filters=filters)
    except ops.OperationError as exc:
        return error(exc)


@router.get('/zone-tasks', response_class=HTMLResponse)
def task_list(request:Request,user:Optional[User]=Depends(work_login),db:Session=Depends(get_db)):
    try:ops.require_admin(user)
    except ops.OperationError as exc:return error(exc)
    regions=db.query(BdZone).filter(BdZone.state!='archived').order_by(BdZone.id).all()
    tasks=db.query(BdMainTask).order_by(BdMainTask.id.desc()).all()
    return render(request,user,'bd_main_tasks.html',regions=regions,tasks=tasks,summaries=task_ops.batch_summaries(db,user,tasks))


@router.post('/zone-tasks')
def task_create(request:Request,title:str=Form(...),region_ids:list[int]=Form(...),csrf_token:str=Form(''),
                user:Optional[User]=Depends(work_login),db:Session=Depends(get_db)):
    try:
        post_guard(request,user,csrf_token)
        task=task_ops.create_main_task(db,user,title,region_ids);db.commit()
    except ops.OperationError as exc:
        db.rollback();return error(exc,409)
    return RedirectResponse(f'/zone-tasks/{task.id}',status_code=303)


@router.get('/zone-tasks/{task_id}', response_class=HTMLResponse)
def task_detail(task_id:int,request:Request,state:str='',kw:str='',
                user:Optional[User]=Depends(work_login),db:Session=Depends(get_db)):
    try: task,tree=task_ops.task_tree(db,user,task_id,task_state=state,keyword=kw)
    except ops.OperationError as exc:return error(exc,403)
    team_ids={item['task'].team_id for item in tree if item['task'].leader_id==user.id}
    members=db.query(User,BdTeamMember.team_id).join(BdTeamMember,BdTeamMember.person_code==User.person_code).filter(
        BdTeamMember.team_id.in_(team_ids),BdTeamMember.role=='member',BdTeamMember.end_date.is_(None),
        User.is_active.is_(True),User.status=='active',User.role.in_(('staff','leader'))).all() if team_ids else []
    members_by_team={identity:[account for account,team in members if team==identity] for identity in team_ids}
    names={account.id:account.display_name or account.username for account in db.query(User).filter(User.id.in_(
        {g['task'].assignee_id for item in tree for g in item['grids'] if g['task'].assignee_id})).all()}
    teams=ops.management_context(db,user,with_geometry=False)['teams'] if user.role=='admin' else []
    _,full_tree=task_ops.task_tree(db,user,task_id) if state or kw else (task,tree)
    stats=task_ops.summarize(full_tree)
    task_ops.summarize(tree)
    if state or kw:tree=[item for item in tree if item['grids']]
    team_names={tm.id:tm.name for tm in db.query(BdTeam).all()}
    ranking={}
    for item in full_tree:
        key=item['task'].team_id
        item['team_name']=team_names.get(key,'')
        if key:
            entry=ranking.setdefault(key,{'name':team_names.get(key,''),'total':0,'done':0})
            entry['total']+=item['total'];entry['done']+=item['done']
    for item in tree:item['team_name']=team_names.get(item['task'].team_id,'')
    for entry in ranking.values():entry['pct']=entry['done']*100//entry['total'] if entry['total'] else 0
    return render(request,user,'bd_main_task_detail.html',task=task,tree=tree,teams=teams,stats=stats,
                  count_rank=sorted(ranking.values(),key=lambda r:(-r['done'],r['name'])),
                  rate_rank=sorted(ranking.values(),key=lambda r:(-(r['done']/r['total'] if r['total'] else 0),-r['done'],r['name'])),
                  members_by_team=members_by_team,names=names,state=state,kw=kw)


@router.get('/my/work/batches/{task_id}', response_class=HTMLResponse)
def work_batch(task_id:int,request:Request,state:str='',kw:str='',
               user:Optional[User]=Depends(work_login),db:Session=Depends(get_db)):
    return task_detail(task_id,request,state,kw,user,db)


@router.post('/zone-tasks/regions/{identity}/assign')
def batch_region_assign(identity:int,request:Request,team_id:int=Form(...),revision:int=Form(...),csrf_token:str=Form(''),
                        user:Optional[User]=Depends(work_login),db:Session=Depends(get_db)):
    try:
        post_guard(request,user,csrf_token)
        task_id=task_ops.assign_region_task(db,user,identity,team_id,revision);db.commit()
    except ops.OperationError as exc:db.rollback();return error(exc,409)
    return RedirectResponse(f'/zone-tasks/{task_id}',status_code=303)


@router.post('/my/work/batch-grids/{identity}/assign')
def batch_grid_assign(identity:int,request:Request,target_id:Optional[int]=Form(None),revision:int=Form(...),csrf_token:str=Form(''),
                      region_back:bool=Form(False),state_filter:str=Form(''),owner_filter:str=Form(''),
                      user:Optional[User]=Depends(work_login),db:Session=Depends(get_db)):
    try:
        post_guard(request,user,csrf_token)
        task_id=task_ops.assign_grid_task(db,user,identity,target_id,revision)
        region_id=db.get(task_ops.BdGridTask,identity).region_task_id
        db.commit()
    except ops.OperationError as exc:db.rollback();return error(exc,409)
    if region_back:
        query=urlencode({k:v for k,v in [('state',state_filter),('owner',owner_filter)] if v})
        return RedirectResponse(f'/my/work/batches/{task_id}/regions/{region_id}'+('?' + query if query else ''),status_code=303)
    return RedirectResponse(f'/my/work/batches/{task_id}',status_code=303)


@router.get('/my/work/batch-grids/{identity}',response_class=HTMLResponse)
def batch_grid_page(identity:int,request:Request,page:int=1,kw:str='',task_state:str='',status:str='',sort:str='',direction:str='asc',focus:Optional[int]=None,resubmit:Optional[int]=None,
                    user:Optional[User]=Depends(work_login),db:Session=Depends(get_db)):
    try:
        main,rt,grid,stores,more,stats=task_ops.store_page(db,user,identity,page,kw,
                                                            task_state=task_state,status=status,sort=sort,direction=direction,focus=focus)
    except ops.OperationError as exc:return error(exc)
    # 地图：只用**本批次快照**里的网格几何，不回读基础网格主档。
    geometry={'type':'FeatureCollection','features':[{'type':'Feature',
        'geometry':grid.snapshot.get('geometry'),'properties':{'id':grid.grid_id}}]}
    grid_stores=db.query(task_ops.BdStoreTask).filter_by(grid_task_id=grid.id).all()
    location_map=task_ops.merchant_location_map(db,[s.store_id for s in grid_stores])
    points=[{'id':s.id,'name':s.snapshot.get('name'),
             'lat':location_map.get(s.store_id,{}).get('lat',s.snapshot.get('lat')),
             'lon':location_map.get(s.store_id,{}).get('lon',s.snapshot.get('lon')),
             'visited':s.status=='completed'} for s in grid_stores]
    if resubmit:
        return RedirectResponse(f'/my/work/batch-grids/{identity}/new-store?'+urlencode(dict(request.query_params)),303)
    return render(request,user,'bd_batch_grid.html',task=main,region_task=rt,grid=grid,stores=stores,more=more,
                  page=stats['page'],kw=kw,task_state=task_state,status=status,stats=stats,sort=sort,direction=direction,can_execute=grid.assignee_id==user.id and main.status!='completed',
                  geometry=geometry,points=points,nonce_for=lambda:str(uuid.uuid4()))


@router.post('/my/work/batch-stores/{identity}/complete')
async def batch_store_complete(identity:int,request:Request,revision:int=Form(...),request_id:str=Form(...),
                         status:str=Form(...),note:str=Form(''),outcome:str=Form('completed'),
                         photos:list[UploadFile|str]=File([]),keep_photos:list[str]=Form([]),csrf_token:str=Form(''),
                         kw:str=Form(''),task_state:str=Form(''),status_filter:str=Form(''),sort:str=Form(''),direction:str=Form('asc'),page:int=Form(1),
                         user:Optional[User]=Depends(work_login),db:Session=Depends(get_db)):
    try:
        post_guard(request,user,csrf_token)
        from app.services import bd_merchant_media as media
        if any(isinstance(photo,str) and photo for photo in photos):raise ops.OperationError('照片格式无效')
        selected_photos=[photo for photo in photos if not isinstance(photo,str) and photo.filename]
        if len(selected_photos)>media.MAX_PHOTOS:raise ops.OperationError('每家店铺最多保存3张照片')
        data=[await photo.read(media.MAX_PHOTO_BYTES+1) for photo in selected_photos]
        from starlette.concurrency import run_in_threadpool
        grid_id=await run_in_threadpool(media.save_visit,db,user,identity,revision,request_id,status,note,outcome,keep_photos,data)
        db.commit()
    except ops.OperationError as exc:db.rollback();return error(exc,409)
    finally:
        for photo in photos:
            if not isinstance(photo,str):await photo.close()
    # 提交后回跳要**保留列表筛选**
    pairs=[('kw',kw),('task_state',task_state),('status',status_filter),('sort',sort),('direction',direction),('page',page)]
    query=('?'+urlencode([(k,v) for k,v in pairs if v])) if any(v for _,v in pairs) else ''
    return RedirectResponse(f'/my/work/batch-grids/{grid_id}{query}',status_code=303)


@router.post('/my/work/batch-stores/{identity}/location')
def batch_store_location(identity:int,request:Request,location_accuracy:str=Form(...),
                         lat:Optional[float]=Form(None),lon:Optional[float]=Form(None),
                         csrf_token:str=Form(''),user:Optional[User]=Depends(work_login),db:Session=Depends(get_db)):
    try:
        post_guard(request,user,csrf_token)
        result=task_ops.update_location_accuracy(db,user,identity,location_accuracy,lat,lon)
        db.commit()
        return JSONResponse({'ok':True,**result})
    except ops.OperationError as exc:
        db.rollback()
        return JSONResponse({'error':str(exc)},status_code=409)


def post_guard(request,user,csrf):
    ops.require_active(user)
    if not csrf_ok(request,csrf):raise ops.OperationError('CSRF校验失败')


def zone_location(db,zone_id):
    zone=db.get(BdZone,zone_id)
    release=db.get(BdZoneRelease,zone.release_id)
    region=release.source.get('region_code','tokyo23')
    return f'/zones?region={region}&zone={zone_id}'


@router.get('/zones/manage',response_class=HTMLResponse)
def manage(request:Request,zone:Optional[int]=None,user:Optional[User]=Depends(work_login),db:Session=Depends(get_db)):
    mode=request.query_params.get('mode','assign')
    if mode not in ('assign','manage'):return error('模式不存在',404)
    region=request.query_params.get('region','tokyo23')
    regions=[('all','全部'),('tokyo23','东京23区'),('tokyo26','东京26市'),('kanagawa','神奈川'),('saitama','埼玉'),('chiba','千叶')]
    if region not in dict(regions):return error('地区不存在',404)
    try:
        context=ops.management_context(db,user,region=region)
        # 重建后旧书签中的片区ID已清空，仍应直接展示新网格。
        selected=ops.management_detail(db,user,zone) if mode=='assign' and zone is not None and any(
            row.id==zone for row in context['zones']) else None
    except ops.OperationError as e:return error(e,404 if user.role=='admin' else 403)
    from app.services.bd_regions import release_ids
    return render(request,user,'bd_zone_manage.html',**context,selected=selected,region=region,regions=regions,mode=mode,
                  grid_rows=bd_grid_regions.management_grids(db,user,region=region),
                  releases=db.query(BdZoneRelease).filter(BdZoneRelease.id.in_(release_ids(db,region))).order_by(BdZoneRelease.id.desc()).all())


@router.post('/zones/grid-merge-preview')
async def grid_merge_preview(request:Request,user:Optional[User]=Depends(work_login),db:Session=Depends(get_db)):
    try:
        payload=await request.json()
        if not isinstance(payload,dict):raise ValueError()
        post_guard(request,user,payload.get('csrf_token',''))
        return JSONResponse(bd_grid_regions.preview(db,user,payload.get('selections'),payload.get('merge_boundaries',True)))
    except (ValueError,ops.OperationError) as exc:return JSONResponse({'error':str(exc) or '合并参数无效'},status_code=409)


@router.post('/zones/grid-auto-preview')
async def grid_auto_preview(request:Request,user:Optional[User]=Depends(work_login),db:Session=Depends(get_db)):
    try:
        payload=await request.json()
        if not isinstance(payload,dict):raise ValueError()
        post_guard(request,user,payload.get('csrf_token',''))
        from app.services.bd_grid_region_builder import plan
        return JSONResponse(plan(db,user,region=payload.get('region','all'),
            target_basis=payload.get('target_basis'),target_per_zone=payload.get('target_per_zone'),
            max_area_km2=payload.get('max_area_km2'),min_stores=payload.get('min_stores'),
            tolerance_pct=payload.get('tolerance_pct'),max_grids_per_zone=payload.get('max_grids_per_zone'),
            max_stores_per_zone=payload.get('max_stores_per_zone'),
            apply=False))
    except (ValueError,ops.OperationError) as exc:
        db.rollback();return JSONResponse({'error':str(exc) or '自动分区参数无效'},status_code=409)


@router.post('/zones/grid-auto-apply')
async def grid_auto_apply(request:Request,user:Optional[User]=Depends(work_login),db:Session=Depends(get_db)):
    try:
        payload=await request.json()
        if not isinstance(payload,dict):raise ValueError()
        post_guard(request,user,payload.get('csrf_token',''))
        from app.services.bd_grid_region_builder import plan
        report=plan(db,user,region=payload.get('region','all'),
            target_basis=payload.get('target_basis'),target_per_zone=payload.get('target_per_zone'),
            max_area_km2=payload.get('max_area_km2'),min_stores=payload.get('min_stores'),
            tolerance_pct=payload.get('tolerance_pct'),max_grids_per_zone=payload.get('max_grids_per_zone'),
            max_stores_per_zone=payload.get('max_stores_per_zone'),
            apply=True,expected_signature=payload.get('signature'))
        db.commit()
        return JSONResponse({'ok':True,'zone_count':report['zone_count']})
    except (ValueError,ops.OperationError) as exc:
        db.rollback();return JSONResponse({'error':str(exc) or '自动分区提交失败'},status_code=409)


@router.post('/zones/merge-grids')
def grid_merge(request:Request,name:str=Form(...),selections:list[str]=Form(...),csrf_token:str=Form(''),
               merge_boundaries:bool=Form(False),
               user:Optional[User]=Depends(work_login),db:Session=Depends(get_db)):
    try:
        post_guard(request,user,csrf_token)
        zone=bd_grid_regions.merge(db,user,[json.loads(item) for item in selections],name,merge_boundaries)
        db.commit()
    except (ValueError,ops.OperationError) as exc:
        db.rollback();return error(exc,409)
    return RedirectResponse(zone_location(db,zone.id),status_code=303)


@router.get('/zones/{zone_id}/panel',response_class=HTMLResponse)
def zone_panel(zone_id:int,request:Request,user:Optional[User]=Depends(work_login),db:Session=Depends(get_db)):
    try:
        context=ops.management_context(db,user,with_geometry=False)
        detail=ops.management_detail(db,user,zone_id)
    except ops.OperationError as e:return error(e,404 if user.role=='admin' else 403)
    return render(request,user,'bd_zone_panel.html',**context,**detail)


@router.post('/zones/formal-generate')
def generate(request:Request,csrf_token:str=Form(''),user:Optional[User]=Depends(work_login)):
    try:post_guard(request,user,csrf_token);ops.require_admin(user)
    except ops.OperationError as e:return error(e)
    return error('片区和网格划分已迁至脚本生成',410)


@router.post('/zones/{zone_id}/assign')
def zone_assign(zone_id:int,request:Request,team_id:int=Form(...),revision:int=Form(...),csrf_token:str=Form(''),
                user:Optional[User]=Depends(work_login),db:Session=Depends(get_db)):
    try:
        post_guard(request,user,csrf_token);ops.assign_zone(db,user,zone_id,team_id,revision);db.commit()
    except ops.OperationError as e:db.rollback();return error(e,409)
    return RedirectResponse(zone_location(db,zone_id),status_code=303)


@router.post('/zones/{zone_id}/recall')
def zone_recall(zone_id:int,request:Request,revision:int=Form(...),csrf_token:str=Form(''),
                user:Optional[User]=Depends(work_login),db:Session=Depends(get_db)):
    try:
        post_guard(request,user,csrf_token);ops.recall_zone(db,user,zone_id,revision);db.commit()
    except ops.OperationError as e:db.rollback();return error(e,409)
    return RedirectResponse(zone_location(db,zone_id),status_code=303)


@router.get('/my/work',response_class=HTMLResponse)
def work(request:Request,user:Optional[User]=Depends(work_login),db:Session=Depends(get_db)):
    try:batches=task_ops.my_tasks(db,user)
    except ops.OperationError as e:return error(e)
    return render(request,user,'bd_work_home.html',batches=batches,summaries=task_ops.batch_summaries(db,user,batches))


@router.get('/my/work/zones/{zone_id}',response_class=HTMLResponse)
def work_zone(zone_id:int,request:Request,state:str='',kw:str='',user:Optional[User]=Depends(work_login),
              db:Session=Depends(get_db)):
    zone=db.get(BdZone,zone_id)
    if not zone:return error('片区不存在',404)
    try:ops.can_view(db,user,zone)
    except ops.OperationError as e:return error(e)
    leader=user.role=='admin' or zone.leader_id==user.id
    grids,progress=ops.zone_grids(db,zone_id,None if leader else user.id,task_state=state,keyword=kw)
    members=db.query(User).join(BdTeamMember,BdTeamMember.person_code==User.person_code).filter(
        BdTeamMember.team_id==zone.team_id,BdTeamMember.role=='member',BdTeamMember.end_date.is_(None),
        User.is_active.is_(True),User.status=='active',User.id!=zone.leader_id).all() if leader else []
    names={u.id:u.display_name or u.username for u in db.query(User).filter(User.id.in_(
        {g.assignee_id for g in grids if g.assignee_id})).all()}
    geometry={'type':'FeatureCollection','features':[{'type':'Feature','id':g.id,'geometry':g.geometry,
        'properties':{'id':g.id,'assigned':g.assignee_id is not None,'review':g.state=='review'}} for g in grids]}
    return render(request,user,'bd_work_zone.html',zone=zone,grids=grids,members=members,names=names,
                  leader=zone.leader_id==user.id,geometry=geometry,state=state,kw=kw,progress=progress)


@router.post('/my/work/grids/{grid_id}/assign')
def grid_assign(grid_id:int,request:Request,target_id:Optional[int]=Form(None),revision:int=Form(...),
                state:str=Form(''),kw:str=Form(''),csrf_token:str=Form(''),
                user:Optional[User]=Depends(work_login),db:Session=Depends(get_db)):
    try:
        post_guard(request,user,csrf_token);ops.assign_grid(db,user,grid_id,target_id,revision);db.commit()
        zone_id=db.get(BdWorkGrid,grid_id).zone_id
    except ops.OperationError as e:db.rollback();return error(e,409)
    pairs=[('state',state),('kw',kw)]
    query=('?'+urlencode([(k,v) for k,v in pairs if v])) if any(v for _,v in pairs) else ''
    return RedirectResponse(f'/my/work/zones/{zone_id}{query}',status_code=303)


@router.get('/my/work/grids/{grid_id}',response_class=HTMLResponse)
def grid_page(grid_id:int,request:Request,page:int=1,kw:str='',visit:str='',status:str='',
              user:Optional[User]=Depends(work_login),db:Session=Depends(get_db)):
    try:
        zone,grid=ops.executable(db,user,grid_id)
        stores,more=ops.stores_page(db,grid_id,page,kw,task_state=visit,status=status)
        # 作业进展按巡店留痕自动算（不再手填百分比）。
        progress=ops.grid_progress(db,grid_id)
        logs=db.query(BdStoreVisitLog).filter_by(grid_id=grid_id).order_by(BdStoreVisitLog.id.desc()).limit(20).all()
        geometry={'type':'FeatureCollection','features':[{'type':'Feature','geometry':grid.geometry,'properties':{'id':grid.id}}]}
        return render(request,user,'bd_work_grid.html',grid=grid,zone=zone,stores=stores,more=more,
            page=max(1,page),kw=kw,visit=visit,status=status,logs=logs,geometry=geometry,nonce=str(uuid.uuid4()),
            progress=progress,nonce_for=lambda identity:str(uuid.uuid4()))
    except ops.OperationError as e:return error(e)


@router.post('/my/work/grids/{grid_id}/visit')
def visit(grid_id:int,request:Request,request_id:str=Form(...),store_id:str=Form(''),name:str=Form(...),address:str=Form(''),
          lat:float=Form(...),lon:float=Form(...),status:str=Form(...),note:str=Form(''),create:bool=Form(False),
          store_revision:Optional[int]=Form(None),kw:str=Form(''),visit:str=Form(''),csrf_token:str=Form(''),
          user:Optional[User]=Depends(work_login),db:Session=Depends(get_db)):
    try:
        post_guard(request,user,csrf_token)
        ops.visit(db,user,grid_id,store_id,request_id,name,address,lat,lon,status,note,create=create,store_revision=store_revision)
        db.commit()
    except ops.OperationError as e:db.rollback();return error(e,409)
    return RedirectResponse(_grid_query(grid_id,kw,visit,status),status_code=303)
