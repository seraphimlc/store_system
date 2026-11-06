"""新店申请与核实：只写作业域，整批核实事务由路由提交。"""
import base64
import math
import uuid
from datetime import datetime
from shapely.geometry import shape, Point
from sqlalchemy import or_, and_
from app.models import BdStoreProposal, BdStoreTask, BdGridTask, BdRegionTask, BdMainTask, BdWorkStore, BdGridStore, BdWorkGrid
from app.services import bd_hierarchical_tasks as tasks, bd_zone_operations as ops

MAX_PHOTO=5*1024*1024


def photo_kind(data):
    if data.startswith(b'\xff\xd8\xff'):return 'image/jpeg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):return 'image/png'
    if data[:4]==b'RIFF' and data[8:12]==b'WEBP':return 'image/webp'
    raise ops.OperationError('照片只支持JPEG、PNG或WebP')


def submit(db,user,grid_id,name,address,lat,lon,photo,request_id,proposal_id=None):
    main,rt,grid=tasks.context(db,user,grid_id,lock=True,execute=True)
    if main.status=='completed':raise ops.OperationError('该批次已完成')
    try:uuid.UUID(request_id)
    except (ValueError,TypeError):raise ops.OperationError('提交标识无效')
    name=name.strip();address=address.strip()
    if not name or len(name)>255 or not address or len(address)>1000:
        raise ops.OperationError('请填写有效店名和地址')
    if not math.isfinite(lat) or not math.isfinite(lon) or not (-90<=lat<=90 and -180<=lon<=180):
        raise ops.OperationError('定位无效')
    if not shape(grid.snapshot['geometry']).covers(Point(lon,lat)):
        raise ops.OperationError('定位不在当前网格内，请调整定位')
    if not photo or len(photo)>MAX_PHOTO:raise ops.OperationError('请上传不超过5MB的门店照片')
    kind=photo_kind(photo)
    payload={'name':name,'address':address,'lat':lat,'lon':lon,'photo':base64.b64encode(photo).decode(),'photo_type':kind}
    existing=db.query(BdStoreProposal).filter_by(request_id=request_id).first()
    if existing:
        if existing.submitted_by!=user.id or existing.grid_task_id!=grid.id or existing.payload!=payload:
            raise ops.OperationError('提交标识冲突')
        return existing
    if proposal_id:
        proposal=ops.locked(db,BdStoreProposal,proposal_id)
        if proposal.submitted_by!=user.id or proposal.grid_task_id!=grid.id or proposal.state!='rejected':
            raise ops.OperationError('仅可重新提交自己被驳回的申请')
        before={'state':proposal.state};proposal.payload=payload;proposal.state='pending';proposal.reason=''
        proposal.request_id=request_id;proposal.reviewed_by=None;proposal.reviewed_at=None;proposal.revision+=1
    else:
        proposal=BdStoreProposal(grid_task_id=grid.id,submitted_by=user.id,request_id=request_id,payload=payload)
        db.add(proposal);db.flush();before={}
    ops.audit_log(db,user,'task','submit_store_proposal',proposal,before,{'grid_task_id':grid.id})
    return proposal


def visible(db,user,task_id):
    main,tree=tasks.task_tree(db,user,task_id)
    region_ids=[item['task'].id for item in tree]
    query=db.query(BdStoreProposal).join(BdGridTask,BdGridTask.id==BdStoreProposal.grid_task_id).filter(
        BdGridTask.region_task_id.in_(region_ids))
    if user.role!='admin':
        leaders=[item['task'].id for item in tree if item['task'].leader_id==user.id]
        query=query.filter((BdGridTask.region_task_id.in_(leaders))|(BdStoreProposal.submitted_by==user.id))
    return main,query


def review(db,user,task_id,selections,action,reason=''):
    main=ops.locked(db,BdMainTask,task_id)
    if not main:raise ops.OperationError('批次不存在')
    tasks.require_enabled(main)
    if action not in ('approve','reject') or not selections or len(selections)>200:
        raise ops.OperationError('请选择有效核实操作')
    if len(reason)>500 or action=='reject' and not reason.strip():raise ops.OperationError('驳回须填写原因')
    try:parsed=dict(tuple(map(int,value.split(':'))) for value in selections)
    except (ValueError,TypeError):raise ops.OperationError('申请选择无效')
    proposals=db.query(BdStoreProposal).filter(BdStoreProposal.id.in_(parsed)).order_by(BdStoreProposal.id).with_for_update().all()
    if len(proposals)!=len(parsed):raise ops.OperationError('申请不存在')
    if action=='approve' and main.status=='completed':raise ops.OperationError('该批次已完成')
    # 全部校验后才写入；批次锁串行化核实与完成任务。
    grid_ids={p.grid_task_id for p in proposals}
    grids={g.id:g for g in db.query(BdGridTask).filter(BdGridTask.id.in_(grid_ids)).all()}
    regions={r.id:r for r in db.query(BdRegionTask).filter(BdRegionTask.id.in_({g.region_task_id for g in grids.values()})).all()}
    for rt in regions.values():
        if rt.main_task_id!=main.id or rt.leader_id!=user.id:raise ops.OperationError('不能操作其他批次或团队申请')
    teams={rt.team_id for rt in regions.values()}
    if len(teams)!=1:raise ops.OperationError('仅可核实本队申请')
    tasks.captain(db,user,next(iter(regions.values())))
    snapshots=[snapshot for snapshot, in db.query(BdStoreTask.snapshot).join(BdGridTask,BdGridTask.id==BdStoreTask.grid_task_id).join(
        BdRegionTask,BdRegionTask.id==BdGridTask.region_task_id).filter(BdRegionTask.main_task_id==main.id)]
    positions=[p.payload for p in proposals]
    master_stores=db.query(BdWorkStore.name,BdWorkStore.lat,BdWorkStore.lon).filter(or_(*[
        and_(BdWorkStore.lat.between(p['lat']-.0003,p['lat']+.0003),
             BdWorkStore.lon.between(p['lon']-.0003,p['lon']+.0003)) for p in positions])).all() if action=='approve' else []
    base_grids={g.id:g for g in db.query(BdWorkGrid).filter(BdWorkGrid.id.in_({g.grid_id for g in grids.values()})).order_by(BdWorkGrid.id).with_for_update().all()} if action=='approve' else {}
    snapshots.extend({'name':name,'lat':lat,'lon':lon} for name,lat,lon in master_stores)
    chosen_names=set();chosen_positions=[]
    for proposal in proposals:
        grid=grids[proposal.grid_task_id]
        ops.revision_ok(proposal,parsed[proposal.id])
        if proposal.state!='pending':raise ops.OperationError('申请已处理，请刷新')
        if action=='approve':
            p=proposal.payload;key=''.join(p['name'].split()).casefold()
            if key in chosen_names:raise ops.OperationError('所选申请可能重复，请分别核实')
            chosen_names.add(key)
            if any(abs(lat-p['lat'])<0.0003 and abs(lon-p['lon'])<0.0003 for lat,lon in chosen_positions):raise ops.OperationError('所选申请位置接近，请分别核实')
            chosen_positions.append((p['lat'],p['lon']))
            if not shape(grid.snapshot['geometry']).covers(Point(p['lon'],p['lat'])):raise ops.OperationError('定位不在当前网格内')
            for snapshot in snapshots:
                same=''.join(snapshot.get('name','').split()).casefold()==key
                near=(snapshot.get('lat') is not None and snapshot.get('lon') is not None and
                      abs(float(snapshot['lat'])-p['lat'])<0.0003 and abs(float(snapshot['lon'])-p['lon'])<0.0003)
                if same or near:raise ops.OperationError('存在疑似重复商户，请先核对后重新提交')
    for proposal in proposals:
        before={'state':proposal.state}
        proposal.state='approved' if action=='approve' else 'rejected'
        proposal.reviewed_by=user.id;proposal.reviewed_at=datetime.utcnow();proposal.reason=reason.strip();proposal.revision+=1
        if action=='approve':
            p=proposal.payload;identity='new-'+str(uuid.uuid4());proposal.store_id=identity
            # 工作商户和批次任务都保留来源申请；照片只经鉴权路由提供。
            db.add(BdWorkStore(id=identity,name=p['name'],address=p['address'],lat=p['lat'],lon=p['lon']))
            grid=grids[proposal.grid_task_id]
            base_grid=base_grids[grid.grid_id];base_grid.store_count+=1
            db.add(BdGridStore(release_id=base_grid.release_id,grid_id=grid.grid_id,source_id=identity))
            db.add(BdStoreTask(grid_task_id=proposal.grid_task_id,store_id=identity,
                snapshot={k:v for k,v in p.items() if k not in ('photo','photo_type')},status='pending',result={}))
        ops.audit_log(db,user,'task','review_store_proposal',proposal,before,{'state':proposal.state,'reason':proposal.reason})
    if action=='approve':tasks.refresh_progress(db,main)
    return len(proposals)
