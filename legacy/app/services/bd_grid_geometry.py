"""闭合外轮廓与真实作业几何分离；包含归并不填水、不连接外部飞地。"""
from shapely import STRtree, union_all
from shapely.geometry import Polygon, mapping, shape


def outline(geometry, *, grid_size=.001):
    if geometry.geom_type == 'Polygon':
        return Polygon(geometry.exterior)
    return union_all([outline(g, grid_size=grid_size) for g in getattr(geometry, 'geoms', ())
                      if g.geom_type in ('Polygon', 'MultiPolygon')], grid_size=grid_size)


def display_geometry(geojson):
    """只供地图显示/点击，不得用于面积、门店位置校验或覆盖审计。"""
    # GeoJSON是经纬度，不能使用米制计算的.001精度（约100米角度网格）。
    # 多面外轮廓仍合并包含岛，但保留源坐标，避免显示越界和阶梯轮廓。
    return mapping(outline(shape(geojson), grid_size=0))


def coverage_matches(expected, actual, *, precision_m=.001):
    """叠加舍入只容忍既有外/内边界的2毫米带；内部漏地不能冒充量化误差。"""
    def polygonal(geometry):
        if geometry.geom_type in ('Polygon','MultiPolygon'):return geometry
        return union_all([polygonal(part) for part in getattr(geometry,'geoms',())],grid_size=.001)
    expected,actual=polygonal(expected),polygonal(actual)
    difference=expected.symmetric_difference(actual)
    if difference.area<=.01:return True
    if difference.area>expected.length*precision_m*2:return False
    return difference.difference(expected.boundary.buffer(precision_m*2)).area<=.01


def containment_groups(geometries):
    """每个内部面归到最小包围面；追溯根节点，一次处理多层包含。"""
    shells = [outline(g) for g in geometries]
    tree = STRtree(shells)
    parent = list(range(len(shells)))
    for i, shell in enumerate(shells):
        containers = [int(j) for j in tree.query(shell, predicate='covered_by')
                      if int(j) != i and shells[int(j)].area > shell.area + .01]
        if containers:
            parent[i] = min(containers, key=lambda j:(shells[j].area,j))
    groups = {}
    for i in range(len(parent)):
        root = i
        while parent[root] != root:
            root = parent[root]
        groups.setdefault(root, []).append(i)
    return groups


def consolidate(groups):
    """生成阶段：按完整自然面成员合并，被包围岛保留真实MultiPolygon。"""
    groups = list(groups)
    families = containment_groups([group['geom'] for group in groups])
    output = []
    for root, members in families.items():
        output.append({**groups[root],
            'geom':union_all([groups[i]['geom'] for i in members],grid_size=.001),
            'members':set().union(*(groups[i]['members'] for i in members)),
            'contained_group_count':len(members)-1,
            'count':sum(groups[i]['count'] for i in members)})
    return output


def repair_grids(db,user,*,apply=False):
    """存量修复，完整校验后修改；调用方备份并提交。"""
    import hashlib
    from datetime import datetime
    from sqlalchemy import func
    from app.models import BdWorkGrid,BdGridStore,BdStoreVisitLog,BdZoneRelease,BdGridTask
    from app.services import bd_zones as z,bd_zone_operations as ops
    ops.require_admin(user)
    if apply:
        # 与片区归组保持相同锁顺序。
        for identity, in db.query(BdZoneRelease.id).order_by(BdZoneRelease.id):
            ops.locked(db,BdZoneRelease,identity)
    query=db.query(BdWorkGrid).order_by(BdWorkGrid.id)
    if apply: query=query.populate_existing().with_for_update()
    rows=query.all()
    geoms=[z.projected(row.geometry) for row in rows]
    planned=[]
    for release_id in sorted({row.release_id for row in rows}):
        indices=[i for i,row in enumerate(rows) if row.release_id==release_id]
        families=containment_groups([geoms[i] for i in indices])
        for root, members in families.items():
            if len(members)<2:continue
            outer=indices[root];inner=[indices[i] for i in members if i!=root]
            involved=[rows[i] for i in [outer,*inner]]
            if any(row.zone_id is not None or row.assignee_id is not None or row.progress
                   or row.task_state!='unvisited' for row in involved):
                raise ops.OperationError('被包围网格已有片区或作业归属，不能自动归并')
            ids=[row.id for row in involved]
            if db.query(BdGridTask.id).filter(BdGridTask.grid_id.in_(ids)).first():
                raise ops.OperationError('网格已有巡店批次任务引用，不能自动归并历史范围')
            if db.query(BdStoreVisitLog.id).filter(BdStoreVisitLog.grid_id.in_(ids)).first():
                raise ops.OperationError('被包围网格已有巡店记录，不能自动归并')
            geom=union_all([geoms[i] for i in [outer,*inner]],grid_size=.001)
            if (not geom.is_valid or outline(geom).geom_type!='Polygon'
                    or abs(geom.area-sum(geoms[i].area for i in [outer,*inner]))>.01):
                raise ops.OperationError('包含网格并集无效或存在重叠')
            planned.append((outer,inner,geom))
    report={'groups':[], 'removed_grid_count':sum(len(inner) for _,inner,_ in planned)}
    for outer,inner,geom in planned:
        row=rows[outer];removed=[rows[i].id for i in inner]
        count=db.query(func.count(BdGridStore.id)).filter(BdGridStore.grid_id.in_([row.id,*removed])).scalar()
        entry={'outer_id':row.id,'absorbed_ids':removed,'area_km2':geom.area/1e6,
               'store_count':count,'geometry_sha256':hashlib.sha256(geom.normalize().wkb).hexdigest()}
        report['groups'].append(entry)
        if apply:
            before={'source_id':row.source_id,'area_km2':row.area_km2,'store_count':row.store_count,
                    'absorbed':[{'id':rows[i].id,'source_id':rows[i].source_id} for i in inner]}
            db.query(BdGridStore).filter(BdGridStore.grid_id.in_(removed)).update(
                {BdGridStore.grid_id:row.id},synchronize_session=False)
            row.geometry=z.geographic(geom);row.area_km2=geom.area/1e6
            row.store_count=count;row.revision+=1;row.updated_at=datetime.utcnow()
            for i in inner:db.delete(rows[i])
            ops.audit_log(db,user,'grid','absorb_enclosed',row,before,entry)
    return report


def absorb_zone_fragments(db, user, region_code, *, max_area_km2=0.36, apply=False, target='longest_edge'):
    """把片区内不足阈值的网格并进共边邻格。未归属碎片只进入贴着的片区，不跨片区。

    target='longest_edge' 时优先共边最长、其次邻格更大。
    target='smallest_neighbor' 时并进当前面积最小的合法邻格。
    """
    import hashlib
    from datetime import datetime
    from sqlalchemy import func
    from shapely import STRtree, union_all
    from app.models import BdWorkGrid, BdGridStore, BdStoreVisitLog, BdZoneRelease, BdGridTask, BdZone
    from app.services import bd_zones as z, bd_zone_operations as ops
    from app.services.bd_regions import release_ids
    ops.require_admin(user)
    if max_area_km2 <= 0 or max_area_km2 > 2:
        raise ops.OperationError('碎片面积阈值无效')
    if target not in ('longest_edge', 'smallest_neighbor'):
        raise ops.OperationError('邻格选择方式无效')
    ids = [row.id for row in release_ids(db, region_code)]
    if apply:
        for identity in sorted(ids):
            ops.locked(db, BdZoneRelease, identity)
    query = db.query(BdWorkGrid).filter(BdWorkGrid.release_id.in_(ids)).order_by(BdWorkGrid.id)
    if apply:
        query = query.populate_existing().with_for_update()
    rows = query.all()
    if not rows:
        return {'region': region_code, 'max_area_km2': max_area_km2, 'removed_grid_count': 0,
                'groups': [], 'remaining_small_ids': [], 'zones_updated': []}
    geoms = {row.id: z.projected(row.geometry) for row in rows}
    items = {row.id: {'geom': geoms[row.id], 'zone_id': row.zone_id, 'release_id': row.release_id,
                      'absorbed': []} for row in rows}
    order = [row.id for row in rows]
    tree = STRtree([geoms[identity] for identity in order])
    neighbors = {identity: set() for identity in order}
    for index, identity in enumerate(order):
        for raw in tree.query(geoms[identity], predicate='intersects'):
            other = order[int(raw)]
            if other <= identity:
                continue
            if geoms[identity].boundary.intersection(geoms[other].boundary).length > .2:
                neighbors[identity].add(other)
                neighbors[other].add(identity)
    limit = max_area_km2 * 1e6
    while True:
        pending = [identity for identity, item in items.items() if item['geom'].area < limit]
        pending.sort(key=lambda identity: (items[identity]['geom'].area, identity))
        merged = False
        for identity in pending:
            if identity not in items or items[identity]['geom'].area >= limit:
                continue
            choices = []
            for other in sorted(neighbors.get(identity, ())):
                if other not in items:
                    continue
                source_zone, target_zone = items[identity]['zone_id'], items[other]['zone_id']
                if items[identity]['release_id'] != items[other]['release_id']:
                    continue
                if source_zone is not None and target_zone != source_zone:
                    continue
                geom = union_all([items[identity]['geom'], items[other]['geom']], grid_size=.001)
                if (geom.geom_type != 'Polygon' or not geom.is_valid
                        or abs(geom.area - items[identity]['geom'].area - items[other]['geom'].area) > .01):
                    continue
                shared = items[identity]['geom'].boundary.intersection(items[other]['geom'].boundary).length
                if shared <= .2:
                    continue
                if target == 'smallest_neighbor':
                    choices.append((items[other]['geom'].area, other, geom))
                else:
                    choices.append((-shared, -items[other]['geom'].area, other, geom))
            if not choices:
                continue
            other, geom = min(choices)[-2:]
            items[other]['geom'] = geom
            items[other]['absorbed'].extend([identity, *items[identity]['absorbed']])
            if items[other]['zone_id'] is None:
                items[other]['zone_id'] = items[identity]['zone_id']
            adjacent = (neighbors[identity] | neighbors[other]) - {identity, other}
            del items[identity]
            for key in adjacent:
                neighbors[key].discard(identity)
                neighbors[key].discard(other)
                neighbors[key].add(other)
            neighbors[other] = adjacent
            del neighbors[identity]
            merged = True
            break
        if not merged:
            break
    by_id = {row.id: row for row in rows}
    groups = [(identity, item) for identity, item in items.items() if item['absorbed']]
    absorbed_ids = [absorbed for _, item in groups for absorbed in item['absorbed']]
    if absorbed_ids:
        blocked = [row for row in rows if row.id in set(absorbed_ids) and (
            row.assignee_id is not None or row.progress or row.task_state != 'unvisited')]
        if blocked or db.query(BdGridTask.id).filter(BdGridTask.grid_id.in_(absorbed_ids)).first():
            raise ops.OperationError('碎片网格已有派发或巡店批次，不能并入邻格')
        if db.query(BdStoreVisitLog.id).filter(BdStoreVisitLog.grid_id.in_(absorbed_ids)).first():
            raise ops.OperationError('碎片网格已有巡店记录，不能并入邻格')
    zone_ids = sorted({items[identity]['zone_id'] for identity, _ in groups if items[identity]['zone_id'] is not None})
    zones = {zone.id: zone for zone in db.query(BdZone).filter(BdZone.id.in_(zone_ids)).all()} if zone_ids else {}
    if any(zone.state == 'archived' or zone.team_id is not None or zone.leader_id is not None for zone in zones.values()):
        raise ops.OperationError('片区已派发或已归档，不能并入碎片网格')
    if apply and zone_ids:
        for identity in zone_ids:
            zones[identity] = ops.locked(db, BdZone, identity)
    report = {'region': region_code, 'max_area_km2': max_area_km2, 'groups': [],
              'removed_grid_count': len(absorbed_ids),
              'remaining_small_ids': sorted(identity for identity, item in items.items() if item['geom'].area < limit)}
    now = datetime.utcnow()
    for identity, item in sorted(groups, key=lambda pair: pair[0]):
        row = by_id[identity]
        removed = sorted(item['absorbed'])
        count = db.query(func.count(BdGridStore.id)).filter(BdGridStore.grid_id.in_([identity, *removed])).scalar()
        entry = {'target_id': identity, 'absorbed_ids': removed, 'zone_id': item['zone_id'],
                 'area_km2': item['geom'].area / 1e6, 'store_count': count,
                 'geometry_sha256': hashlib.sha256(item['geom'].normalize().wkb).hexdigest()}
        report['groups'].append(entry)
        if not apply:
            continue
        before = {'source_id': row.source_id, 'zone_id': row.zone_id, 'area_km2': row.area_km2,
                  'store_count': row.store_count,
                  'absorbed': [{'id': absorbed, 'source_id': by_id[absorbed].source_id} for absorbed in removed]}
        db.query(BdGridStore).filter(BdGridStore.grid_id.in_(removed)).update(
            {BdGridStore.grid_id: identity}, synchronize_session=False)
        db.flush()
        row.geometry = z.geographic(item['geom'])
        row.area_km2 = item['geom'].area / 1e6
        row.store_count = count
        row.zone_id = item['zone_id']
        row.revision += 1
        row.updated_at = now
        for absorbed in removed:
            db.delete(by_id[absorbed])
        ops.audit_log(db, user, 'grid', 'absorb_fragment', row, before, entry)
    report['zones_updated'] = []
    if apply:
        db.flush()
        for zone_id in zone_ids:
            zone = zones[zone_id]
            members = [item['geom'] for identity, item in items.items() if item['zone_id'] == zone_id]
            geom = union_all(members, grid_size=.001)
            old_geom = z.projected(zone.geometry)
            old = {'area_km2': zone.area_km2, 'store_count': zone.store_count, 'revision': zone.revision}
            member_ids = [identity for identity, item in items.items() if item['zone_id'] == zone_id]
            zone.store_count = db.query(func.count(BdGridStore.id)).filter(BdGridStore.grid_id.in_(member_ids)).scalar()
            if not coverage_matches(old_geom, geom) or abs(old_geom.area - geom.area) > .01:
                zone.geometry = z.geographic(geom)
                zone.area_km2 = geom.area / 1e6
                zone.workload = None
            zone.revision += 1
            zone.updated_at = now
            ops.audit_log(db, user, 'zone', 'absorb_fragment', zone, old,
                          {'area_km2': zone.area_km2, 'store_count': zone.store_count, 'grid_ids': member_ids})
            report['zones_updated'].append(zone_id)
    return report
