"""巡店批次片区派工与新店核实。"""
import base64
import uuid
from types import SimpleNamespace
from urllib.parse import urlencode
from fastapi import APIRouter, Depends, Request, Form, File, UploadFile
from fastapi.responses import RedirectResponse, Response
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from sqlalchemy.orm import Session
from app.db import get_db
from app.models import User, BdTeamMember, BdGridTask, BdRegionTask, BdStoreProposal
from app.forms import require_form_token
from app.routers.bd_zone_ops_r import work_login, render, post_guard, error
from app.services import (bd_hierarchical_tasks as tasks, bd_store_proposals as proposals,
                          bd_zone_operations as ops, bd_batch_auto_assign as auto_assign)

router=APIRouter(dependencies=[Depends(require_form_token)])


@router.get('/my/work/batches/{task_id}/regions/{region_id}')
def region_page(task_id:int,region_id:int,request:Request,state:str='',owner:str='',user:User=Depends(work_login),db:Session=Depends(get_db)):
    try:
        main,tree=tasks.task_tree(db,user,task_id)
        item=next((r for r in tree if r['task'].id==region_id),None)
        if not item:raise ops.OperationError('片区任务不存在')
        if user.role!='admin':tasks.captain(db,user,item['task'])
        stats=tasks.summarize([item])
        members=db.query(User).join(BdTeamMember,BdTeamMember.person_code==User.person_code).filter(
            BdTeamMember.team_id==item['task'].team_id,BdTeamMember.role=='member',BdTeamMember.end_date.is_(None),
            User.is_active.is_(True),User.status=='active',User.role.in_(('staff','leader'))).all()
        names={u.id:u.display_name or u.username for u in members}
        def keep(g):
            gt=g['task']
            if owner and str(gt.assignee_id or '')!=owner and not(owner=='unassigned' and gt.assignee_id is None):return False
            if state=='unassigned' and gt.assignee_id is not None:return False
            if state=='assigned' and not(gt.assignee_id is not None and gt.status in ('pending','assigned')):return False
            if state in ('in_progress','completed') and gt.status!=state:return False
            return True
        children=[g for g in item['grids'] if keep(g)]
        geometry={'type':'FeatureCollection','features':[{'type':'Feature','geometry':g['grid'].geometry,
            'properties':{'id':g['task'].id,'assigned':bool(g['task'].assignee_id)}} for g in children]}
        return render(request,user,'bd_batch_region.html',task=main,item=item,children=children,stats=stats,
                      members=members,names=names,state=state,owner=owner,geometry=geometry)
    except ops.OperationError as exc:return error(exc)


@router.post('/my/work/batch-regions/{region_id}/assign')
def bulk_assign(region_id:int,request:Request,selections:list[str]=Form([]),target_id:int|None=Form(None),
                csrf_token:str=Form(''),user:User=Depends(work_login),db:Session=Depends(get_db)):
    try:
        post_guard(request,user,csrf_token)
        task_id=tasks.bulk_assign(db,user,region_id,selections,target_id);db.commit()
    except ops.OperationError as exc:db.rollback();return error(exc,409)
    return RedirectResponse(f'/my/work/batches/{task_id}/regions/{region_id}?'+urlencode({'msg':'派发完成'}),303)


@router.post('/my/work/batch-regions/{region_id}/auto-preview')
async def auto_assign_preview(region_id:int,request:Request,user:User=Depends(work_login),db:Session=Depends(get_db)):
    try:
        payload=await request.json()
        if not isinstance(payload,dict):raise ValueError()
        post_guard(request,user,payload.get('csrf_token',''))
        result=await run_in_threadpool(auto_assign.plan,db,user,region_id,include_batch=payload.get('include_batch',True),
            use_efficiency=payload.get('use_efficiency',False),member_ids=payload.get('member_ids'))
        return JSONResponse(result)
    except (ValueError,TypeError,ops.OperationError) as exc:
        return JSONResponse({'error':str(exc) or '无法生成分配方案'},status_code=409)


@router.post('/my/work/batch-regions/{region_id}/auto-apply')
async def auto_assign_apply(region_id:int,request:Request,user:User=Depends(work_login),db:Session=Depends(get_db)):
    try:
        payload=await request.json()
        if not isinstance(payload,dict):raise ValueError()
        post_guard(request,user,payload.get('csrf_token',''))
        result=auto_assign.apply(db,user,region_id,payload.get('signature',''),
            include_batch=payload.get('include_batch',True),use_efficiency=payload.get('use_efficiency',False),
            member_ids=payload.get('member_ids'),ai_decision=payload.get('ai_decision'))
        db.commit()
        return JSONResponse({'ok':True,'grid_count':result['grid_count']})
    except (ValueError,TypeError,ops.OperationError) as exc:
        db.rollback()
        return JSONResponse({'error':str(exc) or '自动分配失败'},status_code=409)


def _new_store_back(grid_id, values):
    # Construct a local path from known filters; never accept an arbitrary back URL.
    keys=('kw','task_state','status','sort','direction','page')
    pairs=[(key,str(values.get(key,''))[:100]) for key in keys if values.get(key,'') not in ('',None)]
    return f'/my/work/batch-grids/{grid_id}'+('?' + urlencode(pairs) if pairs else '')


@router.get('/my/work/batch-grids/{grid_id}/new-store')
def new_store_page(grid_id:int,request:Request,resubmit:int|None=None,user:User=Depends(work_login),db:Session=Depends(get_db)):
    try:
        main,rt,grid=tasks.context(db,user,grid_id,execute=True)
        if main.status=='completed':raise ops.OperationError('该批次已完成')
        proposal=None
        if resubmit is not None:
            proposal=db.query(BdStoreProposal).filter_by(id=resubmit,grid_task_id=grid.id,
                submitted_by=user.id,state='rejected').first()
            if proposal is None:raise ops.OperationError('仅可重新提交自己被驳回的申请')
        back_fields={key:request.query_params.get(key,'')[:100]
                     for key in ('kw','task_state','status','sort','direction','page')}
        return_url=_new_store_back(grid.id,back_fields)
        back_fields['status_filter']=back_fields.pop('status')
        geometry={'type':'FeatureCollection','features':[{'type':'Feature','geometry':grid.snapshot.get('geometry'),
            'properties':{'id':grid.grid_id}}]}
        return render(request,user,'bd_new_store.html',task=main,grid=grid,geometry=geometry,
            resubmit_proposal=proposal,return_url=return_url,back_fields=back_fields,nonce_for=lambda:str(uuid.uuid4()))
    except ops.OperationError as exc:return error(exc)


@router.post('/my/work/batch-grids/{grid_id}/new-store')
async def new_store(grid_id:int,request:Request,name:str=Form(...),address:str=Form(...),lat:float=Form(...),lon:float=Form(...),
    request_id:str=Form(...),proposal_id:int|None=Form(None),photo:UploadFile=File(...),kw:str=Form(''),task_state:str=Form(''),status_filter:str=Form(''),
    sort:str=Form(''),direction:str=Form('asc'),page:str=Form(''),csrf_token:str=Form(''),
    user:User=Depends(work_login),db:Session=Depends(get_db)):
    try:
        post_guard(request,user,csrf_token)
        data=await photo.read(proposals.MAX_PHOTO+1)
        p=proposals.submit(db,user,grid_id,name,address,lat,lon,data,request_id,proposal_id)
        db.commit()
    except ops.OperationError as exc:db.rollback();return error(exc,409)
    finally:await photo.close()
    back=_new_store_back(grid_id,{'kw':kw,'task_state':task_state,'status':status_filter,'sort':sort,'direction':direction,'page':page})
    return RedirectResponse(back+('&' if '?' in back else '?')+urlencode({'msg':'新店已提交，等待队长核实'}),303)


@router.get('/my/work/batches/{task_id}/new-stores')
def proposal_list(task_id:int,request:Request,state:str='pending',region:str='',grid:str='',submitter:str='',page:int=1,user:User=Depends(work_login),db:Session=Depends(get_db)):
    try:
        main,query=proposals.visible(db,user,task_id)
        if state in ('pending','approved','rejected'):query=query.filter(BdStoreProposal.state==state)
        region=int(region) if region.isdigit() else None
        grid=int(grid) if grid.isdigit() else None
        submitter=int(submitter) if submitter.isdigit() else None
        if region is not None:query=query.filter(BdGridTask.region_task_id==region)
        if grid is not None:query=query.filter(BdGridTask.id==grid)
        if submitter is not None:query=query.filter(BdStoreProposal.submitted_by==submitter)
        _,tree=tasks.task_tree(db,user,task_id)
        grid_names={g['task'].id:g['grid'].id for item in tree for g in item['grids']}
        owners=db.query(User.id,User.display_name,User.username).filter(User.id.in_(
            query.with_entities(BdStoreProposal.submitted_by).distinct())).all()
        names={identity:display or username for identity,display,username in owners}
        can_review=main.status!='disabled' and any(r['task'].leader_id==user.id for r in tree)
        payload=BdStoreProposal.payload
        columns=[getattr(BdStoreProposal,k) for k in ('id','grid_task_id','submitted_by','state','revision','reason','reviewed_by','reviewed_at')]
        page=max(1,page)
        data=query.with_entities(*columns,*[payload[k].as_string().label(k) for k in ('name','address','lat','lon')]).order_by(BdStoreProposal.id.desc()).offset((page-1)*50).limit(51).all()
        rows=[SimpleNamespace(**{k:v for k,v in row._mapping.items() if k not in ('name','address','lat','lon')},
            payload={k:row._mapping[k] for k in ('name','address','lat','lon')}) for row in data[:50]]
        return render(request,user,'bd_store_proposals.html',task=main,proposals=rows,state=state,can_review=can_review,
            tree=tree,region=region,grid=grid,submitter=submitter,names=names,grid_names=grid_names,page=page,more=len(data)>50)
    except ops.OperationError as exc:return error(exc)


@router.get('/my/work/new-stores/{identity}/photo')
def photo(identity:int,user:User=Depends(work_login),db:Session=Depends(get_db)):
    p=db.get(BdStoreProposal,identity)
    if not p:return Response(status_code=404)
    grid=db.get(BdGridTask,p.grid_task_id);rt=db.get(BdRegionTask,grid.region_task_id)
    try:
        _,query=proposals.visible(db,user,rt.main_task_id)
        if not query.filter(BdStoreProposal.id==identity).first():raise ops.OperationError('不能查看该照片')
    except ops.OperationError as exc:return error(exc)
    return Response(base64.b64decode(p.payload['photo']),media_type=p.payload['photo_type'],
                    headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})


@router.post('/my/work/batches/{task_id}/new-stores/review')
def review(task_id:int,request:Request,selections:list[str]=Form([]),action:str=Form(...),reason:str=Form(''),
           csrf_token:str=Form(''),user:User=Depends(work_login),db:Session=Depends(get_db)):
    try:
        post_guard(request,user,csrf_token)
        count=proposals.review(db,user,task_id,selections,action,reason);db.commit()
    except ops.OperationError as exc:db.rollback();return error(exc,409)
    return RedirectResponse(f'/my/work/batches/{task_id}/new-stores?'+urlencode({'msg':f'已处理 {count} 家新店'}),303)
