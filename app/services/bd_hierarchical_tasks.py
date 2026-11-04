"""独立批次的任务及快照；只写bd_*，不修改基础片区、网格或商户主档。"""
import uuid
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
from sqlalchemy import func, text, bindparam, case, or_, String
from app.models import (BdMainTask, BdRegionTask, BdGridTask, BdStoreTask,
                        BdRegion, BdWorkGrid, BdGridStore, BdWorkStore, BdTeam, BdTeamMember, User,
                        BdTaskEvent)
from app.services import bd_zone_operations as ops

STORE_STATUSES=('normal','abnormal','uncertain','closed')
STORE_TASK_FILTERS=('completed','pending')
GRID_TASK_FILTERS=('unassigned','assigned','in_progress','completed')


def task_events(db,user,main_task_id,page=1,limit=50,*,keyword='',actor_id='',
                team_id='',region_task_id='',grid_task_id='',event_type='',dfrom='',dto=''):
    """Filter batch events and current completion status within the user's present scope."""
    _main,visible=task_tree(db,user,main_task_id)
    page=max(1,int(page));limit=min(100,max(1,int(limit)))
    region_ids={item['task'].id for item in visible}
    grid_ids={entry['task'].id for item in visible for entry in item['grids']}
    scope=db.query(BdTaskEvent).filter(BdTaskEvent.main_task_id==main_task_id)
    if user.role!='admin':
        captain_regions={item['task'].id for item in visible if item['task'].leader_id==user.id}
        branches=[BdTaskEvent.grid_task_id.in_(grid_ids)] if grid_ids else []
        if captain_regions:branches.append(BdTaskEvent.region_task_id.in_(captain_regions))
        branches.append((BdTaskEvent.region_task_id.is_(None)) & (BdTaskEvent.grid_task_id.is_(None)))
        scope=scope.filter(or_(*branches))

    actor_options=(scope.with_entities(BdTaskEvent.actor_id,BdTaskEvent.actor_label)
                   .filter(BdTaskEvent.actor_id.isnot(None)).distinct()
                   .order_by(BdTaskEvent.actor_label).all())
    event_options=[value for value, in scope.with_entities(BdTaskEvent.event_type)
                   .distinct().order_by(BdTaskEvent.event_type).all()]
    team_options=(scope.with_entities(BdTaskEvent.team_id,BdTaskEvent.team_label).filter(
        BdTaskEvent.team_id.isnot(None)).distinct().order_by(BdTaskEvent.team_label).all())
    region_options=[item['task'] for item in visible]
    grid_options=[entry['task'] for item in visible for entry in item['grids']]

    filters=scope
    keyword=(keyword or '').strip()[:120]
    if keyword:
        like=f'%{keyword}%'
        filters=filters.filter(or_(BdTaskEvent.actor_label.ilike(like),
            BdTaskEvent.team_label.ilike(like),BdTaskEvent.main_label.ilike(like),
            BdTaskEvent.region_label.ilike(like),BdTaskEvent.grid_label.ilike(like),
            BdTaskEvent.store_label.ilike(like),BdTaskEvent.store_source_id.ilike(like),
            BdTaskEvent.store_task_id.cast(String).ilike(like)))
    if actor_id:
        try:actor_value=int(actor_id)
        except (TypeError,ValueError):actor_value=-1
        filters=filters.filter(BdTaskEvent.actor_id==actor_value)
    try:team_value=int(team_id) if team_id else None
    except (TypeError,ValueError):team_value=-1
    if team_value is not None:filters=filters.filter(BdTaskEvent.team_id==team_value)
    if event_type:
        filters=filters.filter(BdTaskEvent.event_type==event_type)
    try:region_value=int(region_task_id) if region_task_id else None
    except (TypeError,ValueError):region_value=-1
    if region_value is not None:
        if region_value not in region_ids:filters=filters.filter(BdTaskEvent.id==-1)
        else:filters=filters.filter(BdTaskEvent.region_task_id==region_value)
    try:grid_value=int(grid_task_id) if grid_task_id else None
    except (TypeError,ValueError):grid_value=-1
    if grid_value is not None:
        if grid_value not in grid_ids:filters=filters.filter(BdTaskEvent.id==-1)
        else:filters=filters.filter(BdTaskEvent.grid_task_id==grid_value)
    day_start=_parse_report_day(dfrom)
    day_end=_parse_report_day(dto)
    if day_start:filters=filters.filter(BdTaskEvent.created_at>=day_start)
    if day_end:filters=filters.filter(BdTaskEvent.created_at<day_end)

    total=filters.count()
    rows=filters.order_by(BdTaskEvent.created_at.desc(),BdTaskEvent.id.desc()).offset((page-1)*limit).limit(limit).all()
    completion_scope=scope.filter(BdTaskEvent.event_type=='complete_store_task')
    if region_value is not None:
        completion_scope=completion_scope.filter(BdTaskEvent.region_task_id==region_value) if region_value in region_ids else completion_scope.filter(BdTaskEvent.id==-1)
    if grid_value is not None:
        completion_scope=completion_scope.filter(BdTaskEvent.grid_task_id==grid_value) if grid_value in grid_ids else completion_scope.filter(BdTaskEvent.id==-1)
    if day_start:completion_scope=completion_scope.filter(BdTaskEvent.created_at>=day_start)
    if day_end:completion_scope=completion_scope.filter(BdTaskEvent.created_at<day_end)
    if actor_id:completion_scope=completion_scope.filter(BdTaskEvent.actor_id==actor_value)
    if team_value is not None:completion_scope=completion_scope.filter(BdTaskEvent.team_id==team_value)
    completions=completion_scope.with_entities(
        BdTaskEvent.actor_id,BdTaskEvent.actor_label,BdTaskEvent.actor_role,
        BdTaskEvent.team_label,func.count(BdTaskEvent.id)).group_by(
        BdTaskEvent.actor_id,BdTaskEvent.actor_label,BdTaskEvent.actor_role,
        BdTaskEvent.team_label).order_by(func.count(BdTaskEvent.id).desc(),BdTaskEvent.actor_label).all()

    metrics={'first_completed':0,'completion_events':sum(row[4] for row in completions),
             'performers':completions}
    # Count a task only on its earliest completion event across its lifetime.
    first_scope=scope.filter(BdTaskEvent.event_type=='complete_store_task')
    if region_value is not None:
        first_scope=first_scope.filter(BdTaskEvent.region_task_id==region_value) if region_value in region_ids else first_scope.filter(BdTaskEvent.id==-1)
    if grid_value is not None:
        first_scope=first_scope.filter(BdTaskEvent.grid_task_id==grid_value) if grid_value in grid_ids else first_scope.filter(BdTaskEvent.id==-1)
    first_ids=(first_scope.with_entities(func.min(BdTaskEvent.id).label('event_id'))
        .group_by(BdTaskEvent.store_task_id).subquery())
    first_query=db.query(BdTaskEvent).join(first_ids,first_ids.c.event_id==BdTaskEvent.id)
    if day_start:first_query=first_query.filter(BdTaskEvent.created_at>=day_start)
    if day_end:first_query=first_query.filter(BdTaskEvent.created_at<day_end)
    if actor_id:first_query=first_query.filter(BdTaskEvent.actor_id==actor_value)
    if team_value is not None:first_query=first_query.filter(BdTaskEvent.team_id==team_value)
    metrics['first_completed']=first_query.count()
    metric_grid_ids=set(grid_ids)
    if region_value is not None and region_value in region_ids:
        metric_grid_ids={entry['task'].id for item in visible if item['task'].id==region_value
                         for entry in item['grids']}
    if grid_value is not None:
        metric_grid_ids &= {grid_value} if grid_value in grid_ids else set()
    metric_status=db.query(func.count(BdStoreTask.id),
        func.count(BdStoreTask.id).filter(BdStoreTask.status=='completed')).filter(
            BdStoreTask.grid_task_id.in_(metric_grid_ids)).one() if metric_grid_ids else (0,0)
    if metric_grid_ids and (actor_id or team_value is not None):
        current_query=(db.query(func.count(BdStoreTask.id),
            func.count(BdStoreTask.id).filter(BdStoreTask.status=='completed'))
            .join(BdGridTask,BdGridTask.id==BdStoreTask.grid_task_id)
            .join(BdRegionTask,BdRegionTask.id==BdGridTask.region_task_id)
            .filter(BdStoreTask.grid_task_id.in_(metric_grid_ids)))
        if actor_id:current_query=current_query.filter(BdStoreTask.actor_id==actor_value)
        if team_value is not None:current_query=current_query.filter(BdRegionTask.team_id==team_value)
        metric_status=current_query.one()
    metrics['current_total']=metric_status[0] or 0
    metrics['current_done']=metric_status[1] or 0
    return (_main,rows,total,page,limit,metrics,actor_options,event_options,team_options,
            region_options,grid_options,{'keyword':keyword,'actor_id':actor_id,
            'team_id':team_id,
            'region_task_id':region_task_id,'grid_task_id':grid_task_id,
            'event_type':event_type,'dfrom':dfrom,'dto':dto})


def _parse_report_day(value):
    """Parse a JST calendar day and convert its boundary to naive UTC for stored timestamps."""
    try:
        day=datetime.strptime((value or '').strip(),'%Y-%m-%d')
    except (TypeError,ValueError):
        return None
    return day-timedelta(hours=9)


def completion_summary(db,main_task_id,start=None,end=None,*,region_task_ids=None,grid_task_ids=None):
    """Return first-completed store tasks and all completion actions in a time range.

    Pass UTC datetime boundaries for a report period; callers should construct day/week
    boundaries in the business timezone before converting them to UTC.
    """
    def scoped(query):
        if region_task_ids is None and grid_task_ids is None:
            return query
        clauses=[]
        if region_task_ids:clauses.append(BdTaskEvent.region_task_id.in_(region_task_ids))
        if grid_task_ids:clauses.append(BdTaskEvent.grid_task_id.in_(grid_task_ids))
        return query.filter(or_(*clauses)) if clauses else query.filter(BdTaskEvent.id==-1)
    base=scoped(db.query(BdTaskEvent).filter(BdTaskEvent.main_task_id==main_task_id,
                                             BdTaskEvent.event_type=='complete_store_task'))
    if start is not None:base=base.filter(BdTaskEvent.created_at>=start)
    if end is not None:base=base.filter(BdTaskEvent.created_at<end)
    all_count=base.count()
    first_per_store=(scoped(db.query(func.min(BdTaskEvent.id).label('event_id'))
        .filter(BdTaskEvent.main_task_id==main_task_id,
                BdTaskEvent.event_type=='complete_store_task')
        ).group_by(BdTaskEvent.store_task_id).subquery())
    first_query=db.query(func.count(BdTaskEvent.id)).join(
        first_per_store,first_per_store.c.event_id==BdTaskEvent.id)
    if start is not None:first_query=first_query.filter(BdTaskEvent.created_at>=start)
    if end is not None:first_query=first_query.filter(BdTaskEvent.created_at<end)
    return {'first_completed':first_query.scalar() or 0,'completion_events':all_count}


def create_main_task(db, user, title, region_ids):
    ops.require_admin(user)
    title = (title or '').strip()
    if not title or len(title) > 160:
        raise ops.OperationError('主任务名称须为1～160个字符')
    if not isinstance(region_ids,(list,tuple)) or not region_ids or any(type(i) is not int for i in region_ids):
        raise ops.OperationError('至少选择一个片区')
    ids = sorted(set(region_ids))
    if len(ids)>2000: raise ops.OperationError('选择片区过多')
    # 与基础网格归组/重建共用版本锁，创建快照时不允许拆改成员。
    release_ids=[i for i, in db.query(BdRegion.release_id).filter(BdRegion.id.in_(ids)).distinct()]
    if len(release_ids)!=1: raise ops.OperationError('一次主任务只能包含同一网格版本的片区')
    from app.models import BdZoneRelease
    ops.locked(db,BdZoneRelease,release_ids[0])
    regions=db.query(BdRegion).filter(BdRegion.id.in_(ids),BdRegion.state!='archived').order_by(
        BdRegion.id).populate_existing().with_for_update().all()
    if len(regions)!=len(ids): raise ops.OperationError('片区不存在或已归档，请刷新后重试')
    grids=db.query(BdWorkGrid).filter(BdWorkGrid.zone_id.in_(ids), BdWorkGrid.state != 'disabled').order_by(BdWorkGrid.id).with_for_update().all()
    if {g.zone_id for g in grids}!=set(ids): raise ops.OperationError('空片区不能创建巡店任务')
    if any(g.release_id!=release_ids[0] for g in grids): raise ops.OperationError('网格版本与片区不一致')
    grid_ids=[g.id for g in grids]
    # 固定次数SQL：一次读取本批次全部店铺映射和来源快照。
    if db.bind.dialect.name=='postgresql':
        sql=text('''SELECT m.grid_id,m.source_id,
            COALESCE(w.name,s.name) AS name,COALESCE(w.address,s.address,'') AS address,
            COALESCE(w.lat,s.lat) AS lat,COALESCE(w.lon,s.lon) AS lon
            FROM bd_grid_store m LEFT JOIN bd_work_store w ON w.id=m.source_id
            LEFT JOIN public.kanto_merchants_v20261029 s ON s.place_id=m.source_id
            WHERE m.grid_id IN :ids ORDER BY m.grid_id,m.source_id''').bindparams(bindparam('ids',expanding=True))
        source_rows=[dict(row) for row in db.execute(sql,{'ids':grid_ids}).mappings()]
    else:
        source_rows=[{'grid_id':mapping.grid_id,'source_id':mapping.source_id,
                      'name':store.name if store else None,'address':store.address if store else '',
                      'lat':store.lat if store else None,'lon':store.lon if store else None}
                     for mapping,store in db.query(BdGridStore,BdWorkStore).outerjoin(
                         BdWorkStore,BdWorkStore.id==BdGridStore.source_id).filter(
                             BdGridStore.grid_id.in_(grid_ids)).order_by(BdGridStore.id)]
    seen=set()
    for row in source_rows:
        if row['source_id'] in seen: raise ops.OperationError('同一批次存在重复店铺归属')
        seen.add(row['source_id'])
        if not row['name']: raise ops.OperationError('存在缺失源信息的店铺，不能生成任务快照')
        for coordinate in ('lat','lon'):
            if row[coordinate] is not None:row[coordinate]=float(row[coordinate])
    store_counts={g.id:0 for g in grids}
    for row in source_rows:store_counts[row['grid_id']]+=1
    task=BdMainTask(release_id=release_ids[0],title=title,status='active',created_by=user.id,started_at=datetime.utcnow())
    db.add(task);db.flush()
    region_tasks={}
    for region in regions:
        rt=BdRegionTask(main_task_id=task.id,region_id=region.id,team_id=region.team_id,
                        leader_id=region.leader_id,snapshot={'name':region.name,'geometry':deepcopy(region.geometry),
                        'area_km2':region.area_km2,'source_id':region.source_id},status='pending')
        db.add(rt);region_tasks[region.id]=rt
    db.flush()
    grid_tasks={}
    for grid in grids:
        gt=BdGridTask(region_task_id=region_tasks[grid.zone_id].id,grid_id=grid.id,
                      snapshot={'geometry':deepcopy(grid.geometry),'area_km2':grid.area_km2,'source_id':grid.source_id},
                      status='pending' if store_counts[grid.id] else 'completed',progress=0 if store_counts[grid.id] else 100)
        db.add(gt);grid_tasks[grid.id]=gt
    db.flush()
    for rt in region_tasks.values():
        children=[g for g in grid_tasks.values() if g.region_task_id==rt.id]
        rt.progress=sum(g.progress for g in children)//len(children)
        if all(g.status=='completed' for g in children):rt.status='completed'
    if all(rt.status=='completed' for rt in region_tasks.values()):
        task.status='completed';task.completed_at=datetime.utcnow()
    task.progress=sum(rt.progress for rt in region_tasks.values())//len(region_tasks)
    db.add_all([BdStoreTask(grid_task_id=grid_tasks[row['grid_id']].id,store_id=row['source_id'],
                            snapshot={k:row[k] for k in ('name','address','lat','lon')},result={}) for row in source_rows])
    db.flush()
    ops.audit_log(db,user,'task','create_batch',task,{}, {'title':title,'regions':ids,
        'grids':len(grids),'stores':len(source_rows)})
    for grid_id,grid_task in grid_tasks.items():
        if store_counts[grid_id]==0:
            ops.audit_log(db,user,'task','grid_auto_completed_empty',grid_task,{},
                          {'status':'completed','reason':'no_store_tasks'})
    for rt in region_tasks.values():
        if rt.status=='completed':
            ops.audit_log(db,user,'task','region_auto_completed_empty',rt,{},
                          {'status':'completed','reason':'all_grids_empty'})
    if task.status=='completed':
        ops.audit_log(db,user,'task','batch_auto_completed_empty',task,{},
                      {'status':'completed','reason':'all_regions_empty'})
    return task


def captain(db,user,rt):
    ops.require_active(user)
    if not rt.team_id or rt.leader_id!=user.id or ops.captain(db,rt.team_id).id!=user.id:
        raise ops.OperationError('仅本批次片区的当前队长可派发')


def member(db,user,rt):
    team=db.get(BdTeam,rt.team_id) if rt.team_id else None
    if not team or team.status!='active':raise ops.OperationError('任务团队不存在或已停用')
    ops.require_member(db,user,SimpleNamespace(team_id=rt.team_id,leader_id=rt.leader_id))


def visible_regions(db,user):
    """批量权限；撤销成员/队长身份后不再展示历史任务入口。"""
    query=db.query(BdRegionTask)
    if user.role=='admin':return query
    if user.role not in ('staff','leader'):return query.filter(BdRegionTask.id==-1)
    memberships=db.query(BdTeamMember.team_id,BdTeamMember.role).join(BdTeam,
        BdTeam.id==BdTeamMember.team_id).filter(BdTeamMember.person_code==user.person_code,
        BdTeamMember.end_date.is_(None),BdTeam.status=='active').all()
    leader_teams={team for team,role in memberships if role=='leader'}
    member_teams={team for team,role in memberships if role=='member'}
    leader_counts=dict(db.query(BdTeamMember.team_id,func.count(BdTeamMember.id)).filter(
        BdTeamMember.team_id.in_(leader_teams),BdTeamMember.role=='leader',BdTeamMember.end_date.is_(None))
        .group_by(BdTeamMember.team_id).all())
    valid_leaders={team for team in leader_teams if leader_counts.get(team)==1}
    owned=db.query(BdGridTask.id).filter(BdGridTask.region_task_id==BdRegionTask.id,
        BdGridTask.assignee_id==user.id).exists()
    return query.filter(((BdRegionTask.leader_id==user.id)&BdRegionTask.team_id.in_(valid_leaders))|
        ((BdRegionTask.leader_id!=user.id)&BdRegionTask.team_id.in_(member_teams)&owned))


def my_tasks(db,user):
    ops.require_active(user)
    query=db.query(BdMainTask)
    if user.role!='admin':
        query=query.filter(BdMainTask.id.in_(visible_regions(db,user).with_entities(BdRegionTask.main_task_id)))
    return query.order_by(BdMainTask.id.desc()).all()


def task_tree(db,user,task_id,*,task_state='',keyword=''):
    """批次任务树 + 列表头筛选（网格作业状态 / 网格编号搜索）。"""
    ops.require_active(user)
    task_state=task_state if task_state in GRID_TASK_FILTERS else ''
    keyword=(keyword or '')[:100]
    task=db.get(BdMainTask,task_id)
    if not task: raise ops.OperationError('主任务不存在')
    region_query=visible_regions(db,user).filter(BdRegionTask.main_task_id==task_id)
    region_tasks=region_query.order_by(BdRegionTask.id).all()
    if user.role!='admin' and not region_tasks: raise ops.OperationError('不能访问其他批次任务')
    region_ids=[rt.id for rt in region_tasks]
    grid_query=db.query(BdGridTask).filter(BdGridTask.region_task_id.in_(region_ids))
    if user.role!='admin':
        leader_regions=[rt.id for rt in region_tasks if rt.leader_id==user.id]
        grid_query=grid_query.filter((BdGridTask.assignee_id==user.id)|BdGridTask.region_task_id.in_(leader_regions))
    grids=grid_query.order_by(BdGridTask.id).all()
    counts={identity:(count,done or 0) for identity,count,done in db.query(BdStoreTask.grid_task_id,
        func.count(BdStoreTask.id),func.sum(case((BdStoreTask.status=='completed',1),else_=0)))
        .filter(BdStoreTask.grid_task_id.in_([g.id for g in grids])).group_by(BdStoreTask.grid_task_id)}
    def keep(g):
        if task_state=='unassigned' and g.assignee_id is not None:return False
        if task_state=='assigned' and not (g.assignee_id is not None and g.status in ('pending','assigned')):return False
        if task_state=='in_progress' and g.status!='in_progress':return False
        if task_state=='completed' and g.status!='completed':return False
        if keyword and keyword not in str(g.grid_id) and keyword not in str(g.id):return False
        return True
    result=[{'task':rt,'region':SimpleNamespace(id=rt.region_id,**rt.snapshot),
             'grids':[{'task':g,'grid':SimpleNamespace(id=g.grid_id,**g.snapshot),
                       'store_count':counts.get(g.id,(0,0))[0],'done':counts.get(g.id,(0,0))[1]}
                      for g in grids if g.region_task_id==rt.id and keep(g)]} for rt in region_tasks]
    return task,result


def context(db,user,grid_task_id,*,lock=False,execute=False):
    ops.require_active(user)
    initial=db.get(BdGridTask,grid_task_id)
    if not initial: raise ops.OperationError('网格任务不存在')
    region=db.get(BdRegionTask,initial.region_task_id)
    if lock:
        main=ops.locked(db,BdMainTask,region.main_task_id)
        region=ops.locked(db,BdRegionTask,region.id)
        grid=ops.locked(db,BdGridTask,grid_task_id)
    else:
        grid=initial;main=db.get(BdMainTask,region.main_task_id)
    if execute:
        if grid.assignee_id!=user.id: raise ops.OperationError('仅本次网格任务负责人可执行')
        member(db,user,region)
    elif user.role!='admin' and region.leader_id!=user.id:
        if grid.assignee_id!=user.id: raise ops.OperationError('不能访问其他网格任务')
        member(db,user,region)
    elif user.role!='admin': captain(db,user,region)
    return main,region,grid


def assign_region_task(db,user,identity,team_id,revision):
    ops.require_admin(user)
    rt=db.get(BdRegionTask,identity)
    if not rt: raise ops.OperationError('片区任务不存在')
    main=ops.locked(db,BdMainTask,rt.main_task_id)
    rt=ops.locked(db,BdRegionTask,identity);ops.revision_ok(rt,revision)
    if main.status=='completed': raise ops.OperationError('该批次已完成')
    leader=ops.captain(db,team_id)
    if rt.team_id!=team_id:
        for grid in db.query(BdGridTask).filter_by(region_task_id=rt.id).order_by(BdGridTask.id).with_for_update():
            grid.assignee_id=None;grid.revision+=1
    before={'team_id':rt.team_id,'leader_id':rt.leader_id}
    rt.team_id=team_id;rt.leader_id=leader.id;rt.revision+=1
    ops.audit_log(db,user,'task','assign_region_task',rt,before,{'team_id':team_id,'leader_id':leader.id,'main_task_id':main.id})
    return main.id


def assign_grid_task(db,user,grid_task_id,target_id,revision):
    main,rt,gt=context(db,user,grid_task_id,lock=True)
    captain(db,user,rt);ops.revision_ok(gt,revision)
    if main.status=='completed': raise ops.OperationError('该批次已完成')
    if target_id is not None:
        target=db.get(User,target_id)
        if not target: raise ops.OperationError('队员不存在')
        member(db,target,rt)
    before={'assignee_id':gt.assignee_id}
    gt.assignee_id=target_id;gt.revision+=1
    if gt.status!='completed':gt.status='assigned' if target_id else 'pending'
    ops.audit_log(db,user,'task','assign_grid_task',gt,before,{'assignee_id':target_id,'main_task_id':main.id})
    return main.id


def grid_progress_stats(db, grid_task_id):
    """网格作业进展（**自动口径**）：店铺任务总数 / 已完成数 / 百分比 / 状态。

    状态三档：pending=未巡店、in_progress=巡店中、completed=巡店完成。
    不接受手填百分比 —— 完成一家店铺任务就自动推进（见 complete_store）。
    """
    total,done=db.query(func.count(BdStoreTask.id),
        func.count(BdStoreTask.id).filter(BdStoreTask.status=='completed')).filter_by(
        grid_task_id=grid_task_id).one()
    pct=done*100//total if total else 0
    state='completed' if total and done>=total else ('in_progress' if done else 'pending')
    return {'total':total,'done':done,'pct':pct,'state':state}


def store_page(db,user,grid_task_id,page=1,keyword='',*,task_state='',status='',sort='',direction='asc',focus=None):
    """批次网格页的商户分页 + 列表头筛选 + 自动作业进展。

    筛选口径（与巡店页一致）：
    - task_state：completed=已巡店 / pending=未巡店（按店铺任务是否完成）；
    - status：完成时记录���门店状态（正常/异常/存疑/关店）；
    - keyword：商户名称、地址或商户编号，任一命中即算。
    """
    main,rt,grid=context(db,user,grid_task_id)
    page=max(1,page);offset=(page-1)*30
    keyword=(keyword or '')[:100]
    task_state=task_state if task_state in STORE_TASK_FILTERS else ''
    status=status if status in STORE_STATUSES else ''
    query=db.query(BdStoreTask).filter(BdStoreTask.grid_task_id==grid.id)
    if keyword:
        like='%'+keyword+'%'
        query=query.filter(or_(
            BdStoreTask.snapshot['name'].as_string().ilike(like),
            BdStoreTask.snapshot['address'].as_string().ilike(like),
            BdStoreTask.store_id.ilike(like)))
    if status:
        query=query.filter(BdStoreTask.result['business_status'].as_string()==status)
    if task_state=='completed':
        query=query.filter(BdStoreTask.status=='completed')
    elif task_state=='pending':
        query=query.filter(BdStoreTask.status!='completed')
    order = BdStoreTask.id
    if sort=='visit': order=case((BdStoreTask.status=='completed',1),else_=0)
    elif sort=='status':
        order=case(*[(BdStoreTask.result['business_status'].as_string()==key,i+1)
                     for i,key in enumerate(STORE_STATUSES)],else_=0)
    ordered=query.order_by(order.desc() if direction=='desc' else order.asc(),BdStoreTask.id)
    if focus is not None:
        ids=[i for i, in ordered.with_entities(BdStoreTask.id)]
        if focus in ids:page=ids.index(focus)//30+1;offset=(page-1)*30
    stores=ordered.offset(offset).limit(31).all()
    stats=grid_progress_stats(db,grid.id);stats['page']=page
    return main,rt,grid,stores[:30],len(stores)>30,stats


def complete_store(db,user,identity,revision,request_id,status,note):
    initial=db.get(BdStoreTask,identity)
    if not initial: raise ops.OperationError('店铺任务不存在')
    main,rt,grid=context(db,user,initial.grid_task_id,lock=True,execute=True)
    store=ops.locked(db,BdStoreTask,identity)
    if status not in ('normal','abnormal','uncertain','closed') or len(note)>2000:
        raise ops.OperationError('门店状态或备注无效')
    try:uuid.UUID(request_id)
    except (ValueError,TypeError):raise ops.OperationError('提交标识无效')
    result={'business_status':status,'note':note.strip()}
    if store.request_id==request_id:
        if store.actor_id!=user.id or store.result!=result:raise ops.OperationError('提交标识冲突')
        return grid.id
    if main.status=='completed': raise ops.OperationError('该批次已完成')
    if db.query(BdStoreTask.id).filter_by(request_id=request_id).first():raise ops.OperationError('提交标识冲突')
    ops.revision_ok(store,revision)
    old_grid_status,old_region_status,old_main_status=grid.status,rt.status,main.status
    before={'status':store.status,'result':store.result}
    store.status='completed';store.result=result;store.actor_id=user.id
    store.updated_at=datetime.utcnow();store.request_id=request_id;store.revision+=1;db.flush()
    counts=db.query(func.count(BdStoreTask.id),func.count(BdStoreTask.id).filter(BdStoreTask.status=='completed')).filter_by(grid_task_id=grid.id).one()
    grid.progress=counts[1]*100//counts[0] if counts[0] else 0
    grid.status='completed' if counts[0] and counts[0]==counts[1] else 'in_progress';grid.revision+=1
    db.flush()
    refresh_progress(db,main)
    event='complete_store_task' if before['status']!='completed' else 'update_store_result'
    ops.audit_log(db,user,'task',event,store,before,{'main_task_id':main.id,'result':result})
    for row,old_state,new_state,event_name in (
        (grid,old_grid_status,grid.status,'grid_status_changed'),
        (rt,old_region_status,rt.status,'region_status_changed'),
        (main,old_main_status,main.status,'batch_status_changed'),
    ):
        if old_state != new_state:
            ops.audit_log(db,user,'task',event_name,row,{'status':old_state},{'status':new_state})
    return grid.id


def refresh_progress(db, main):
    """写入与展示使用同一商户数量口径；调用方已持有批次锁。"""
    db.flush()
    regions=db.query(BdRegionTask).filter_by(main_task_id=main.id).all()
    grids=db.query(BdGridTask).filter(BdGridTask.region_task_id.in_([r.id for r in regions])).all()
    counts={i:(n,d or 0) for i,n,d in db.query(BdStoreTask.grid_task_id,func.count(BdStoreTask.id),
        func.sum(case((BdStoreTask.status=='completed',1),else_=0))).filter(
            BdStoreTask.grid_task_id.in_([g.id for g in grids])).group_by(BdStoreTask.grid_task_id)}
    def apply(obj,n,d):
        obj.progress=d*100//n if n else 100
        obj.status='completed' if d==n else ('in_progress' if d else ('assigned' if getattr(obj,'assignee_id',None) else 'pending'))
        obj.revision+=1
    for g in grids:apply(g,*counts.get(g.id,(0,0)))
    for r in regions:
        own=[counts.get(g.id,(0,0)) for g in grids if g.region_task_id==r.id]
        apply(r,sum(n for n,d in own),sum(d for n,d in own))
    total=sum(n for n,d in counts.values());done=sum(d for n,d in counts.values())
    main.progress=done*100//total if total else 100
    main.status='completed' if total==done else 'active'
    main.completed_at=datetime.utcnow() if main.status=='completed' else None
    main.revision+=1


def summarize(tree):
    total=done=0
    for item in tree:
        item['grids'].sort(key=lambda c:(-c['done'],c['grid'].id))
        item['total']=sum(c['store_count'] for c in item['grids'])
        item['done']=sum(c['done'] for c in item['grids'])
        item['pct']=item['done']*100//item['total'] if item['total'] else 0
        total+=item['total'];done+=item['done']
    return {'total':total,'done':done,'pct':done*100//total if total else 0}


def batch_summaries(db,user,tasks):
    """列表一次聚合；队员只统计自己获派的网格。"""
    query=db.query(BdRegionTask.main_task_id,func.count(BdStoreTask.id),
        func.sum(case((BdStoreTask.status=='completed',1),else_=0)),
        func.count(func.distinct(BdRegionTask.id)),func.count(func.distinct(BdGridTask.id)),
        func.count(func.distinct(BdRegionTask.team_id))).join(
        BdGridTask,BdGridTask.region_task_id==BdRegionTask.id).outerjoin(
        BdStoreTask,BdStoreTask.grid_task_id==BdGridTask.id).filter(
        BdRegionTask.main_task_id.in_([t.id for t in tasks]))
    if user.role!='admin':
        query=query.filter(BdRegionTask.id.in_(visible_regions(db,user).with_entities(BdRegionTask.id)),
            or_(BdRegionTask.leader_id==user.id,BdGridTask.assignee_id==user.id))
    return {i:{'total':n,'done':d or 0,'pct':(d or 0)*100//n if n else 0,'regions':r,'grids':g,'teams':tm}
            for i,n,d,r,g,tm in query.group_by(BdRegionTask.main_task_id)}


def bulk_assign(db,user,region_id,selections,target_id):
    rt=db.get(BdRegionTask,region_id)
    if not rt:raise ops.OperationError('片区任务不存在')
    main=ops.locked(db,BdMainTask,rt.main_task_id)
    if main.status=='completed':raise ops.OperationError('该批次已完成')
    captain(db,user,rt)
    if not selections or len(selections)>1000:raise ops.OperationError('请选择网格')
    parsed={}
    try:
        for value in selections:
            identity,revision=map(int,value.split(':'));parsed[identity]=revision
    except (ValueError,AttributeError):raise ops.OperationError('网格选择无效')
    grids=db.query(BdGridTask).filter(BdGridTask.id.in_(parsed)).order_by(BdGridTask.id).with_for_update().all()
    if len(grids)!=len(parsed) or any(g.region_task_id!=rt.id for g in grids):
        raise ops.OperationError('不能操作其他片区网格')
    if target_id is not None:
        target=db.get(User,target_id)
        if not target:raise ops.OperationError('队员不存在')
        ops.require_active(target);member(db,target,rt)
    for g in grids:
        ops.revision_ok(g,parsed[g.id])
        if g.status=='completed':raise ops.OperationError('已完成网格不能派发')
    for g in grids:
        before={'assignee_id':g.assignee_id};g.assignee_id=target_id;g.revision+=1
        g.status='in_progress' if g.progress else ('assigned' if target_id else 'pending')
        ops.audit_log(db,user,'task','assign_grid_task',g,before,{'assignee_id':target_id,'main_task_id':main.id})
    return main.id
