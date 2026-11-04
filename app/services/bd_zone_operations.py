"""正式片区及H5巡店事务；仅写bd_*，源商户表只读。调用方提交事务。"""
import hashlib
import json
import math
import uuid
from datetime import datetime
from sqlalchemy import and_, bindparam, exists, func, insert, or_, select, text
from shapely import set_precision
from shapely.geometry import Point, shape
from app.models import (User, BdTeam, BdTeamMember, BdZoneRelease, BdZone,
                        BdWorkGrid, BdGridStore, BdWorkStore, BdStoreVisitLog)
from app.services import bd_log


class OperationError(ValueError):
    pass


def require_active(user):
    if not user or not user.is_active or user.status!='active':
        raise OperationError('账号未在岗')


def require_admin(user):
    require_active(user)
    if user.role!='admin':raise OperationError('仅管理员可操作')


def locked(db,model,identity):
    row=db.query(model).filter(model.id==identity).populate_existing().with_for_update().one_or_none()
    if row is None:raise OperationError('记录不存在')
    return row


def revision_ok(row,revision):
    if row.revision!=revision:raise OperationError('记录已变更，请刷新后重试')


def audit_log(db,user,domain,action,row,before,after):
    bd_log.log_op(db,user,domain,action,ref_id=row.id,ref_label=str(row.id),field='operations',
        old=json.dumps(before,ensure_ascii=False),new=json.dumps(after,ensure_ascii=False))
    # Keep the legacy audit log while recording batch task history in a typed stream.
    from app.models import BdTaskEvent, BdMainTask, BdRegionTask, BdGridTask, BdStoreTask, BdTeamMember
    if isinstance(row,(BdMainTask,BdRegionTask,BdGridTask,BdStoreTask)):
        region=row if isinstance(row,BdRegionTask) else None
        grid=row if isinstance(row,BdGridTask) else None
        store=row if isinstance(row,BdStoreTask) else None
        if region:
            main_id,region_id=region.main_task_id,region.id
        elif grid:
            region_id=grid.region_task_id;region=db.get(BdRegionTask,region_id)
            main_id=region.main_task_id if region else None
        elif store:
            grid=db.get(BdGridTask,store.grid_task_id)
            region_id=grid.region_task_id if grid else None
            region=db.get(BdRegionTask,region_id) if region_id else None
            main_id=region.main_task_id if region else None
        else:
            main_id,region_id=row.id,None
        team_id=region.team_id if region else None
        team=db.get(BdTeam,team_id) if team_id else None
        main=db.get(BdMainTask,main_id) if main_id else None
        store_snapshot=store.snapshot if store else {}
        actor_role=user.role
        if team_id and user.person_code and user.role!='admin':
            membership=db.query(BdTeamMember.role).filter_by(
                team_id=team_id,person_code=user.person_code,end_date=None).first()
            if membership:actor_role=membership[0]
        db.add(BdTaskEvent(
            main_task_id=main_id or 0,region_task_id=region_id,
            grid_task_id=grid.id if grid else (store.grid_task_id if store else None),
            store_task_id=store.id if store else None,event_type=action,
            main_label=main.title if main else '',
            region_label=(region.snapshot or {}).get('name','') if region else '',
            grid_label=str(grid.grid_id) if grid else '',
            store_label=store_snapshot.get('name','') if store else '',
            store_source_id=store.store_id if store else '',
            actor_id=user.id,actor_label=user.display_name or user.username or '',
            actor_role=actor_role,team_id=team_id,team_label=team.name if team else '',
            request_id=store.request_id if store else None,
            old_value=before or {},new_value=after or {},
        ))


def captain(db,team_id):
    team=db.get(BdTeam,team_id)
    if not team or team.status!='active':raise OperationError('团队不存在或已停用')
    memberships=db.query(BdTeamMember).filter_by(team_id=team_id,role='leader',end_date=None).all()
    users=db.query(User).join(BdTeamMember,BdTeamMember.person_code==User.person_code).filter(
        BdTeamMember.team_id==team_id,BdTeamMember.role=='leader',BdTeamMember.end_date.is_(None),
        User.is_active.is_(True),User.status=='active',User.role.in_(('leader','staff'))).all()
    if len(memberships)!=1 or len(users)!=1:
        raise OperationError('团队必须只有一名在岗且已建账号的队长')
    return users[0]


def require_captain(db,user,zone):
    require_active(user)
    if zone.team_id is None or zone.leader_id!=user.id or captain(db,zone.team_id).id!=user.id:
        raise OperationError('仅当前片区队长可派发；换队长后请管理员执行交接')


def require_member(db,user,zone):
    require_active(user)
    if not zone.team_id or user.id==zone.leader_id:raise OperationError('队长不参与队员作业')
    membership=db.query(BdTeamMember).filter_by(team_id=zone.team_id,person_code=user.person_code,
                                             role='member',end_date=None).first()
    if membership is None or user.role not in ('staff','leader'):
        raise OperationError('必须为本队在岗队员')


def formal_generate(db,user,result,doc):
    """同源同参数幂等，问题单元隔离保存；不自动分派、不覆盖已有版本。"""
    require_admin(user)
    audit=result.get('audit',{})
    if not audit.get('geometric_pass') or not audit.get('merchant_pass'):
        raise OperationError('覆盖或商户唯一归属检查失败，不能正式生成')
    if result['meta']['snapshot_id']!=doc['snapshot_id']:raise OperationError('商户版本不一致')
    from app.services.bd_zone_rules import count_limit
    from app.services import bd_zones as z
    policy = audit.get('generation_rules')
    expected_limit = count_limit(z.projected(doc['work_area']).area / 1e6,
                                 result['summary']['target_area_km2'])
    if len(result['features']) > expected_limit or (policy and policy.get('max_zone_count') != expected_limit):
        raise OperationError('片区数量超过生成规则上限')
    identity={'source':doc['snapshot_id'],'parameters':result['summary']['balance'],
              'zones':sorted(f['id'] for f in result['features']),
              'grids':sorted(f['id'] for f in result['grids'])}
    fingerprint=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    if db.bind.dialect.name=='postgresql':db.execute(text('SELECT pg_advisory_xact_lock(70422301)'))
    prior=db.query(BdZoneRelease).filter_by(fingerprint=fingerprint).first()
    if prior:return prior
    if db.query(BdZoneRelease).first():raise OperationError('已有正式版本，禁止覆盖或重复生成任务')
    from app.services import bd_zones as z
    cells=doc['road_cells'];features=result['features'];grids=result['grids']
    ids={f['id'] for f in features}
    if len(ids)!=len(features) or any(g['properties']['zone_id'] not in ids for g in grids):
        raise OperationError('片区或网格关联无效')
    base=[set_precision(shape(c['metric_geometry']),.001) for c in cells]
    polygons=[z.projected(g['geometry']) for g in grids]
    # 直接沿用生成时的拓扑成员关系。极薄道路面在毫米精度下取代表点
    # 可能落在面外，不能再次用点匹配推测归属；下方仍校验完整面包含。
    indices={c['id']:i for i,c in enumerate(cells)}
    if len(indices)!=len(cells):raise OperationError('街块标识重复')
    owners={}
    for j,g in enumerate(grids):
        for identity in g['properties'].get('cell_ids',[]):
            if identity not in indices:raise OperationError('网格包含未知街块')
            i=indices[identity]
            if i in owners:raise OperationError('街块重复归属')
            owners[i]=j
    excluded_ids = audit.get('excluded_empty_cell_ids', [])
    if excluded_ids and not policy:
        raise OperationError('缺少无商户区域排除审计')
    if len(excluded_ids) != len(set(excluded_ids)) or any(identity not in indices for identity in excluded_ids):
        raise OperationError('排除街块标识无效')
    excluded = {indices[identity] for identity in excluded_ids}
    if any(cells[i]['store_count'] != 0 or cells[i].get('merchant_ids') for i in excluded):
        raise OperationError('不能排除有商户的街块')
    if set(owners) & excluded or set(owners) | excluded != set(range(len(cells))):
        raise OperationError('存在未归属街块')
    for i,j in owners.items():
        if base[i].difference(polygons[j]).area>1:raise OperationError('网格切断了街块')
    merchants=[v for c in cells for v in c['merchant_ids']]
    if len(merchants)!=len(set(merchants)) or len(merchants)!=audit['assigned_merchants']:
        raise OperationError('正式门店映射不完整')
    release=BdZoneRelease(fingerprint=fingerprint,source=result['meta']['source'],
        parameters=result['summary']['balance'],audit={**audit,'issues':result['issues'],
        'confirmation_ready':result['summary']['confirmation_ready']},reviews=result['review_merchants'],created_by=user.id)
    db.add(release);db.flush()
    zone_rows={}
    for number,f in enumerate(features,1):
        p=f['properties'];reasons=[]
        if p.get('needs_review'):reasons.append(p.get('review_reason') or 'constraint_conflict')
        if not p.get('shape_valid',False):reasons.append('shape_conflict')
        if any(g['properties']['zone_id']==f['id'] and not g['properties']['area_valid'] for g in grids):
            reasons.append('grid_area_conflict')
        row=BdZone(release_id=release.id,source_id=f['id'],name=f'片区 {number:03}',geometry=f['geometry'],
            area_km2=p['area_km2'],store_count=p['store_count'],workload=p.get('workload'),review=reasons,
            state='ready')
        db.add(row);db.flush();zone_rows[f['id']]=row
    tasks=[]
    for f in grids:
        p=f['properties'];row=BdWorkGrid(release_id=release.id,zone_id=zone_rows[p['zone_id']].id,source_id=f['id'],
            geometry=f['geometry'],area_km2=p['area_km2'],store_count=p['store_count'],
            state='ready')
        db.add(row);tasks.append(row)
    db.flush();batch=[]
    for i,c in enumerate(cells):
        if i in excluded:continue
        batch.extend(dict(release_id=release.id,grid_id=tasks[owners[i]].id,source_id=v) for v in c['merchant_ids'])
        if len(batch)>=5000:db.execute(insert(BdGridStore),batch);batch=[]
    if batch:db.execute(insert(BdGridStore),batch)
    audit_log(db,user,'zone','generate',release,{},dict(zones=len(features),grids=len(tasks),merchants=len(merchants)))
    return release


def clear_grids(db,user,zone,action):
    for grid in db.query(BdWorkGrid).filter_by(zone_id=zone.id).order_by(BdWorkGrid.id).with_for_update():
        if grid.assignee_id is not None:
            audit_log(db,user,'grid',action,grid,{'assignee_id':grid.assignee_id},{'assignee_id':None})
        grid.assignee_id=None;grid.revision+=1;grid.updated_at=datetime.utcnow()


def assign_zone(db,user,zone_id,team_id,revision):
    require_admin(user);zone=locked(db,BdZone,zone_id);revision_ok(zone,revision)
    if zone.state == 'archived' or not db.query(BdWorkGrid.id).filter_by(zone_id=zone.id).first():
        raise OperationError('空片区或已归档片区不能指派')
    locked(db,BdTeam,team_id);leader=captain(db,team_id)
    before={'team_id':zone.team_id,'leader_id':zone.leader_id}
    if zone.team_id!=team_id:clear_grids(db,user,zone,'team_transfer')
    zone.team_id,zone.leader_id=team_id,leader.id;zone.revision+=1;zone.updated_at=datetime.utcnow()
    audit_log(db,user,'zone','assign',zone,before,{'team_id':team_id,'leader_id':leader.id})
    return zone


def recall_zone(db,user,zone_id,revision):
    require_admin(user);zone=locked(db,BdZone,zone_id);revision_ok(zone,revision)
    clear_grids(db,user,zone,'zone_recall')
    audit_log(db,user,'zone','recall',zone,{'team_id':zone.team_id,'leader_id':zone.leader_id},{})
    zone.team_id=zone.leader_id=None;zone.revision+=1;zone.updated_at=datetime.utcnow()


def assign_grid(db,user,grid_id,target_id,revision):
    snapshot=db.get(BdWorkGrid,grid_id)
    if not snapshot:raise OperationError('网格不存在')
    zone=locked(db,BdZone,snapshot.zone_id);require_captain(db,user,zone)
    grid=locked(db,BdWorkGrid,grid_id);revision_ok(grid,revision)
    if target_id is not None:
        target=db.get(User,target_id)
        if not target:raise OperationError('队员不存在')
        require_member(db,target,zone)
    audit_log(db,user,'grid','assign' if target_id else 'recall',grid,
              {'assignee_id':grid.assignee_id},{'assignee_id':target_id})
    grid.assignee_id=target_id;grid.revision+=1;grid.updated_at=datetime.utcnow()


def executable(db,user,grid_id):
    snapshot=db.get(BdWorkGrid,grid_id)
    if not snapshot:raise OperationError('网格不存在')
    zone=locked(db,BdZone,snapshot.zone_id);grid=locked(db,BdWorkGrid,grid_id)
    require_member(db,user,zone)
    if grid.assignee_id!=user.id:
        raise OperationError('当前没有此网格执行权限')
    if captain(db,zone.team_id).id!=zone.leader_id:raise OperationError('队长已更换，请管理员交接')
    return zone,grid


def progress_state(total,visited):
    """网格作业状态的**唯一口径**：已巡店门店数 / 门店总数。

    - 0 家已巡 → 未巡店
    - 1..总数-1 已巡 → 巡店中
    - 全部已巡 → 巡店完成
    """
    total=max(0,int(total or 0));visited=max(0,int(visited or 0))
    pct=visited*100//total if total else 0
    state='visited' if total and visited>=total else ('visiting' if visited else 'unvisited')
    return pct,state


def grid_progress(db,grid_id):
    """当前网格的自动进度：总数 / 已巡店 / 百分比 / 状态（不手填）。"""
    total=db.query(func.count(BdGridStore.id)).filter(BdGridStore.grid_id==grid_id).scalar() or 0
    visited=db.query(func.count(func.distinct(BdStoreVisitLog.store_id))).filter(
        BdStoreVisitLog.grid_id==grid_id).scalar() or 0
    pct,state=progress_state(total,visited)
    return {'total':total,'visited':visited,'pct':pct,'state':state}


def zone_progress_map(db,grid_ids):
    """一批网格的自动进度：一条聚合 SQL，不按网格数增长。"""
    ids=list(grid_ids)
    if not ids:return {}
    if db.bind.dialect.name=='postgresql':
        sql=text('''SELECT g.id AS grid_id,COUNT(DISTINCT r.source_id) AS total,
            COUNT(DISTINCT v.store_id) AS visited
            FROM bd_work_grid g
            LEFT JOIN bd_grid_store r ON r.grid_id=g.id
            LEFT JOIN bd_store_visit_log v ON v.grid_id=g.id AND v.store_id=r.source_id
            WHERE g.id IN :ids GROUP BY g.id''').bindparams(bindparam('ids',expanding=True))
        rows=[dict(r) for r in db.execute(sql,{'ids':ids}).mappings()]
    else:
        rows=[{'grid_id':row[0],'total':row[1] or 0,'visited':row[2] or 0} for row in db.query(
            BdWorkGrid.id,func.count(func.distinct(BdGridStore.source_id)),
            func.count(func.distinct(BdStoreVisitLog.store_id))).outerjoin(
            BdGridStore,BdGridStore.grid_id==BdWorkGrid.id).outerjoin(
            BdStoreVisitLog,and_(BdStoreVisitLog.grid_id==BdWorkGrid.id,
                                 BdStoreVisitLog.store_id==BdGridStore.source_id)).filter(
            BdWorkGrid.id.in_(ids)).group_by(BdWorkGrid.id).all()]
    result={}
    for row in rows:
        pct,state=progress_state(row['total'],row['visited'])
        result[row['grid_id']]={'total':row['total'],'visited':row['visited'],'pct':pct,'state':state}
    return result


def sync_grid_progress(db,grid):
    """把自动进度写回网格行（片区列表/管理面板/重建守卫共用同一口径）。"""
    stats=grid_progress(db,grid.id)
    grid.progress=stats['pct'];grid.task_state=stats['state'];grid.updated_at=datetime.utcnow()
    return stats


def source_store(db,identity):
    if db.bind.dialect.name!='postgresql':return None
    return db.execute(text('SELECT place_id,name,address,lat,lon FROM public.kanto_merchants_v20261029 WHERE place_id=:id'),{'id':identity}).mappings().first()


def store_snapshot(row):
    return dict(name=row.name,address=row.address,lat=row.lat,lon=row.lon,status=row.status)


def visit(db,user,grid_id,identity,request_id,name,address,lat,lon,status,note,*,create=False,store_revision=None):
    zone,grid=executable(db,user,grid_id)
    try:uuid.UUID(request_id)
    except (ValueError,TypeError):raise OperationError('提交标识无效')
    prior=db.query(BdStoreVisitLog).filter_by(request_id=request_id).first()
    if prior:
        if prior.actor_id!=user.id or prior.grid_id!=grid_id or (not create and prior.store_id!=identity):
            raise OperationError('提交标识冲突')
        return db.get(BdWorkStore,prior.store_id)
    if not name.strip() or len(name)>300 or len(address)>1000 or len(note)>2000:
        raise OperationError('门店名称或备注长度无效')
    if not math.isfinite(lat) or not math.isfinite(lon) or not -90<=lat<=90 or not -180<=lon<=180:
        raise OperationError('经纬度无效')
    if status not in ('normal','abnormal','uncertain','closed'):raise OperationError('门店状态无效')
    if not shape(grid.geometry).covers(Point(lon,lat)):raise OperationError('门店坐标不在当前网格内')
    if create:
        identity='manual:'+str(uuid.uuid4())
        row=BdWorkStore(id=identity,name=name.strip(),address=address.strip(),lat=lat,lon=lon,status=status,revision=0)
        db.add(row);db.add(BdGridStore(release_id=zone.release_id,grid_id=grid.id,source_id=identity));before={}
        grid.store_count+=1;zone.store_count+=1
    else:
        if not db.query(BdGridStore.id).filter_by(grid_id=grid_id,source_id=identity).first():
            raise OperationError('门店不属于当前网格')
        if db.bind.dialect.name=='postgresql':
            db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:id,0))'),{'id':identity})
        row=db.query(BdWorkStore).filter_by(id=identity).with_for_update().first()
        if row is None:
            raw=source_store(db,identity)
            if raw is None:raise OperationError('源门店不存在')
            if store_revision not in (None,0):raise OperationError('门店已变更')
            row=BdWorkStore(id=identity,name=raw['name'],address=raw['address'] or '',lat=raw['lat'],lon=raw['lon'],status='normal',revision=0)
            db.add(row);db.flush()
        if store_revision is not None:revision_ok(row,store_revision)
        before=store_snapshot(row)
        row.name,row.address,row.lat,row.lon,row.status=name.strip(),address.strip(),lat,lon,status
    row.revision+=1;row.updated_at=datetime.utcnow();db.flush()
    db.add(BdStoreVisitLog(request_id=request_id,store_id=identity,grid_id=grid_id,actor_id=user.id,
        operation='create_store' if create else 'visit_update',before=before,after=store_snapshot(row),note=note.strip()))
    db.flush()
    # 网格作业进展 = 巡店留痕自动算（不再让队员手填百分比）
    sync_grid_progress(db,grid)
    return row


def visible_zones(db,user):
    require_active(user)
    query=db.query(BdZone).filter(BdZone.state != 'archived')
    if user.role!='admin':
        leader_teams=db.query(BdTeamMember.team_id).filter_by(person_code=user.person_code,role='leader',end_date=None)
        member_grids=db.query(BdWorkGrid.zone_id).filter_by(assignee_id=user.id)
        query=query.filter(((BdZone.leader_id==user.id)&BdZone.team_id.in_(leader_teams))|BdZone.id.in_(member_grids))
    return query.order_by(BdZone.id).all()


def can_view(db,user,zone):
    require_active(user)
    if user.role=='admin':return
    if zone.leader_id==user.id:
        require_captain(db,user,zone);return
    require_member(db,user,zone)
    if not db.query(BdWorkGrid.id).filter_by(zone_id=zone.id,assignee_id=user.id).first():
        raise OperationError('不能访问其他片区')


STORE_STATUSES=('normal','abnormal','uncertain','closed')
STORE_VISIT_FILTERS=('visited','unvisited')
GRID_STATE_FILTERS=('unassigned','assigned','visiting','visited')


def stores_page(db,grid_id,page=1,keyword='',*,task_state='',status=''):
    """每页最多30家，源门店只读，叠加作业状态与门店状态；新门店也走相同ID。

    筛选口径（队员端列表头）：
    - task_state：visited=已巡店 / unvisited=未巡店（按本网格的巡店留痕判定）；
    - status：bd_work_store 上的门店状态（正常/异常/存疑/关店）；
    - keyword：名称、地址或商户编号，任一命中即算。
    """
    page=max(1,page);offset=(page-1)*30
    keyword=(keyword or '')[:100]
    task_state=task_state if task_state in STORE_VISIT_FILTERS else ''
    status=status if status in STORE_STATUSES else ''
    visited_sql='EXISTS(SELECT 1 FROM bd_store_visit_log v WHERE v.store_id=r.source_id AND v.grid_id=r.grid_id)'
    if db.bind.dialect.name=='postgresql':
        conditions=['r.grid_id=:grid'];params={'grid':grid_id,'offset':offset}
        if keyword:
            params['kw']='%'+keyword+'%'
            conditions.append("(COALESCE(w.name,s.name,'') ILIKE :kw OR COALESCE(w.address,s.address,'') ILIKE :kw OR r.source_id ILIKE :kw)")
        if status:
            params['status']=status
            conditions.append("COALESCE(w.status,'normal')=:status")
        if task_state=='visited':conditions.append(visited_sql)
        elif task_state=='unvisited':conditions.append('NOT '+visited_sql)
        query=text('''SELECT r.source_id AS id,COALESCE(w.name,s.name) AS name,
          COALESCE(w.address,s.address,'') AS address,COALESCE(w.lat,s.lat) AS lat,
          COALESCE(w.lon,s.lon) AS lon,COALESCE(w.status,'normal') AS status,COALESCE(w.revision,0) AS revision,
          ''' + visited_sql + ''' AS visited
          FROM bd_grid_store r LEFT JOIN bd_work_store w ON w.id=r.source_id
          LEFT JOIN public.kanto_merchants_v20261029 s ON s.place_id=r.source_id
          WHERE ''' + ' AND '.join(conditions) + '''
          ORDER BY r.source_id LIMIT 31 OFFSET :offset''')
        rows=[dict(r) for r in db.execute(query,params).mappings()]
    else:
        visited_expr=exists(select(BdStoreVisitLog.id).where(and_(
            BdStoreVisitLog.store_id==BdGridStore.source_id,
            BdStoreVisitLog.grid_id==grid_id)))
        query=db.query(BdWorkStore).join(BdGridStore,BdGridStore.source_id==BdWorkStore.id).filter(
            BdGridStore.grid_id==grid_id)
        if keyword:
            query=query.filter(or_(BdWorkStore.name.contains(keyword),BdWorkStore.address.contains(keyword),
                BdGridStore.source_id.contains(keyword)))
        if status:query=query.filter(BdWorkStore.status==status)
        if task_state=='visited':query=query.filter(visited_expr)
        elif task_state=='unvisited':query=query.filter(~visited_expr)
        rows=[{'id':row.id,**store_snapshot(row),'revision':row.revision,'visited':bool(visited)}
              for row,visited in query.add_columns(visited_expr.label('visited')).order_by(
                  BdWorkStore.id).offset(offset).limit(31).all()]
    return rows[:30],len(rows)>30


def zone_grids(db,zone_id,assignee_id=None,*,task_state='',keyword=''):
    """片区内的网格清单（队长看全部，队员只看自己的）+ 每个网格的**自动进度**。

    作业状态按巡店留痕自动算（`zone_progress_map`），筛选与显示用同一口径，
    不再依赖手填的 `bd_work_grid.progress/task_state`。
    """
    task_state=task_state if task_state in GRID_STATE_FILTERS else ''
    keyword=(keyword or '')[:100]
    query=db.query(BdWorkGrid).filter(BdWorkGrid.zone_id==zone_id)
    if assignee_id is not None:query=query.filter(BdWorkGrid.assignee_id==assignee_id)
    if keyword:
        conditions=[BdWorkGrid.source_id.contains(keyword)]
        if keyword.isdigit():conditions.append(BdWorkGrid.id==int(keyword))
        query=query.filter(or_(*conditions))
    grids=query.order_by(BdWorkGrid.id).all()
    progress=zone_progress_map(db,[g.id for g in grids])
    if task_state=='unassigned':
        grids=[g for g in grids if g.assignee_id is None]
    elif task_state=='assigned':
        grids=[g for g in grids if g.assignee_id is not None and progress[g.id]['state']=='unvisited']
    elif task_state in ('visiting','visited'):
        grids=[g for g in grids if progress[g.id]['state']==task_state]
    return grids,progress


def management_context(db, user, *, with_geometry=True, region='tokyo23'):
    """管理员地图：批量取责任信息，不读取商户源或重新划分。"""
    require_admin(user)
    teams = db.query(BdTeam).order_by(BdTeam.id).all()
    accounts = db.query(User).all()
    memberships = db.query(BdTeamMember).filter_by(role='leader', end_date=None).all()
    leaders, by_code = {}, {}
    for membership in memberships:
        leaders.setdefault(membership.team_id, []).append(membership.person_code)
    for account in accounts:
        if account.is_active and account.status == 'active' and account.role in ('staff', 'leader'):
            by_code.setdefault(account.person_code, []).append(account)
    eligible = [team for team in teams if team.status == 'active'
                and len(leaders.get(team.id, [])) == 1
                and len(by_code.get(leaders[team.id][0], [])) == 1]
    names = {account.id: account.display_name or account.username for account in accounts}
    team_names = {team.id: team.name for team in teams}
    if not with_geometry:
        return dict(teams=eligible, names=names, team_names=team_names)
    from sqlalchemy import func
    from app.services.bd_regions import release_ids
    zones = db.query(BdZone).filter(BdZone.state!='archived',BdZone.release_id.in_(release_ids(db,region))).order_by(BdZone.id).all()
    counts = dict(db.query(BdWorkGrid.zone_id, func.count(BdGridStore.id)).join(
        BdGridStore, BdGridStore.grid_id == BdWorkGrid.id).group_by(BdWorkGrid.zone_id).all())
    progresses = dict(db.query(BdWorkGrid.zone_id, func.avg(BdWorkGrid.progress))
                      .group_by(BdWorkGrid.zone_id).all())
    from app.services.bd_grid_geometry import display_geometry
    boundaries = {}
    for identity, geometry in db.query(BdWorkGrid.zone_id, BdWorkGrid.geometry).join(
            BdZone, BdZone.id == BdWorkGrid.zone_id).filter(BdZone.merge_boundaries.is_(False)).all():
        boundaries.setdefault(identity, []).append(display_geometry(geometry))
    geometry = {'type': 'FeatureCollection', 'features': [
        {'type': 'Feature', 'geometry': {'type':'GeometryCollection','geometries':boundaries[zone.id]}
         if zone.id in boundaries else display_geometry(zone.geometry), 'properties': {
            'id': zone.id, 'name': zone.name, 'area': zone.area_km2, 'preserve_holes':True,
            'stores': counts.get(zone.id, 0), 'progress': round(float(progresses.get(zone.id) or 0), 1),
            'assigned': zone.team_id is not None,
            'team': team_names.get(zone.team_id, ''), 'leader': names.get(zone.leader_id, '')}}
        for zone in zones]}
    residual_count = sum('no_shared_edge_neighbour' in (zone.review or []) for zone in zones)
    return dict(zones=zones, teams=eligible, names=names, team_names=team_names, geometry=geometry,
                candidate_count=len(zones)-residual_count, residual_count=residual_count)


def management_detail(db, user, zone_id):
    """网格平均进度；商户经营状态按当前唯一映射统计，未记录单列。"""
    from sqlalchemy import func
    require_admin(user)
    zone = db.get(BdZone, zone_id)
    if zone is None or zone.state == 'archived':
        raise OperationError('片区不存在')
    grids = db.query(BdWorkGrid).filter_by(zone_id=zone_id).order_by(BdWorkGrid.id).all()
    statuses = dict(db.query(BdWorkStore.status, func.count(BdGridStore.id))
        .select_from(BdGridStore).join(BdWorkGrid, BdWorkGrid.id == BdGridStore.grid_id)
        .outerjoin(BdWorkStore, BdWorkStore.id == BdGridStore.source_id)
        .filter(BdWorkGrid.zone_id == zone_id).group_by(BdWorkStore.status).all())
    status_counts = {key: statuses.get(key, 0) for key in ('normal', 'abnormal', 'uncertain', 'closed')}
    status_counts['unverified'] = sum(value for key, value in statuses.items() if key not in status_counts)
    total = sum(status_counts.values())
    visited = db.query(BdGridStore.id).join(BdWorkGrid, BdWorkGrid.id == BdGridStore.grid_id).filter(
        BdWorkGrid.zone_id == zone_id, db.query(BdStoreVisitLog.id).filter(
            BdStoreVisitLog.grid_id == BdGridStore.grid_id,
            BdStoreVisitLog.store_id == BdGridStore.source_id).exists()).count()
    # 进度用**实时口径**（巡店留痕），与队员端同一套算法，避免与"已巡店商户"自相矛盾。
    stats = zone_progress_map(db, [grid.id for grid in grids])
    progress = round(sum(item['pct'] for item in stats.values()) / len(stats), 1) if stats else 0
    from app.services.bd_grid_geometry import display_geometry
    grid_geometry = {'type': 'FeatureCollection', 'features': [
        {'type': 'Feature', 'geometry': display_geometry(grid.geometry),
         'properties': {'id': grid.id, 'review': grid.state != 'ready', 'preserve_holes':True}} for grid in grids]}
    release = db.get(BdZoneRelease, zone.release_id)
    from app.services.bd_zone_balance import BalanceOptions
    parameters = release.parameters or {}
    defaults = vars(BalanceOptions())
    parameters = {key: parameters.get(key, value) for key, value in defaults.items()}
    options = BalanceOptions(**parameters).validate()
    load = dict(parameters, area=zone.area_km2, merchants=total,
                merchant_weight=1-options.area_weight,
                score=options.score(zone.area_km2*1e6, total))
    return dict(zone=zone, grids=grids, grid_geometry=grid_geometry,
                merchant_total=total, status_counts=status_counts, load=load,
                visited=visited, progress=progress,
                completed=sum(item['state'] == 'visited' for item in stats.values()),
                visiting=sum(item['state'] == 'visiting' for item in stats.values()),
                unvisited=sum(item['state'] == 'unvisited' for item in stats.values()),
                assigned=sum(grid.assignee_id is not None for grid in grids))


def replace_unused_zone_release(db, user, result, doc):
    """显式覆盖尚未开展的划分；调用方先备份，整个替换在同一事务提交。"""
    require_admin(user)
    if db.bind.dialect.name == 'postgresql':
        db.execute(text('SELECT pg_advisory_xact_lock(70422301)'))
        db.execute(text('LOCK TABLE bd_zone_release, bd_region, bd_work_grid, bd_grid_store, bd_store_visit_log IN EXCLUSIVE MODE'))
    old = db.query(BdZoneRelease).order_by(BdZoneRelease.id).with_for_update().all()
    if not old:
        return formal_generate(db, user, result, doc)
    from app.models import BdMainTask
    if db.query(BdMainTask.id).first():
        raise OperationError('已有巡店批次历史，禁止覆盖片区和网格版本')
    if db.query(BdStoreVisitLog.id).first() or db.query(BdWorkGrid.id).filter(
            (BdWorkGrid.assignee_id.isnot(None)) | (BdWorkGrid.progress != 0)
            | (BdWorkGrid.task_state != 'unvisited')).first():
        raise OperationError('已有网格派发或巡店进展，禁止覆盖划分')
    # 迁移已有团队归属：用商户完整集合映射，避免按名称/最近距离猜测。
    owner = {}
    for grid in result['grids']:
        for identity in grid['properties'].get('cell_ids', []):
            owner[identity] = grid['properties']['zone_id']
    merchant_owners = {merchant: owner[cell['id']] for cell in doc['road_cells']
                       if cell['id'] in owner for merchant in cell['merchant_ids']}
    assignments = {}
    for zone in db.query(BdZone).filter(BdZone.team_id.isnot(None)).all():
        merchants = [row[0] for row in db.query(BdGridStore.source_id).join(
            BdWorkGrid, BdWorkGrid.id == BdGridStore.grid_id).filter(BdWorkGrid.zone_id == zone.id)]
        targets = {merchant_owners.get(merchant) for merchant in merchants}
        if not merchants or None in targets:
            raise OperationError('已有团队商户缺少新片区归属，禁止覆盖')
        for target in targets:
            prior = assignments.get(target)
            if prior and prior != (zone.team_id, zone.leader_id):
                raise OperationError('多个团队归属冲突，禁止覆盖')
            assignments[target] = (zone.team_id, zone.leader_id)
    old_ids = [release.id for release in old]
    db.query(BdGridStore).delete(synchronize_session=False)
    db.query(BdWorkGrid).delete(synchronize_session=False)
    db.query(BdZone).delete(synchronize_session=False)
    db.query(BdZoneRelease).delete(synchronize_session=False)
    db.flush()
    release = formal_generate(db, user, result, doc)
    for source_id, (team_id, leader_id) in assignments.items():
        zone = db.query(BdZone).filter_by(release_id=release.id, source_id=source_id).one()
        zone.team_id, zone.leader_id = team_id, leader_id
        zone.revision += 1
        audit_log(db, user, 'zone', 'carry_assignment', zone, {},
                  dict(team_id=team_id, leader_id=leader_id, previous_releases=old_ids))
    audit_log(db, user, 'zone', 'replace_release', release, dict(releases=old_ids),
              dict(release_id=release.id, carried_assignments=len(assignments)))
    return release
