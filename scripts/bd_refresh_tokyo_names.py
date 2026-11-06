#!/usr/bin/env python3
"""AI-review Tokyo 23-ward/26-city grids, then name regions from grid labels."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / '.env')

from shapely import STRtree
from shapely.geometry import Point, mapping, shape
from app.db import SessionLocal
from app.models import BdRegion, BdWorkGrid, BdZoneRelease, User
from app.services import bd_grid_address_names as address_names
from app.services import bd_grid_enrichment as enrichment
from app.services import bd_grid_metadata as metadata
from app.services import bd_zone_operations as ops
from app.services import bd_zones as zones
from scripts.bd_name_all_grids import write_json

TARGET_CODES = {'tokyo23', 'tokyo26'}


def target_rows(db):
    releases = db.query(BdZoneRelease).order_by(BdZoneRelease.id).all()
    codes = {r.id: (r.source or {}).get('region_code', 'tokyo23') for r in releases}
    release_ids = {rid for rid, code in codes.items() if code in TARGET_CODES}
    regions = db.query(BdRegion).filter(BdRegion.release_id.in_(release_ids)).order_by(BdRegion.id).all()
    grids = db.query(BdWorkGrid).filter(BdWorkGrid.release_id.in_(release_ids)).order_by(BdWorkGrid.id).all()
    return codes, release_ids, regions, grids


def region_fingerprint(regions):
    rows = [(r.id, r.release_id, r.name, r.geometry, r.state, r.team_id, r.leader_id, r.revision)
            for r in regions]
    return hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def membership_fingerprint(regions, grids):
    rows = [('r', r.id, r.release_id, r.geometry) for r in regions]
    rows += [('g', g.id, g.release_id, g.zone_id, g.geometry) for g in grids]
    return hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def rail_stations_in_grids(grids, station_rows):
    result = {g.id: set() for g in grids}
    if not grids or not station_rows:
        return {key: [] for key in result}
    points = [zones.projected(mapping(Point(row['lon'], row['lat']))) for row in station_rows]
    tree = STRtree(points)
    for grid in grids:
        geom = zones.projected(grid.geometry)
        for index in tree.query(geom, predicate='intersects'):
            result[grid.id].add(station_rows[int(index)]['name'])
    return {key: metadata.clean_terms(list(names)) for key, names in result.items()}


def address_evidence(db, grid_ids):
    result = {gid: {'cities': Counter(), 'towns': Counter(), 'localities': Counter(), 'buildings': Counter()}
              for gid in grid_ids}
    for row in address_names.address_rows(db):
        if row['grid_id'] not in result:
            continue
        parts = address_names.address_parts(row['address'], row['prefecture'], row['municipality'])
        evidence = result[row['grid_id']]
        if parts['city']:
            evidence['cities'][parts['city']] += 1
        if parts['town']:
            evidence['towns'][parts['town']] += 1
            evidence['localities'][parts['locality']] += 1
        evidence['buildings'].update(parts['buildings'])
    return result


def make_region_rows(regions, grids, labels):
    members = {region.id: [] for region in regions}
    for grid in grids:
        if grid.zone_id in members:
            members[grid.zone_id].append(grid.id)
    by_release = {}
    for grid in grids:
        by_release.setdefault(grid.release_id, []).append(grid)
    rows = []
    for region in regions:
        ids = members[region.id]
        if not ids:
            boundary = shape(region.geometry)
            ids = [g.id for g in by_release.get(region.release_id, [])
                   if boundary.intersects(shape(g.geometry))]
        votes = Counter(labels[gid] for gid in ids if labels.get(gid))
        candidates = [label for label, _ in sorted(votes.items(), key=lambda item: (-item[1], item[0]))]
        if not candidates:
            rows.append({'id': region.id, 'release_id': region.release_id, 'old_name': region.name,
                         'name': region.name, 'point_names': [], 'member_grid_ids': sorted(ids),
                         'unchanged_reason': 'no current grid intersects region'})
        else:
            rows.append({'id': region.id, 'release_id': region.release_id, 'old_name': region.name,
                         'candidates': candidates, 'member_grid_ids': sorted(ids)})
    for row in rows:
        if 'candidates' not in row:
            continue
        candidates = row.pop('candidates')
        chosen = candidates[:3]
        while chosen and len('&'.join(chosen)) > 128:
            chosen.pop()
        row['point_names'] = chosen
        row['name'] = '&'.join(chosen)
    return rows


def build_plan(db, pbf_path, rail_rows, batch_size, checkpoint_path):
    code_by_id, release_ids, regions, grids = target_rows(db)
    if not grids:
        raise ValueError('东京23区/26市当前没有网格')
    code_by_grid = {g.id: code_by_id[g.release_id] for g in grids}
    fingerprint = enrichment.fingerprint(regions, grids)
    geo = enrichment.geographic_metadata(grids, enrichment.pbf_features(pbf_path))
    rail = rail_stations_in_grids(grids, rail_rows)
    stations = {g.id: metadata.clean_terms([*geo[g.id]['stations'], *rail[g.id]]) for g in grids}
    refs = [SimpleNamespace(id=g.id, geometry=g.geometry, stations=stations[g.id]) for g in grids]
    nearby = address_names.station_context(refs, rail_rows)
    addresses = address_evidence(db, {g.id for g in grids})
    centers = {}
    for grid in grids:
        point = shape(grid.geometry).representative_point()
        centers.setdefault(grid.release_id, []).append((point.x, point.y))
    centers = {rid: (sum(x for x, _ in pts) / len(pts), sum(y for _, y in pts) / len(pts))
               for rid, pts in centers.items()}

    payloads = []
    for grid in grids:
        evidence = addresses[grid.id]
        candidates = ([name for name, _ in evidence['towns'].most_common(4)]
                      or [name for name, _ in evidence['cities'].most_common(3)]
                      or geo[grid.id]['localities'][:3] or geo[grid.id]['keywords'][:3]
                      or stations[grid.id][:3])
        if not candidates:
            point = shape(grid.geometry).representative_point()
            area = address_names.AREA_NAMES[code_by_grid[grid.id]]
            candidates = [area + '・' + address_names.direction((point.x, point.y), centers[grid.release_id])]
        payloads.append({'id': grid.id, 'current_name': grid.name,
                         'region': address_names.AREA_NAMES[code_by_grid[grid.id]],
                         'centroid': list(shape(grid.geometry).representative_point().coords)[0],
                         'stations': stations[grid.id], 'landmarks': geo[grid.id]['keywords'],
                         'localities': geo[grid.id]['localities'], 'address_candidates': candidates[:5]})

    checkpoint = json.loads(checkpoint_path.read_text()) if checkpoint_path.exists() else {}
    responses = checkpoint.get('responses', {}) if checkpoint.get('fingerprint') == fingerprint else {}
    names = {}
    chunks = [payloads[i:i + batch_size] for i in range(0, len(payloads), batch_size)]
    for index, chunk in enumerate(chunks, 1):
        digest = hashlib.sha256(json.dumps(chunk, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        choices = responses.get(digest)
        if choices is None:
            response = enrichment.generate_names({'grids': chunk}, include_region=False)
            choices = enrichment.validate_names(response, [row['id'] for row in chunk], include_region=False)
            responses[digest] = choices
            write_json(checkpoint_path, {'fingerprint': fingerprint, 'responses': responses})
        names.update(choices)
        print(f'AI reviewed {len(names)}/{len(payloads)} grids (batch {index}/{len(chunks)})', flush=True)

    grid_rows = []
    for grid in grids:
        label = re.sub(r'・G\d+$', '', names[grid.id]).strip()
        name = address_names.compose_name(label, 'G', grid.id)
        evidence = addresses[grid.id]
        code = code_by_grid[grid.id]
        nearby_terms = [f"{row['name']}駅付近（境界から約{row['distance_m']}m）" for row in nearby[grid.id]]
        keywords = metadata.clean_terms([
            address_names.AREA_NAMES[code], address_names.AREA_ALIASES[code],
            *evidence['cities'].keys(), *evidence['towns'].keys(), *evidence['localities'].keys(),
            *evidence['buildings'].keys(), *geo[grid.id]['keywords'], *geo[grid.id]['localities'],
            *stations[grid.id], *nearby_terms, label])
        grid_rows.append({'id': grid.id, 'release_id': grid.release_id, 'old_name': grid.name,
                          'name': name, 'label': label, 'old_stations': list(grid.stations or []),
                          'stations': stations[grid.id], 'old_keywords': list(grid.keywords or []),
                          'keywords': keywords})
    region_rows = make_region_rows(regions, grids, {row['id']: row['label'] for row in grid_rows})
    return {'version': 1, 'region_codes': sorted(TARGET_CODES), 'release_ids': sorted(release_ids),
            'fingerprint': fingerprint, 'region_fingerprint': region_fingerprint(regions),
            'membership_fingerprint': membership_fingerprint(regions, grids),
            'grids': grid_rows, 'regions': region_rows,
            'summary': {'grids': len(grid_rows),
                        'grid_names_changed': sum(r['old_name'] != r['name'] for r in grid_rows),
                        'grid_stations_changed': sum(r['old_stations'] != r['stations'] for r in grid_rows),
                        'grid_keywords_changed': sum(set(r['old_keywords']) != set(r['keywords']) for r in grid_rows),
                        'keywords_added': sum(len(set(r['keywords']) - set(r['old_keywords'])) for r in grid_rows),
                        'keywords_removed': sum(len(set(r['old_keywords']) - set(r['keywords'])) for r in grid_rows),
                        'regions': len(region_rows),
                        'region_names_changed': sum(r['old_name'] != r['name'] for r in region_rows),
                        'regions_without_grid_basis': sum(bool(r.get('unchanged_reason')) for r in region_rows),
                        'max_point_names': max((len(r['point_names']) for r in region_rows), default=0)}}


def apply_plan(plan, backup):
    release_ids = plan['release_ids']
    grid_values = {row['id']: row for row in plan['grids']}
    region_values = {row['id']: row for row in plan['regions'] if not row.get('unchanged_reason')}
    with SessionLocal.begin() as db:
        releases = db.query(BdZoneRelease).filter(BdZoneRelease.id.in_(release_ids)) \
            .order_by(BdZoneRelease.id).populate_existing().with_for_update().all()
        codes = {(row.source or {}).get('region_code', 'tokyo23') for row in releases}
        if codes != TARGET_CODES or len(releases) != len(release_ids):
            raise ops.OperationError('目标地区与预览不匹配')
        regions = db.query(BdRegion).filter(BdRegion.release_id.in_(release_ids)) \
            .order_by(BdRegion.id).populate_existing().with_for_update().all()
        grids = db.query(BdWorkGrid).filter(BdWorkGrid.release_id.in_(release_ids)) \
            .order_by(BdWorkGrid.id).populate_existing().with_for_update().all()
        if enrichment.fingerprint(regions, grids) != plan['fingerprint']:
            raise ops.OperationError('目标网格在预览后变化，请重新生成计划')
        if {g.id for g in grids} != set(grid_values) or {r.id for r in regions} != {x['id'] for x in plan['regions']}:
            raise ops.OperationError('预览未完整覆盖目标网格和片区')
        user = db.query(User).filter_by(username='admin').one()
        ops.require_admin(user)
        # Both stages share this transaction: any failure rolls back all grid and region updates.
        for grid in grids:
            row = grid_values[grid.id]
            after = {'name': row['name'], 'stations': metadata.clean_terms(row['stations']),
                     'keywords': metadata.clean_terms(row['keywords'])}
            before = {'name': grid.name, 'stations': list(grid.stations or []),
                      'keywords': list(grid.keywords or [])}
            if before == after:
                continue
            grid.name, grid.stations, grid.keywords = after['name'], after['stations'], after['keywords']
            grid.revision += 1
            grid.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            ops.audit_log(db, user, 'grid', 'rename', grid, before, after)
        db.flush()
        if region_fingerprint(regions) != plan['region_fingerprint'] or \
                membership_fingerprint(regions, grids) != plan['membership_fingerprint']:
            raise ops.OperationError('片区或网格归属发生变化，事务已取消')
        for region in regions:
            row = region_values.get(region.id)
            if not row or region.name == row['name']:
                continue
            before = {'name': region.name}
            region.name = row['name']
            region.revision += 1
            region.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            ops.audit_log(db, user, 'zone', 'rename', region, before, {'name': row['name']})
    return {'applied': True, **plan['summary'], 'backup': str(backup)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pbf', type=Path, default=Path('data/bd_zones/kanto-latest.osm.pbf'))
    parser.add_argument('--plan', type=Path, default=Path('data/bd_zones/tokyo-grid-refresh-plan.json'))
    parser.add_argument('--checkpoint', type=Path, default=Path('data/bd_zones/tokyo-grid-refresh-checkpoint.json'))
    parser.add_argument('--batch-size', type=int, default=60)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup', type=Path)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 100:
        parser.error('--batch-size must be 1..100')
    if args.apply:
        if not args.backup or not args.backup.is_file() or args.backup.stat().st_size < 1000:
            parser.error('--apply requires a verified nonempty database backup')
        print(json.dumps(apply_plan(json.loads(args.plan.read_text()), args.backup), ensure_ascii=False), flush=True)
        return
    if not args.pbf.is_file():
        parser.error(f'OSM PBF not found: {args.pbf}')
    rail = json.loads(Path('scripts/bd_kanto_rail.json').read_text())
    with SessionLocal() as db:
        plan = build_plan(db, args.pbf, rail['stations'], args.batch_size, args.checkpoint)
    plan['source'] = {'pbf': args.pbf.name, 'rail': rail['source']}
    write_json(args.plan, plan)
    print(json.dumps({'plan': str(args.plan), **plan['summary']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
