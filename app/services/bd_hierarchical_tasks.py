"""独立批次的任务及快照；只写bd_*，不修改基础片区、网格或商户主档。"""
import uuid
import math
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
from sqlalchemy import func, text, bindparam, case, or_, String, insert
from app.models import (BdMainTask, BdRegionTask, BdGridTask, BdStoreTask,
                        BdRegion, BdWorkGrid, BdGridStore, BdWorkStore, BdTeam, BdTeamMember, User,
                        BdTaskEvent)
from app.services import bd_zone_operations as ops
from app.services import bd_grid_metadata as metadata

STORE_STATUSES=('normal','abnormal','uncertain','closed')
STORE_TASK_FILTERS=('completed','pending')
GRID_TASK_FILTERS=('unassigned','assigned','in_progress','completed')


def require_enabled(main):
    if main.status == 'disabled':
        raise ops.OperationError('该批次已禁用，不能更新计划或巡店记录')


def disable_batch(db, user, task_id, revision):
    ops.require_admin(user)
    main = ops.locked(db, BdMainTask, task_id)
    ops.revision_ok(main, revision)
    if main.status == 'disabled':
        return main.id
    before = {'status': main.status}
    main.status = 'disabled'
    main.revision += 1
    ops.audit_log(db, user, 'task', 'disable_batch', main, before, {'status': 'disabled'})
    return main.id


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
            BdTaskEvent.store_task_id.cast(String).ilike(like),
            metadata.event_match(db,keyword)))
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


def _postgres_batch_sources(db, grid_ids):
    """在当前事务内固定来源快照，避免将全地区商户搬进Python内存。"""
    # 标识符只来自内部UUID；临时表随事务结束删除，不进入业务表或迁移。
    table = 'bd_batch_sources_' + uuid.uuid4().hex
    db.execute(text(f"""CREATE TEMP TABLE {table} ON COMMIT DROP AS
        SELECT m.grid_id,m.source_id,
            COALESCE(w.name,s.name) AS name,COALESCE(w.address,s.address,'') AS address,
            COALESCE(w.lat,s.lat)::double precision AS lat,
            COALESCE(w.lon,s.lon)::double precision AS lon
        FROM bd_grid_store m LEFT JOIN bd_work_store w ON w.id=m.source_id
        LEFT JOIN public.kanto_merchants_v20261029 s ON s.place_id=m.source_id
        WHERE m.grid_id IN :ids""").bindparams(bindparam('ids', expanding=True)), {'ids':grid_ids})
    total, unique, missing = db.execute(text(f"""SELECT COUNT(*),COUNT(DISTINCT source_id),
        COUNT(*) FILTER (WHERE name IS NULL OR name='') FROM {table}""")).one()
    if total != unique:
        raise ops.OperationError('同一批次存在重复店铺归属')
    if missing:
        raise ops.OperationError('存在缺失源信息的店铺，不能生成任务快照')
    counts = {identity:0 for identity in grid_ids}
    counts.update(dict(db.execute(text(f'SELECT grid_id,COUNT(*) FROM {table} GROUP BY grid_id')).all()))
    return table, counts, total


def _insert_postgres_batch_stores(db, source_table, main_task_id):
    """直接复制已校验快照；临时来源与全部任务同一事务提交/回滚。"""
    db.execute(text(f"""INSERT INTO bd_store_task
        (grid_task_id,store_id,snapshot,status,result,updated_at,revision)
        SELECT g.id,s.source_id,
            json_build_object('name',s.name,'address',s.address,'lat',s.lat,'lon',s.lon),
            'pending',CAST('{{}}' AS json),:now,0
        FROM {source_table} s JOIN bd_grid_task g ON g.grid_id=s.grid_id
        JOIN bd_region_task r ON r.id=g.region_task_id
        WHERE r.main_task_id=:main_task_id"""),
        {'main_task_id':main_task_id,'now':datetime.utcnow()})
    db.execute(text(f'DROP TABLE {source_table}'))


def _set_task_progress(task, total, completed):
    task.progress = completed * 100 // total if total else 100
    if isinstance(task, BdGridTask):
        task.status = ('completed' if completed == total else
                       'in_progress' if completed else
                       'assigned' if task.assignee_id else 'pending')
    elif isinstance(task, BdMainTask):
        task.status = 'completed' if completed == total else 'active'
        task.completed_at = datetime.utcnow() if task.status == 'completed' else None
    else:
        task.status = ('completed' if completed == total else
                       'in_progress' if completed else 'pending')


def _refresh_parent_progress(db, main, region):
    region_total, region_done = db.query(
        func.coalesce(func.sum(BdGridTask.total_stores), 0),
        func.coalesce(func.sum(BdGridTask.completed_stores), 0),
    ).filter(BdGridTask.region_task_id == region.id).one()
    _set_task_progress(region, region_total, region_done)
    main_total, main_done = db.query(
        func.coalesce(func.sum(BdGridTask.total_stores), 0),
        func.coalesce(func.sum(BdGridTask.completed_stores), 0),
    ).join(BdRegionTask, BdRegionTask.id == BdGridTask.region_task_id).filter(
        BdRegionTask.main_task_id == main.id).one()
    _set_task_progress(main, main_total, main_done)


def add_store_task_counts(db, grid, count=1):
    """Keep the bounded grid denominator in sync when an approved store is added."""
    grid.total_stores += count
    _set_task_progress(grid, grid.total_stores, grid.completed_stores)
    region = db.get(BdRegionTask, grid.region_task_id)
    main = db.get(BdMainTask, region.main_task_id)
    _refresh_parent_progress(db, main, region)
    grid.revision += 1
    region.revision += 1
    main.revision += 1


def create_main_task(db, user, title, region_ids):
    ops.require_admin(user)
    title = (title or '').strip()
    if not title or len(title) > 160:
        raise ops.OperationError('主任务名称须为1～160个字符')
    if not isinstance(region_ids,(list,tuple)) or not region_ids or any(type(i) is not int for i in region_ids):
        raise ops.OperationError('至少选择一个片区')
    ids = sorted(set(region_ids))
    if len(ids)>2000: raise ops.OperationError('选择片区过多')
    # 为所有涉及的地区按固定顺序加锁，避免与网格重组互锁；批次跨地区选取片区。
    release_ids=sorted(i for i, in db.query(BdRegion.release_id).filter(BdRegion.id.in_(ids)).distinct())
    from app.models import BdZoneRelease
    for release_id in release_ids: ops.locked(db,BdZoneRelease,release_id)
    regions=db.query(BdRegion).filter(BdRegion.id.in_(ids),BdRegion.state!='archived').order_by(
        BdRegion.id).populate_existing().with_for_update().all()
    if len(regions)!=len(ids): raise ops.OperationError('片区不存在或已归档，请刷新后重试')
    grids=db.query(BdWorkGrid).filter(BdWorkGrid.zone_id.in_(ids), BdWorkGrid.state != 'disabled').order_by(BdWorkGrid.id).with_for_update().all()
    if {g.zone_id for g in grids}!=set(ids): raise ops.OperationError('空片区不能创建巡店任务')
    region_sources={r.id:r.release_id for r in regions}
    if any(g.release_id!=region_sources[g.zone_id] for g in grids):
        raise ops.OperationError('网格与片区来源不一致')
    grid_ids=[g.id for g in grids]
    source_table = None
    if db.bind.dialect.name == 'postgresql':
        source_table, store_counts, store_total = _postgres_batch_sources(db, grid_ids)
    else:
        source_rows=[{'grid_id':mapping.grid_id,'source_id':mapping.source_id,
                      'name':store.name if store else None,'address':store.address if store else '',
                      'lat':store.lat if store else None,'lon':store.lon if store else None}
                     for mapping,store in db.query(BdGridStore,BdWorkStore).outerjoin(
                         BdWorkStore,BdWorkStore.id==BdGridStore.source_id).filter(
                             BdGridStore.grid_id.in_(grid_ids)).order_by(BdGridStore.id)]
        seen=set()
        store_counts={g.id:0 for g in grids}
        for row in source_rows:
            if row['source_id'] in seen: raise ops.OperationError('同一批次存在重复店铺归属')
            seen.add(row['source_id'])
            if not row['name']: raise ops.OperationError('存在缺失源信息的店铺，不能生成任务快照')
            for coordinate in ('lat','lon'):
                if row[coordinate] is not None:row[coordinate]=float(row[coordinate])
            store_counts[row['grid_id']]+=1
        store_total = len(source_rows)
    task=BdMainTask(title=title,status='active',created_by=user.id,started_at=datetime.utcnow())
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
                      snapshot={'geometry':deepcopy(grid.geometry),'area_km2':grid.area_km2,'source_id':grid.source_id,
                                'name':grid.name,'stations':deepcopy(grid.stations or []),'keywords':deepcopy(grid.keywords or [])},
                      status='pending' if store_counts[grid.id] else 'completed',
                      progress=0 if store_counts[grid.id] else 100,
                      total_stores=store_counts[grid.id],completed_stores=0)
        db.add(gt);grid_tasks[grid.id]=gt
    db.flush()
    for rt in region_tasks.values():
        children=[g for g in grid_tasks.values() if g.region_task_id==rt.id]
        total=sum(g.total_stores for g in children)
        completed=sum(g.completed_stores for g in children)
        _set_task_progress(rt,total,completed)
        if all(g.status=='completed' for g in children):rt.status='completed'
    if all(rt.status=='completed' for rt in region_tasks.values()):
        task.status='completed';task.completed_at=datetime.utcnow()
    _set_task_progress(task,store_total,0)
    if task.status=='completed':task.completed_at=datetime.utcnow()
    if source_table is not None:
        _insert_postgres_batch_stores(db, source_table, task.id)
    else:
        # 其他数据库分批写入；任一批失败仍回滚整个巡店批次。
        for offset in range(0, len(source_rows), 1000):
            db.execute(insert(BdStoreTask), [
                {'grid_task_id': grid_tasks[row['grid_id']].id, 'store_id': row['source_id'],
                 'snapshot': {k: row[k] for k in ('name', 'address', 'lat', 'lon')}, 'result': {}}
                for row in source_rows[offset:offset + 1000]
            ])
    ops.audit_log(db,user,'task','create_batch',task,{}, {'title':title,'regions':ids,
        'grids':len(grids),'stores':store_total})
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


def assignment_members(db,team_ids):
    """批次派发的同一候选池：本队在岗队长和队员，按person_code关联账号。"""
    if not team_ids:return []
    return (db.query(User,BdTeamMember.team_id)
        .join(BdTeamMember,BdTeamMember.person_code==User.person_code)
        .join(BdTeam,BdTeam.id==BdTeamMember.team_id)
        .filter(BdTeamMember.team_id.in_(team_ids),BdTeamMember.role.in_(('leader','member')),
                BdTeamMember.end_date.is_(None),BdTeam.status=='active',
                User.is_active.is_(True),User.status=='active',User.role.in_(('staff','leader')))
        .order_by(User.id).all())


def member(db,user,rt):
    team=db.get(BdTeam,rt.team_id) if rt.team_id else None
    if not team or team.status!='active':raise ops.OperationError('任务团队不存在或已停用')
    if user.id==rt.leader_id:
        captain(db,user,rt)
        return
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
    keyword=(keyword or '').strip()[:100]
    task=db.get(BdMainTask,task_id)
    if not task: raise ops.OperationError('主任务不存在')
    region_query=visible_regions(db,user).filter(BdRegionTask.main_task_id==task_id)
    # 管理员在片区行分团队；队长在片区下分网格负责人，两端分别按可分派对象筛选。
    if user.role=='admin' and task_state in ('unassigned','assigned'):
        region_query=region_query.filter(BdRegionTask.team_id.is_(None) if task_state=='unassigned'
                                         else BdRegionTask.team_id.is_not(None))
    region_rows=region_query.add_columns(BdRegion.name).outerjoin(
        BdRegion,BdRegion.id==BdRegionTask.region_id).order_by(BdRegionTask.id).all()
    region_tasks=[rt for rt,_ in region_rows]
    current_regions={rt.region_id:name for rt,name in region_rows}
    if user.role!='admin' and not region_tasks: raise ops.OperationError('不能访问其他批次任务')
    region_ids=[rt.id for rt in region_tasks]
    grid_query=db.query(BdGridTask).filter(BdGridTask.region_task_id.in_(region_ids))
    if user.role!='admin':
        leader_regions=[rt.id for rt in region_tasks if rt.leader_id==user.id]
        grid_query=grid_query.filter((BdGridTask.assignee_id==user.id)|BdGridTask.region_task_id.in_(leader_regions))
    grid_rows=grid_query.add_columns(BdWorkGrid.name,BdWorkGrid.stations,BdWorkGrid.keywords,
        BdWorkGrid.source_id).outerjoin(BdWorkGrid,BdWorkGrid.id==BdGridTask.grid_id).order_by(BdGridTask.id).all()
    grids=[row[0] for row in grid_rows]
    current_grids={g.grid_id:{'name':name,'stations':stations,'keywords':keywords,'source_id':source_id}
                   for g,name,stations,keywords,source_id in grid_rows}
    counts={identity:(count,done or 0) for identity,count,done in db.query(BdStoreTask.grid_task_id,
        func.count(BdStoreTask.id),func.sum(case((BdStoreTask.status=='completed',1),else_=0)))
        .filter(BdStoreTask.grid_task_id.in_([g.id for g in grids])).group_by(BdStoreTask.grid_task_id)}
    # Current metadata shares the existing queries; historical snapshots remain intact.
    region_names={rt.id:[current_regions.get(rt.region_id,''),(rt.snapshot or {}).get('name','')] for rt in region_tasks}
    def keep(g):
        if user.role!='admin':
            if task_state=='unassigned' and g.assignee_id is not None:return False
            if task_state=='assigned' and not (g.assignee_id is not None and g.status in ('pending','assigned')):return False
        if task_state=='in_progress' and g.status!='in_progress':return False
        if task_state=='completed' and g.status!='completed':return False
        current=current_grids.get(g.grid_id)
        if keyword and not metadata.matches(keyword, str(g.grid_id), str(g.id),
                *region_names[g.region_task_id], metadata.search_text(g.snapshot or {}),
                metadata.search_text(current) if current else ''):return False
        return True
    def grid_view(g):
        values=dict(g.snapshot or {})
        current=current_grids.get(g.grid_id)
        if current:values.update(name=current['name'],stations=current['stations'],keywords=current['keywords'])
        return SimpleNamespace(id=g.grid_id,**values)
    result=[{'task':rt,'region':SimpleNamespace(id=rt.region_id,**{**rt.snapshot,
                'name':current_regions.get(rt.region_id,rt.snapshot.get('name',''))}),
             'grids':[{'task':g,'grid':grid_view(g),
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
    if lock or execute:
        require_enabled(main)
    return main,region,grid


def assign_region_task(db,user,identity,team_id,revision):
    ops.require_admin(user)
    rt=db.get(BdRegionTask,identity)
    if not rt: raise ops.OperationError('片区任务不存在')
    main=ops.locked(db,BdMainTask,rt.main_task_id)
    rt=ops.locked(db,BdRegionTask,identity);ops.revision_ok(rt,revision)
    require_enabled(main)
    if main.status=='completed': raise ops.OperationError('该批次已完成')
    leader=ops.captain(db,team_id) if team_id is not None else None
    if rt.team_id!=team_id:
        for grid in db.query(BdGridTask).filter_by(region_task_id=rt.id).order_by(BdGridTask.id).with_for_update():
            grid.assignee_id=None;grid.revision+=1
            if grid.status!='completed':grid.status='in_progress' if grid.progress else 'pending'
    before={'team_id':rt.team_id,'leader_id':rt.leader_id}
    rt.team_id=team_id;rt.leader_id=leader.id if leader else None;rt.revision+=1
    ops.audit_log(db,user,'task','assign_region_task',rt,before,{'team_id':team_id,'leader_id':rt.leader_id,'main_task_id':main.id})
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
    keyword=(keyword or '').strip()[:100]
    task_state=task_state if task_state in STORE_TASK_FILTERS else ''
    status=status if status in STORE_STATUSES else ''
    query=db.query(BdStoreTask).filter(BdStoreTask.grid_task_id==grid.id)
    if keyword:
        like='%'+keyword.replace('/','//').replace('%','/%').replace('_','/_')+'%'
        query=query.filter(or_(
            BdStoreTask.snapshot['name'].as_string().ilike(like,escape='/'),
            BdStoreTask.snapshot['address'].as_string().ilike(like,escape='/'),
            BdStoreTask.store_id.ilike(like,escape='/')))
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
    now=datetime.utcnow()
    last_visits=dict(db.query(BdTaskEvent.store_source_id,func.max(BdTaskEvent.created_at)).filter(
        BdTaskEvent.store_source_id.in_([store.store_id for store in stores]),
        BdTaskEvent.event_type.in_(('complete_store_task','update_store_result')),
        BdTaskEvent.created_at<=now).group_by(BdTaskEvent.store_source_id).all())
    location_map=merchant_location_map(db,[store.store_id for store in stores])
    for store in stores:
        store.last_visited_at=last_visits.get(store.store_id)
        # No visit event means this merchant has never been visited in the task system.
        # Treat that as overdue at the merchant level, independent of this batch's task status.
        store.overdue_60d=store.last_visited_at is None or store.last_visited_at < now-timedelta(days=60)
        current=location_map.get(store.store_id,{})
        store.location_accuracy=current.get('location_accuracy') or 'pending'
        store.nav_lat=current.get('lat',store.snapshot.get('lat'))
        store.nav_lon=current.get('lon',store.snapshot.get('lon'))
        store.photo_urls=current.get('photo_urls',[])
        store.google_place_id=current.get('google_place_id')
        store.place_lookup_state=current.get('place_lookup_state','pending')
    stats=grid_progress_stats(db,grid.id);stats['page']=page
    return main,rt,grid,stores[:30],len(stores)>30,stats


def merchant_location_map(db,store_ids):
    from app.services import bd_merchant_media as media
    ids=list(dict.fromkeys(str(value) for value in store_ids if value))
    if not ids:return {}
    result={}
    if db.bind.dialect.name=='postgresql':
        query=text('''SELECT place_id,lat,lon,location_accuracy
            FROM public.kanto_merchants_v20261029 WHERE place_id IN :ids''').bindparams(
                bindparam('ids',expanding=True))
        result={row['place_id']:{'lat':row['lat'],'lon':row['lon'],
                                 'location_accuracy':row['location_accuracy']}
                for row in db.execute(query,{'ids':ids}).mappings()}
    for identity,profile in media.detail_map(db,ids).items():
        item=result.setdefault(identity,{})
        if profile.lat is not None and profile.lon is not None:
            item.update(lat=profile.lat,lon=profile.lon)
        item.update(photo_urls=list(profile.photo_urls or []),google_place_id=profile.google_place_id,
                    place_lookup_state=profile.place_lookup_state)
    return result


def update_location_accuracy(db,user,identity,accuracy,lat=None,lon=None):
    initial=db.get(BdStoreTask,identity)
    if not initial:raise ops.OperationError('店铺任务不存在')
    main,_,grid=context(db,user,initial.grid_task_id,execute=True)
    store=initial
    if accuracy not in ('pending','accurate','nonexistent','doubtful'):
        raise ops.OperationError('定位核实状态无效')
    if (lat is None)!=(lon is None):raise ops.OperationError('纬度和经度必须同时填写')
    if lat is not None:
        try:lat,lon=float(lat),float(lon)
        except (TypeError,ValueError):raise ops.OperationError('坐标格式无效')
        if not math.isfinite(lat) or not math.isfinite(lon) or not (-90<=lat<=90 and -180<=lon<=180):
            raise ops.OperationError('坐标超出有效范围')
        accuracy='accurate'
    if main.status=='completed':raise ops.OperationError('该批次已完成，不能修改定位')

    from app.services import bd_merchant_media as media
    snapshot=deepcopy(store.snapshot or {})
    store_id=store.store_id
    store_revision=store.revision
    profile_before=media.detail_map(db,[store_id]).get(store_id)
    profile_revision=profile_before.revision if profile_before else 0
    current=merchant_location_map(db,[store_id]).get(store_id,{})
    changed=lat is not None and (lat!=current.get('lat',snapshot.get('lat')) or lon!=current.get('lon',snapshot.get('lon')))
    lookup=media.lookup_place_id(snapshot.get('name',''),lat,lon) if changed else None

    # Google is queried before taking batch/grid/store locks. Re-read and validate
    # under the normal lock order before writing the coordinate result.
    main,_,grid=context(db,user,initial.grid_task_id,lock=True,execute=True)
    store=ops.locked(db,BdStoreTask,identity)
    if store.store_id!=store_id:raise ops.OperationError('店铺任务已变化，请刷新后重试')
    if store.revision!=store_revision:raise ops.OperationError('店铺任务已变化，请刷新后重试')
    if main.status=='completed':raise ops.OperationError('该批次已完成，不能修改定位')
    profile=media.locked_detail(db,store_id)
    if profile.revision!=profile_revision:raise ops.OperationError('商户信息已变化，请刷新后重试')
    snapshot=deepcopy(store.snapshot or {})
    current=merchant_location_map(db,[store_id]).get(store_id,{})
    changed=lat is not None and (lat!=current.get('lat',snapshot.get('lat')) or lon!=current.get('lon',snapshot.get('lon')))
    if changed:
        if lookup is None:raise ops.OperationError('商户坐标已变化，请刷新后重试')
        profile.lat,profile.lon=lat,lon
        profile.google_place_id=lookup['google_place_id']
        profile.place_lookup_state=lookup['place_lookup_state']
        profile.revision+=1;profile.updated_at=datetime.utcnow()
    old={'location_accuracy':snapshot.get('location_accuracy','pending'),
         'lat':snapshot.get('lat'),'lon':snapshot.get('lon')}
    if db.bind.dialect.name=='postgresql':
        params={'place_id':store.store_id,'accuracy':accuracy,'lat':lat,'lon':lon}
        sql=text('''UPDATE public.kanto_merchants_v20261029
            SET location_accuracy=:accuracy,
                lat=COALESCE(:lat,lat),lon=COALESCE(:lon,lon)
            WHERE place_id=:place_id''')
        db.execute(sql,params)
    if lat is not None:
        snapshot.update(lat=lat,lon=lon)
    snapshot['location_accuracy']=accuracy
    # Keep only unfinished task snapshots aligned with the shared merchant location.
    if store.status!='completed':
        store.snapshot=snapshot
        store.revision+=1
    store.updated_at=datetime.utcnow()
    db.flush()
    ops.audit_log(db,user,'task','verify_store_location',store,old,
                  {'location_accuracy':accuracy,'lat':lat if lat is not None else old['lat'],
                   'lon':lon if lon is not None else old['lon']})
    return {'location_accuracy':accuracy,'lat':lat if lat is not None else old['lat'],
            'lon':lon if lon is not None else old['lon'],
            'google_place_id':profile.google_place_id,'place_lookup_state':profile.place_lookup_state}


def complete_store(db,user,identity,revision,request_id,status,note,*,outcome=None,photo_urls=None):
    initial=db.get(BdStoreTask,identity)
    if not initial: raise ops.OperationError('店铺任务不存在')
    main,rt,grid=context(db,user,initial.grid_task_id,lock=True,execute=True)
    store=ops.locked(db,BdStoreTask,identity)
    if status not in ('normal','abnormal','uncertain','closed') or len(note)>2000:
        raise ops.OperationError('门店状态或备注无效')
    try:uuid.UUID(request_id)
    except (ValueError,TypeError):raise ops.OperationError('提交标识无效')
    result={'business_status':status,'note':note.strip()}
    if outcome is not None:
        from app.services.bd_merchant_media import OUTCOMES
        if outcome not in OUTCOMES:raise ops.OperationError('作业结果无效')
        result['outcome']=outcome
    if photo_urls is not None:result['photo_urls']=list(photo_urls)
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
    first_completion = before['status'] != 'completed'
    if first_completion:
        grid.completed_stores += 1
    _set_task_progress(grid,grid.total_stores,grid.completed_stores)
    grid.revision+=1
    if first_completion:
        _refresh_parent_progress(db,main,rt)
        rt.revision+=1
        main.revision+=1
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
    """Explicit reconciliation; normal visits use cached per-grid counters."""
    require_enabled(main)
    db.flush()
    regions=db.query(BdRegionTask).filter_by(main_task_id=main.id).all()
    grids=db.query(BdGridTask).filter(BdGridTask.region_task_id.in_([r.id for r in regions])).all()
    counts={i:(n,d or 0) for i,n,d in db.query(BdStoreTask.grid_task_id,func.count(BdStoreTask.id),
        func.sum(case((BdStoreTask.status=='completed',1),else_=0))).filter(
            BdStoreTask.grid_task_id.in_([g.id for g in grids])).group_by(BdStoreTask.grid_task_id)}
    for g in grids:
        g.total_stores,g.completed_stores=counts.get(g.id,(0,0))
        _set_task_progress(g,g.total_stores,g.completed_stores)
        g.revision+=1
    for r in regions:
        own=[g for g in grids if g.region_task_id==r.id]
        _set_task_progress(r,sum(g.total_stores for g in own),
                           sum(g.completed_stores for g in own))
        r.revision+=1
    _set_task_progress(main,sum(g.total_stores for g in grids),
                       sum(g.completed_stores for g in grids))
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
    require_enabled(main)
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
