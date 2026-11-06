#!/usr/bin/env python3
"""Apply a Places preview to one region: stations, scene keywords, grid and region names.

Address keywords already on the grid are kept. Scene names are appended after
office rooms and monuments are removed. The database is unchanged without --apply.

Example:
  python scripts/bd_apply_grid_places.py --region tokyo26
  python scripts/bd_apply_grid_places.py --region tokyo26 --apply --backup data/backup/before.dump
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / '.env')

from app.db import SessionLocal
from app.models import BdRegion, BdWorkGrid, BdZoneRelease, User
from app.services import bd_grid_address_names as address_names
from app.services import bd_grid_enrichment as enrichment
from app.services import bd_grid_metadata as metadata
from app.services import bd_grid_places as places
from app.services import bd_zone_operations as ops


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.replace(path)


def target(db, region_code):
    def matches(source):
        code = (source or {}).get('region_code')
        if region_code == 'tokyo23':
            return code in (None, '', 'tokyo23')
        return code == region_code
    releases = [row for row in db.query(BdZoneRelease).order_by(BdZoneRelease.id) if matches(row.source)]
    releases = [row for row in releases if db.query(BdWorkGrid).filter_by(release_id=row.id).count()]
    if len(releases) != 1:
        raise ValueError(f'{region_code} 的版本不是恰好一份')
    release = releases[0]
    regions = db.query(BdRegion).filter_by(release_id=release.id).order_by(BdRegion.id).all()
    grids = db.query(BdWorkGrid).filter_by(release_id=release.id).order_by(BdWorkGrid.id).all()
    return release, regions, grids


def build_plan(db, region_code, preview):
    release, regions, grids = target(db, region_code)
    incoming = {row['id']: row for row in preview['grids']}
    if {grid.id for grid in grids} != set(incoming):
        raise ValueError('预览网格与当前地区网格不一致')
    grid_rows = []
    labels = {}
    for grid in grids:
        source = incoming[grid.id]
        scenes = places.filter_scenes(source.get('keywords') or [])
        # Places order is nearest-first. Existing names stay searchable after that.
        stations = metadata.clean_terms([*(source.get('stations') or []), *(grid.stations or [])])
        label = places.grid_label(grid.name, stations, scenes)
        keywords = metadata.clean_terms([*(grid.keywords or []), *scenes])
        grid_rows.append({
            'id': grid.id, 'zone_id': grid.zone_id, 'old_name': grid.name,
            'name': address_names.compose_name(label, 'G', grid.id), 'label': label,
            'old_stations': list(grid.stations or []), 'stations': stations,
            'old_keywords': list(grid.keywords or []), 'keywords': keywords,
            'store_count': grid.store_count or 0,
        })
        labels[grid.id] = label
    region_rows = []
    for region in regions:
        members = [{'label': labels[row['id']], 'stores': row['store_count']}
                   for row in grid_rows if row['zone_id'] == region.id]
        label = places.region_label(members, region.name)
        region_rows.append({
            'id': region.id, 'old_name': region.name,
            'name': address_names.compose_name(label, 'R', region.id) if label else region.name,
            'label': label, 'member_grid_ids': [row['id'] for row in grid_rows if row['zone_id'] == region.id],
        })
    return {
        'region_code': region_code, 'release_id': release.id,
        'fingerprint': enrichment.fingerprint(regions, grids),
        'grids': grid_rows, 'regions': region_rows,
        'summary': {
            'grids': len(grid_rows),
            'grid_names_changed': sum(row['old_name'] != row['name'] for row in grid_rows),
            'grids_with_stations': sum(bool(row['stations']) for row in grid_rows),
            'scene_terms_added': sum(len(set(row['keywords']) - set(row['old_keywords'])) for row in grid_rows),
            'regions': len(region_rows),
            'region_names_changed': sum(row['old_name'] != row['name'] for row in region_rows),
        },
    }


def apply_plan(plan, backup):
    with SessionLocal.begin() as db:
        release, regions, grids = target(db, plan['region_code'])
        db.query(BdZoneRelease).filter_by(id=release.id).populate_existing().with_for_update().one()
        regions = db.query(BdRegion).filter_by(release_id=release.id).order_by(BdRegion.id) \
            .populate_existing().with_for_update().all()
        grids = db.query(BdWorkGrid).filter_by(release_id=release.id).order_by(BdWorkGrid.id) \
            .populate_existing().with_for_update().all()
        if release.id != plan['release_id'] or enrichment.fingerprint(regions, grids) != plan['fingerprint']:
            raise ops.OperationError('网格或片区在预览后变了，没有写入')
        if {grid.id for grid in grids} != {row['id'] for row in plan['grids']}:
            raise ops.OperationError('计划没有覆盖当前地区的全部网格')
        user = db.query(User).filter_by(username='admin').one()
        ops.require_admin(user)
        grid_values = {row['id']: row for row in plan['grids']}
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
        region_values = {row['id']: row for row in plan['regions']}
        for region in regions:
            row = region_values[region.id]
            if region.name == row['name']:
                continue
            before = {'name': region.name}
            region.name = row['name']
            region.revision += 1
            region.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            ops.audit_log(db, user, 'zone', 'rename', region, before, {'name': row['name']})
    return {'applied': True, 'backup': str(backup), **plan['summary']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--region', default='tokyo26')
    parser.add_argument('--preview', type=Path, default=Path('data/bd_zones/tokyo26-places-preview.json'))
    parser.add_argument('--plan', type=Path, default=Path('data/bd_zones/tokyo26-places-apply-plan.json'))
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup', type=Path)
    args = parser.parse_args()
    if args.apply and (not args.backup or not args.backup.is_file() or args.backup.stat().st_size < 1000):
        parser.error('--apply 需要一份已存在的数据库备份')
    preview = json.loads(args.preview.read_text())
    if preview.get('region') != args.region:
        parser.error('预览地区与 --region 不一致')
    if args.apply:
        plan = json.loads(args.plan.read_text())
        if plan.get('region_code') != args.region:
            parser.error('计划地区与 --region 不一致')
        print(json.dumps(apply_plan(plan, args.backup), ensure_ascii=False), flush=True)
        return
    with SessionLocal() as db:
        plan = build_plan(db, args.region, preview)
    write_json(args.plan, plan)
    print(json.dumps({'plan': str(args.plan), **plan['summary']}, ensure_ascii=False), flush=True)
    print('GRID SAMPLES')
    changed = [row for row in plan['grids'] if row['old_name'] != row['name']]
    for row in changed[:12]:
        print(f"  {row['old_name']} -> {row['name']} stations={row['stations'][:3]}")
    print('REGION SAMPLES')
    for row in plan['regions']:
        if row['old_name'] != row['name']:
            print(f"  {row['old_name']} -> {row['name']}")


if __name__ == '__main__':
    main()
