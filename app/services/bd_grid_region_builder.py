"""按当前商户数将既有不可切分任务网格组合为连通片区。"""
import hashlib
import json
import math
from datetime import datetime

from shapely import STRtree, union_all

from app.models import BdRegion, BdStoreVisitLog, BdWorkGrid
from app.services import bd_grid_density, bd_grid_shapes, bd_zone_operations as ops, bd_zones as z
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


def _merge_geometry_groups(groups, max_area, *, max_stores=None, max_members=None):
    """Recover disconnected groups using whole shared-edge neighbors before exclusion."""
    active = {i: {**group, 'members': set(group['members'])}
              for i, group in enumerate(groups)}
    while len(active) > 1:
        keys = sorted(active)
        tree = STRtree([active[key]['geom'] for key in keys])
        did_merge = False
        for key in sorted(keys, key=lambda k: min(active[k]['members'])):
            source = active[key]
            if source['geom'].geom_type == 'Polygon' or not source['geom'].is_valid:
                continue
            selected = {key}
            geom, members, count = source['geom'], set(source['members']), source['count']
            while geom.geom_type != 'Polygon':
                options = []
                for raw in tree.query(geom):
                    other_key = keys[int(raw)]
                    if other_key in selected:
                        continue
                    other = active[other_key]
                    shared = geom.boundary.intersection(other['geom'].boundary).length
                    if shared <= .2:
                        continue
                    combined_members = members | other['members']
                    combined_count = count + other['count']
                    if (geom.area + other['geom'].area > max_area + 1
                            or max_stores is not None and combined_count > max_stores
                            or max_members is not None and len(combined_members) > max_members):
                        continue
                    combined = union_all([geom, other['geom']], grid_size=.001)
                    if not combined.is_valid or combined.geom_type not in ('Polygon', 'MultiPolygon'):
                        continue
                    parts = 1 if combined.geom_type == 'Polygon' else len(combined.geoms)
                    # Prefer closing the gap, then the smallest supporting territory.
                    score = (parts, combined.area, -shared, min(other['members']))
                    options.append((score, other_key, combined, combined_members, combined_count))
                if not options:
                    break
                _, other_key, geom, members, count = min(options, key=lambda item: item[0])
                selected.add(other_key)
            if geom.geom_type == 'Polygon' and geom.is_valid:
                active[key] = {'geom': geom, 'members': members, 'count': count}
                for other_key in selected - {key}:
                    del active[other_key]
                did_merge = True
                break
        if not did_merge:
            break
    return list(active.values())


def _merge_small_groups(groups, threshold, max_area, *, target_basis, target_per_zone,
                        max_stores=None, max_members=None, shape_guard=None):
    """Merge whole low-count territories only across real shared edges."""
    active={i:{**g,'members':set(g['members'])} for i,g in enumerate(groups)}
    before=sum(g['count']<threshold for g in active.values())
    merged_count=0
    reasons={}
    while threshold and len(active)>1:
        keys=sorted(active)
        tree=STRtree([active[k]['geom'] for k in keys])
        did_merge=False
        reasons={}
        for i in sorted(keys,key=lambda k:(active[k]['count'],min(active[k]['members']))):
            source=active[i]
            if source['count']>=threshold:continue
            geom=source['geom']
            if geom.geom_type!='Polygon' or not geom.is_valid:
                reasons[i]='invalid_geometry'
                continue
            neighbors=[]
            for raw in tree.query(geom):
                j=keys[int(raw)]
                if i==j:continue
                other=active[j]
                shared=geom.boundary.intersection(other['geom'].boundary).length
                if shared>.2:neighbors.append((j,shared))
            if not neighbors:
                reasons[i]='no_shared_edge'
                continue
            options=[]
            failures=set()
            for j,shared in sorted(neighbors):
                other=active[j]
                members=source['members']|other['members']
                count=source['count']+other['count']
                if (geom.area+other['geom'].area>max_area+1
                        or max_stores is not None and count>max_stores
                        or max_members is not None and len(members)>max_members):
                    failures.add('hard_limit')
                    continue
                combined=union_all([geom,other['geom']],grid_size=.001)
                if (combined.geom_type!='Polygon' or not combined.is_valid
                        or shape_guard is not None and not shape_guard(combined)):
                    failures.add('invalid_geometry')
                    continue
                units=count if target_basis=='stores' else len(members)
                compactness=z._outer_perimeter(combined)**2/(4*math.pi*combined.area)
                score=(compactness,abs(units-target_per_zone)/target_per_zone,-shared,min(other['members']))
                options.append((score,j,combined,members,count))
            if not options:
                reasons[i]='hard_limit' if 'hard_limit' in failures else 'invalid_geometry'
                continue
            _,j,combined,members,count=min(options,key=lambda item:item[0])
            active[j]={'geom':combined,'members':members,'count':count}
            del active[i]
            merged_count+=1
            did_merge=True
            break  # Rebuild spatial candidates after every merge; retry previously blocked tails.
        if not did_merge:break
    remaining=[]
    for i,g in sorted(active.items(),key=lambda item:min(item[1]['members'])):
        if g['count']<threshold:
            remaining.append({'members':sorted(g['members']),'store_count':g['count'],
                              'reason':reasons.get(i,'no_shared_edge')})
    return list(active.values()),{'before':before,'merged':merged_count,'remaining':remaining}


def plan(db, user, *, region='all', target_stores=8000, max_area_km2=25.,
         target_basis='stores', target_per_zone=None, min_stores=5,
         tolerance_pct=15, max_grids_per_zone=None, max_stores_per_zone=None,
         small_zone_threshold=None,
         apply=False, source_engine=None, expected_signature=None, source_table=None):
    """仅对未划分网格预览/追加片区；已有片区及所属网格不变。"""
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
    if small_zone_threshold is None:
        small_zone_threshold=min_stores*3 if min_stores else 15
    if (isinstance(small_zone_threshold,bool) or not isinstance(small_zone_threshold,int)
            or not 0<=small_zone_threshold<=300_000):
        raise ops.OperationError('小片区归并阈值须为0～300000')
    if tolerance_pct is None:
        tolerance_pct = 15
    if isinstance(tolerance_pct, bool) or not isinstance(tolerance_pct, int) or not 0 <= tolerance_pct <= 50:
        raise ops.OperationError('目标容差须为0～50%')
    for value, label in ((max_grids_per_zone,'网格上限'),(max_stores_per_zone,'商户上限')):
        if value is not None and (isinstance(value,bool) or not isinstance(value,int) or value < 1):
            raise ops.OperationError(f'{label}须为正整数')

    density = bd_grid_density.refresh(db, user, region=region, apply=apply,
                                     source_engine=source_engine, min_stores=min_stores,
                                     source_table=source_table,unassigned_only=True)
    releases = db.query(BdWorkGrid.release_id).filter(
        BdWorkGrid.release_id.in_(release_ids(db, region))).distinct().all()
    release_ids_found = sorted(row[0] for row in releases)
    if not release_ids_found:
        raise ops.OperationError('没有可组合的正式网格')
    counts = density['grid_counts']
    plan_rows = []
    unassigned_grid_ids = []
    geometry_unassigned = []
    shape_unassigned = []
    small_zone_merge={'threshold':small_zone_threshold,'before':0,'merged':0,'remaining':[]}
    out_of_tolerance = False
    max_area = max_area_km2 * 1_000_000
    for release_id in release_ids_found:
        grids = db.query(BdWorkGrid).filter_by(release_id=release_id).order_by(BdWorkGrid.id).all()
        # 零商户只表示当前商户源没有覆盖，仍需作为连通范围参与片区组合；
        # min_stores 只影响目标计数，不再把这些完整网格从拓扑图移除。
        eligible = [g for g in grids if g.zone_id is None and g.state != 'disabled']
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
            groups = bd_grid_shapes.group(part_geoms, part_counts, requested, max_area,
                max_stores=max_stores_per_zone, max_members=max_grids_per_zone)
            groups,merge_report=_merge_small_groups(groups,small_zone_threshold,max_area,
                target_basis=target_basis,target_per_zone=target_per_zone,
                max_stores=max_stores_per_zone,max_members=max_grids_per_zone,
                shape_guard=bd_grid_shapes.acceptable)
            small_zone_merge['before']+=merge_report['before']
            small_zone_merge['merged']+=merge_report['merged']
            for item in merge_report['remaining']:
                small_zone_merge['remaining'].append({
                    'grid_ids':[eligible[indexes[i]].id for i in item['members']],
                    'store_count':item['store_count'],'reason':item['reason']})
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
                    unassigned_grid_ids.extend(g.id for g in members)
                    geometry_unassigned.append({'grid_ids': [g.id for g in members],
                                                'store_count': group_stores,
                                                'geometry_type': geometry.geom_type})
                    continue
                if not bd_grid_shapes.acceptable(geometry):
                    unassigned_grid_ids.extend(g.id for g in members)
                    shape_unassigned.append({'grid_ids': [g.id for g in members],
                                             'store_count': group_stores,
                                             **z._shape_metrics(geometry)})
                    continue
                member_keys = sorted(g.source_id for g in members)
                source_id = 'density-free-' + hashlib.sha256(
                    '|'.join(member_keys).encode()).hexdigest()[:32]
                plan_rows.append({'release_id': release_id, 'grid_ids': [g.id for g in members],
                                  'source_id': source_id, 'geometry': z.geographic(geometry),
                                  'area_km2': geometry.area / 1_000_000,
                                  'store_count': group_stores})

    if not plan_rows:
        if apply:
            raise ops.OperationError('没有可生成片区的未划分网格，请重新预览')
        return {'applied':False,'region':region,'zone_count':0,'grids_assigned':0,
                'zones':[],'density':density,'unassigned_grid_ids':sorted(unassigned_grid_ids),
                'geometry_unassigned':geometry_unassigned, 'shape_unassigned':shape_unassigned,
                'small_zone_merge':small_zone_merge,
                'out_of_tolerance':False,'signature':None}
    releases_to_change = sorted({row['release_id'] for row in plan_rows})
    grids_to_change = db.query(BdWorkGrid).filter(
        BdWorkGrid.release_id.in_(releases_to_change),BdWorkGrid.zone_id.is_(None)).order_by(BdWorkGrid.id).all()
    busy = (any(grid.assignee_id is not None or grid.progress or grid.task_state != 'unvisited'
                   for grid in grids_to_change)
            or db.query(BdStoreVisitLog.id).filter(
                BdStoreVisitLog.grid_id.in_([g.id for g in grids_to_change])).first() is not None)
    if apply and busy:
        raise ops.OperationError('现有片区或网格已经派发/开展作业，拒绝覆盖；本次事务未提交')

    signature_data = {'density_snapshot': density['snapshot_id'], 'region': region,
                      'scope':'unassigned_only','shape_policy':'compact-v1','preserved_grid_ids':density['preserved_grid_ids'],
                      'small_zone_threshold':small_zone_threshold,
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
              'geometry_unassigned':geometry_unassigned, 'shape_unassigned':shape_unassigned,
              'small_zone_merge':small_zone_merge,
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
    by_id = {grid.id: grid for grid in grids_to_change}
    for number, row in enumerate(plan_rows, 1):
        name = f"商户密度片区-{number:04d}"
        zone = BdRegion(release_id=row['release_id'], source_id=row['source_id'],
                        name=name, geometry=row['geometry'], area_km2=row['area_km2'],
                        store_count=row['store_count'], workload=None, review=[],
                        state='ready', merge_boundaries=True)
        db.add(zone)
        db.flush()
        for grid_id in row['grid_ids']:
            grid = by_id[grid_id]
            if grid.zone_id != zone.id:
                grid.zone_id = zone.id
                grid.revision += 1
                grid.updated_at = now
    ops.audit_log(db, user, 'zone', 'regroup_by_merchant_density',zone, {}, report)
    db.flush()
    return report
