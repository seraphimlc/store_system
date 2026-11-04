"""按当前商户数将既有不可切分任务网格组合为连通片区。"""
import hashlib
import json
import math
from datetime import datetime

from shapely import union_all

from app.models import BdRegion, BdStoreVisitLog, BdWorkGrid
from app.services import bd_grid_density, bd_zone_operations as ops, bd_zones as z
from app.services.bd_regions import release_ids


def _components(graph):
    unseen = set(graph)
    result = []
    while unseen:
        pending, component = [min(unseen)], set()
        while pending:
            current = pending.pop()
            if current in unseen:
                unseen.remove(current)
                component.add(current)
                pending.extend(graph[current] & unseen)
        result.append(component)
    return result


def plan(db, user, *, region='all', target_stores=8000, max_area_km2=25.,
         target_basis='stores', target_per_zone=None, min_stores=5,
         tolerance_pct=15, max_grids_per_zone=None, max_stores_per_zone=None,
         apply=False, source_engine=None, expected_signature=None, source_table=None):
    """预览或提交一次重分区；apply=True 时当前商户重绑和新片区同事务提交。"""
    ops.require_admin(user)
    if (isinstance(target_stores, bool) or not isinstance(target_stores, int)
            or not 1 <= target_stores <= 100_000
            or isinstance(max_area_km2,bool) or not isinstance(max_area_km2,(int,float))
            or not math.isfinite(max_area_km2) or not 2 <= max_area_km2 <= 500):
        raise ops.OperationError('目标商户数须为1～100000，片区面积上限须为2～500 km²')
    if target_basis not in ('stores', 'grids'):
        raise ops.OperationError('片区目标类型无效')
    if target_per_zone is None:
        target_per_zone = target_stores if target_basis == 'stores' else 8
    if (isinstance(target_per_zone, bool) or not isinstance(target_per_zone, int)
            or not 1 <= target_per_zone <= 100_000):
        raise ops.OperationError('片区目标值须为1～100000')
    if min_stores is None:
        min_stores = 5
    if isinstance(min_stores, bool) or not isinstance(min_stores, int) or not 0 <= min_stores <= 100_000:
        raise ops.OperationError('低密度网格阈值须为0～100000')
    if tolerance_pct is None:
        tolerance_pct = 15
    if isinstance(tolerance_pct, bool) or not isinstance(tolerance_pct, int) or not 0 <= tolerance_pct <= 50:
        raise ops.OperationError('目标容差须为0～50%')
    for value, label in ((max_grids_per_zone,'网格上限'),(max_stores_per_zone,'商户上限')):
        if value is not None and (isinstance(value,bool) or not isinstance(value,int) or value < 1):
            raise ops.OperationError(f'{label}须为正整数')

    density = bd_grid_density.refresh(db, user, region=region, apply=apply,
                                     source_engine=source_engine, min_stores=min_stores,
                                     source_table=source_table)
    releases = db.query(BdWorkGrid.release_id).filter(
        BdWorkGrid.release_id.in_(release_ids(db, region))).distinct().all()
    release_ids_found = sorted(row[0] for row in releases)
    if not release_ids_found:
        raise ops.OperationError('没有可组合的正式网格')
    counts = density['grid_counts']
    plan_rows = []
    unassigned_grid_ids = []
    out_of_tolerance = False
    max_area = max_area_km2 * 1_000_000
    for release_id in release_ids_found:
        grids = db.query(BdWorkGrid).filter_by(release_id=release_id).order_by(BdWorkGrid.id).all()
        eligible = [g for g in grids if int(counts[str(g.id)]) >= min_stores]
        if not eligible:
            continue
        geoms = [z.projected(g.geometry) for g in eligible]
        store_counts = [int(counts[str(g.id)]) for g in eligible]
        graph = z.neighbours(geoms)
        for component in _components(graph):
            indexes = sorted(component)
            part_geoms = [geoms[i] for i in indexes]
            part_counts = [store_counts[i] for i in indexes]
            total_target_units = sum(part_counts) if target_basis == 'stores' else len(indexes)
            lower = target_per_zone * (1 - tolerance_pct / 100)
            upper = target_per_zone * (1 + tolerance_pct / 100)
            minimum_zones = max(1, math.ceil(total_target_units / upper))
            maximum_zones = max(minimum_zones, math.floor(total_target_units / max(lower, 1)))
            target_zones = round(total_target_units / target_per_zone)
            target_zones = min(max(target_zones, minimum_zones), maximum_zones)
            if max_grids_per_zone:
                target_zones = max(target_zones, math.ceil(len(indexes) / max_grids_per_zone))
            if max_stores_per_zone:
                target_zones = max(target_zones, math.ceil(sum(part_counts) / max_stores_per_zone))
            requested = max(1, target_zones, math.ceil(sum(g.area for g in part_geoms) / max_area))
            groups = z._group(part_geoms, part_counts, requested, max_area,
                              max_stores=max_stores_per_zone,max_members=max_grids_per_zone)
            groups = z._rebalance(groups, part_geoms, part_counts, max_area,
                                  max_stores=max_stores_per_zone,max_members=max_grids_per_zone)
            for group in groups:
                members = [eligible[indexes[i]] for i in sorted(group['members'])]
                group_stores = sum(store_counts[indexes[i]] for i in group['members'])
                if ((max_grids_per_zone is not None and len(members) > max_grids_per_zone)
                        or (max_stores_per_zone is not None and group_stores > max_stores_per_zone)):
                    unassigned_grid_ids.extend(g.id for g in members)
                    continue
                group_target = len(members) if target_basis == 'grids' else group_stores
                if not target_per_zone * (1-tolerance_pct/100) <= group_target <= target_per_zone * (1+tolerance_pct/100):
                    out_of_tolerance = True
                geometry = union_all([z.projected(g.geometry) for g in members], grid_size=.001)
                if geometry.area / 1_000_000 > max_area_km2 + 1e-6:
                    unassigned_grid_ids.extend(g.id for g in members)
                    continue
                if geometry.geom_type != 'Polygon' or not geometry.is_valid:
                    raise ops.OperationError('片区几何不是有效的连续单面，已停止生成')
                member_keys = sorted(g.source_id for g in members)
                source_id = 'density-' + hashlib.sha256(
                    '|'.join(member_keys).encode()).hexdigest()[:32]
                plan_rows.append({'release_id': release_id, 'grid_ids': [g.id for g in members],
                                  'source_id': source_id, 'geometry': z.geographic(geometry),
                                  'area_km2': geometry.area / 1_000_000,
                                  'store_count': group_stores})

    if not plan_rows:
        if apply:
            raise ops.OperationError('硬上限下没有可生成的片区；请调整配置后重新预览')
        return {'applied':False,'region':region,'zone_count':0,'grids_assigned':0,
                'zones':[],'density':density,'unassigned_grid_ids':sorted(unassigned_grid_ids),
                'out_of_tolerance':False,'signature':None}
    releases_to_change = sorted({row['release_id'] for row in plan_rows})
    existing_zones = db.query(BdRegion).filter(BdRegion.release_id.in_(releases_to_change)).order_by(
        BdRegion.id).all()
    grids_to_change = db.query(BdWorkGrid).filter(
        BdWorkGrid.release_id.in_(releases_to_change)).order_by(BdWorkGrid.id).all()
    busy = (any(zone.team_id is not None or zone.leader_id is not None for zone in existing_zones)
            or any(grid.assignee_id is not None or grid.progress or grid.task_state != 'unvisited'
                   for grid in grids_to_change)
            or db.query(BdStoreVisitLog.id).filter(
                BdStoreVisitLog.grid_id.in_([g.id for g in grids_to_change])).first() is not None)
    if apply and busy:
        raise ops.OperationError('现有片区或网格已经派发/开展作业，拒绝覆盖；本次事务未提交')

    signature_data = {'density_snapshot': density['snapshot_id'], 'region': region,
                      'target_basis': target_basis, 'target_per_zone': target_per_zone,
                      'min_stores': min_stores, 'max_area_km2': max_area_km2,
                      'tolerance_pct': tolerance_pct, 'max_grids_per_zone': max_grids_per_zone,
                      'max_stores_per_zone': max_stores_per_zone,
                      'zones': [{'release_id': row['release_id'], 'grid_ids': row['grid_ids'],
                                 'source_id': row['source_id'], 'store_count': row['store_count'],
                                 'area_km2': round(row['area_km2'], 6)} for row in plan_rows]}
    signature_data['unassigned_grid_ids'] = sorted(unassigned_grid_ids)
    signature = hashlib.sha256(json.dumps(signature_data, sort_keys=True).encode()).hexdigest()
    if apply and (not isinstance(expected_signature,str) or len(expected_signature)!=64):
        raise ops.OperationError('请先预览自动分区，再提交应用')
    if expected_signature and signature != expected_signature:
        raise ops.OperationError('商户或网格数据已变化，预览已失效，请重新预览')
    report = {'applied': apply, 'region': region, 'target_basis': target_basis,
              'target_per_zone': target_per_zone,
              'max_area_km2': max_area_km2, 'density': density,
              'min_stores': min_stores,'tolerance_pct':tolerance_pct,
              'max_grids_per_zone':max_grids_per_zone,'max_stores_per_zone':max_stores_per_zone,
              'signature': signature,'unassigned_grid_ids':sorted(unassigned_grid_ids),
              'out_of_tolerance':out_of_tolerance,
              'zone_count': len(plan_rows),
              'grids_assigned': sum(len(row['grid_ids']) for row in plan_rows),
              'zones': [{'release_id': row['release_id'], 'grid_ids': row['grid_ids'],
                         'grid_count': len(row['grid_ids']), 'geometry': row['geometry'],
                         'area_km2': round(row['area_km2'], 4),
                         'store_count': row['store_count']} for row in plan_rows]}
    if not apply:
        return report

    now = datetime.utcnow()
    desired_sources = {(row['release_id'], row['source_id']) for row in plan_rows}
    existing_by_source = {(zone.release_id, zone.source_id): zone for zone in existing_zones}
    for zone in existing_zones:
        if (zone.release_id, zone.source_id) in desired_sources:
            continue
        if zone.state != 'archived' or zone.store_count != 0 or zone.area_km2 != 0:
            zone.state = 'archived'
            zone.team_id = None
            zone.leader_id = None
            zone.store_count = 0
            zone.area_km2 = 0
            zone.workload = None
            zone.revision += 1
            zone.updated_at = now
    db.flush()
    by_id = {grid.id: grid for grid in grids_to_change}
    assigned_grid_ids = {grid_id for row in plan_rows for grid_id in row['grid_ids']}
    for number, row in enumerate(plan_rows, 1):
        key = (row['release_id'], row['source_id'])
        zone = existing_by_source.get(key)
        name = f"商户密度片区-{number:04d}"
        same_geometry = zone is not None and z.projected(zone.geometry).equals(
            z.projected(row['geometry']))
        changed = zone is None or zone.state != 'ready' or zone.name != name or \
            not same_geometry or zone.area_km2 != row['area_km2'] or \
            zone.store_count != row['store_count'] or zone.workload is not None or \
            zone.merge_boundaries is not True
        if zone is None:
            zone = BdRegion(release_id=row['release_id'], source_id=row['source_id'],
                            name=name, geometry=row['geometry'], area_km2=row['area_km2'],
                            store_count=row['store_count'], workload=None, review=[],
                            state='ready', merge_boundaries=True)
            db.add(zone)
        elif changed:
            zone.name = name
            zone.geometry = row['geometry']
            zone.area_km2 = row['area_km2']
            zone.store_count = row['store_count']
            zone.workload = None
            zone.review = []
            zone.state = 'ready'
            zone.merge_boundaries = True
            zone.revision += 1
            zone.updated_at = now
        db.flush()
        for grid_id in row['grid_ids']:
            grid = by_id[grid_id]
            if grid.zone_id != zone.id:
                grid.zone_id = zone.id
                grid.revision += 1
                grid.updated_at = now
    for grid in grids_to_change:
        if grid.state != 'disabled' and grid.id not in assigned_grid_ids and grid.zone_id is not None:
            grid.zone_id = None
            grid.revision += 1
            grid.updated_at = now
    ops.audit_log(db, user, 'zone', 'regroup_by_merchant_density',
                  existing_zones[0] if existing_zones else zone, {}, report)
    db.flush()
    return report
