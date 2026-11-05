"""按当前商户坐标刷新正式网格；不改几何、不碰结算域和历史批次快照。"""
from datetime import datetime

from shapely import union_all, set_precision
from shapely.geometry import mapping, shape

from app.models import BdGridStore, BdWorkGrid, BdZone, BdZoneRelease, BdStoreVisitLog
from app.services import bd_zones as z, bd_zone_operations as ops
from app.services.bd_regions import release_ids
from app.services.bd_zone_merchants import bind_rows, database_rows

MIN_STORES = 5


def refresh(db, user, *, region='all', apply=False, source_engine=None, min_stores=MIN_STORES,
            source_table=None, unassigned_only=False):
    """调用者提交事务；默认只读，apply 时统一按版本→片区→网格顺序锁定。"""
    ops.require_admin(user)
    if isinstance(min_stores, bool) or not isinstance(min_stores, int) or not 0 <= min_stores <= 100_000:
        raise ops.OperationError('低密度网格阈值须为0～100000')
    releases = db.query(BdZoneRelease).filter(BdZoneRelease.id.in_(release_ids(db, region))).order_by(BdZoneRelease.id)
    if apply:
        releases = releases.populate_existing().with_for_update()
    releases = releases.all()
    ids = [r.id for r in releases]
    parents = db.query(BdZone).filter(BdZone.release_id.in_(ids)).order_by(BdZone.id)
    if apply:
        parents = parents.populate_existing().with_for_update()
    parents = parents.all()
    query = db.query(BdWorkGrid).filter(BdWorkGrid.release_id.in_(ids)).order_by(BdWorkGrid.id)
    if apply:
        query = query.populate_existing().with_for_update()
    grids = query.all()
    if not grids:
        raise ops.OperationError('没有可统计的正式网格')
    preserved=[g.id for g in grids if g.zone_id is not None] if unassigned_only else []
    if unassigned_only:
        grids=[g for g in grids if g.zone_id is None]
        parents=[]  # Existing territories and their counts/geometry are outside this write scope.
    if not grids:
        return {'applied':apply,'region':region,'min_stores':min_stores,'blocked_by_work':False,
                'grid_count':0,'disabled_count':0,'enabled_count':0,'mapped_stores':0,
                'disabled_stores':0,'disabled_grid_ids':[],'invalid_coordinates':0,
                'source':{},'snapshot_id':'no-unassigned-grids','grid_counts':{},
                'preserved_grid_ids':preserved}
    # 有作业中的版本不可重新绑定来源；批次使用独立快照，不在这里改写。
    blocked = any(g.assignee_id is not None or g.progress or g.task_state != 'unvisited' for g in grids) or any(
            p.team_id is not None or p.leader_id is not None for p in parents) or db.query(BdStoreVisitLog.id).filter(
                BdStoreVisitLog.grid_id.in_([g.id for g in grids])).first() is not None
    if apply and blocked:
        raise ops.OperationError('涉及已派发或已有巡店历史的范围，请先处理作业状态；本次未更新')
    geometries = [z.projected(g.geometry) for g in grids]
    work = union_all(geometries, grid_size=.001)
    doc = {'boundary': z.geographic(work.envelope), 'work_area': z.geographic(work),
           'metric_crs': 'EPSG:6677', 'captured_at': datetime.utcnow().isoformat(),
           'road_cells': [{'id': str(g.id), 'metric_geometry': mapping(geom)}
                          for g, geom in zip(grids, geometries)]}
    source_options = {'engine': source_engine}
    if source_table is not None:
        source_options['source_table'] = source_table
    with database_rows(shape(doc['boundary']).bounds, **source_options) as (info, rows):
        bound = bind_rows(doc, rows, info)
    audit = bound['merchant_audit']
    if audit['source_duplicates'] or audit['unlocated_in_work']:
        raise ops.OperationError('商户存在重复标识或无法唯一落格，请修正商户源后重试；本次未更新')
    memberships = {int(cell['id']): cell['merchant_ids'] for cell in bound['road_cells']}
    disabled = {g.id for g in grids if len(memberships[g.id]) < min_stores}
    report = {'applied': apply, 'region': region, 'min_stores': min_stores,
              'blocked_by_work': bool(blocked), 'grid_count': len(grids), 'disabled_count': len(disabled),
              'enabled_count': len(grids) - len(disabled),
              'mapped_stores': sum(map(len, memberships.values())),
              'disabled_stores': sum(len(memberships[i]) for i in disabled),
              'disabled_grid_ids': sorted(disabled),
              'invalid_coordinates': audit['invalid_coordinates'],
              'source': info, 'snapshot_id': bound['snapshot_id']}
    report['preserved_grid_ids']=preserved
    report['grid_counts'] = {str(gid): len(ids) for gid, ids in memberships.items()}
    if not apply:
        return report
    old_mappings = db.query(BdGridStore).filter(BdGridStore.grid_id.in_([g.id for g in grids]))
    old_members = {}
    for gid, source_id in old_mappings.with_entities(BdGridStore.grid_id, BdGridStore.source_id):
        old_members.setdefault(gid, set()).add(source_id)
    changed_ids = [g.id for g in grids if old_members.get(g.id, set()) != set(memberships[g.id])]
    if changed_ids:
        old_mappings.filter(BdGridStore.grid_id.in_(changed_ids)).delete(synchronize_session=False)
    changed_ids = set(changed_ids)
    now = datetime.utcnow()
    for grid in grids:
        members = memberships[grid.id]
        state = 'disabled' if grid.id in disabled else ('ready' if grid.state == 'disabled' else grid.state)
        zone_id = None if grid.id in disabled else grid.zone_id
        before = {'state': grid.state, 'zone_id': grid.zone_id, 'store_count': grid.store_count}
        if (before != {'state': state, 'zone_id': zone_id, 'store_count': len(members)}
                or old_members.get(grid.id, set()) != set(members)):
            grid.state, grid.zone_id, grid.store_count = state, zone_id, len(members)
            grid.revision += 1
            grid.updated_at = now
        # 只替换本次范围的映射；已有店铺状态和历史任务快照保留。
        if grid.id in changed_ids:
            db.add_all([BdGridStore(release_id=grid.release_id, grid_id=grid.id, source_id=identity)
                        for identity in members])
    by_parent = {}
    for grid in grids:
        if grid.zone_id is not None and grid.id not in disabled:
            by_parent.setdefault(grid.zone_id, []).append(grid)
    for parent in parents:
        members = by_parent.get(parent.id, [])
        geometry = union_all([z.projected(g.geometry) for g in members], grid_size=.001)
        state = parent.state if members else 'archived'
        area = geometry.area / 1e6
        count = sum(g.store_count for g in members)
        changed = parent.state != state or parent.area_km2 != area or parent.store_count != count
        if members and not set_precision(z.projected(parent.geometry), .001).equals(geometry):
            parent.geometry = z.geographic(geometry)
            changed = True
        if changed:
            parent.state, parent.area_km2, parent.store_count = state, area, count
            parent.workload = None
            parent.revision += 1
            parent.updated_at = now
    for release in releases:
        release.audit = {**release.audit, 'grid_density': report}
    ops.audit_log(db, user, 'grid_density', 'refresh', releases[0], {}, report)
    db.flush()
    return report
