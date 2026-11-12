#!/usr/bin/env python3
"""Convert disabled grids and absorb unassigned, unworked grids with fewer than 100 stores.

Requires an explicit DATABASE_URL and a database backup. Default is a rollback-only preview.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shapely import STRtree, union_all
from sqlalchemy import func

from app.db import SessionLocal
from app.models import BdGridStore, BdGridTask, BdStoreVisitLog, BdWorkGrid, BdZoneRelease, User
from app.services import bd_zones as zones, bd_zone_operations as ops


def run(db, admin, apply=False):
    ops.require_admin(admin)
    for release_id, in db.query(BdZoneRelease.id).order_by(BdZoneRelease.id):
        ops.locked(db, BdZoneRelease, release_id)
    grids = db.query(BdWorkGrid).order_by(BdWorkGrid.release_id, BdWorkGrid.id).with_for_update().all()
    counts = dict(db.query(BdGridStore.grid_id, func.count(BdGridStore.id))
                  .group_by(BdGridStore.grid_id).all())
    tasks = {grid_id for grid_id, in db.query(BdGridTask.grid_id).distinct()}
    visits = {grid_id for grid_id, in db.query(BdStoreVisitLog.grid_id).distinct()}
    total_stores = sum(counts.values())

    def free(grid):
        return (grid.zone_id is None and grid.assignee_id is None and not grid.progress
                and grid.task_state == 'unvisited' and grid.id not in tasks and grid.id not in visits)

    disabled = [grid for grid in grids if grid.state == 'disabled']
    if any(not free(grid) for grid in disabled):
        raise ops.OperationError('禁用网格存在片区或作业引用，已停止')
    for grid in disabled:
        before = {'state': grid.state, 'revision': grid.revision}
        grid.state = 'ready'
        grid.revision += 1
        grid.updated_at = datetime.utcnow()
        if apply:
            ops.audit_log(db, admin, 'grid', 'enable_grid', grid, before,
                          {'state': grid.state, 'revision': grid.revision})

    by_release = defaultdict(list)
    for grid in grids:
        by_release[grid.release_id].append(grid)
    merged = []
    skipped = []
    for release_id, group in by_release.items():
        candidates = [grid for grid in group if free(grid) and grid.state == 'ready']
        geometry = {grid.id: zones.projected(grid.geometry) for grid in candidates}
        tree = STRtree(list(geometry.values()))
        index = list(candidates)
        alive = {grid.id: grid for grid in candidates}
        owner = {grid.id: grid.id for grid in candidates}
        sources = sorted((grid for grid in candidates if counts.get(grid.id, 0) < 100),
                         key=lambda grid: (counts.get(grid.id, 0), grid.id))
        for source in sources:
            if source.id not in alive or counts.get(source.id, 0) >= 100:
                continue
            source_geometry = geometry[source.id]
            neighbours = set()
            for j in tree.query(source_geometry, predicate='intersects'):
                target_id = owner[index[int(j)].id]
                if target_id != source.id and target_id in alive:
                    target_geometry = geometry[target_id]
                    if source_geometry.boundary.intersection(target_geometry.boundary).length > .01:
                        neighbours.add(target_id)
            if not neighbours:
                skipped.append({'id': source.id, 'reason': 'no_free_shared_edge'})
                continue
            target_id = max(neighbours, key=lambda identity: (counts.get(identity, 0), -identity))
            target = alive[target_id]
            combined = union_all([source_geometry, geometry[target_id]], grid_size=.001)
            if (combined.is_empty or not combined.is_valid
                    or combined.geom_type not in ('Polygon', 'MultiPolygon')
                    or abs(combined.area - source_geometry.area - geometry[target_id].area) > .01):
                skipped.append({'id': source.id, 'reason': 'invalid_union_or_overlap'})
                continue
            source_count = counts.get(source.id, 0)
            before = {'source_id': target.source_id, 'store_count': target.store_count,
                      'revision': target.revision, 'absorbed_id': source.id,
                      'absorbed_source_id': source.source_id}
            if apply:
                db.query(BdGridStore).filter(BdGridStore.grid_id == source.id).update(
                    {BdGridStore.grid_id: target.id}, synchronize_session=False)
                target.geometry = zones.geographic(combined)
                target.area_km2 = combined.area / 1e6
                target.store_count = counts.get(target.id, 0) + source_count
                target.revision += 1
                target.updated_at = datetime.utcnow()
                db.delete(source)
                ops.audit_log(db, admin, 'grid', 'merge_low_density', target, before,
                              {'store_count': target.store_count, 'revision': target.revision,
                               'absorbed_id': source.id})
            counts[target.id] = counts.get(target.id, 0) + source_count
            geometry[target.id] = combined
            for identity, root in owner.items():
                if root == source.id:
                    owner[identity] = target.id
            del alive[source.id]
            merged.append({'source': source.id, 'target': target.id,
                           'source_stores': source_count, 'target_stores': counts[target.id],
                           'release': release_id})
    if apply:
        db.flush()
        remaining = db.query(func.count(BdGridStore.id)).scalar()
        if remaining != total_stores:
            raise ops.OperationError('商户映射总量变化，已回滚')
        if db.query(BdGridStore.id).outerjoin(BdWorkGrid).filter(BdWorkGrid.id.is_(None)).first():
            raise ops.OperationError('商户映射出现悬空引用，已回滚')
    return {'enabled_ids': [grid.id for grid in disabled], 'merged': merged,
            'skipped': skipped, 'initial_grid_count': len(grids),
            'initial_store_mappings': total_stores, 'applied': apply}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--admin-user', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if not os.environ.get('DATABASE_URL'):
        parser.error('必须显式设置 DATABASE_URL')
    if args.apply and (not args.backup or not args.backup.is_file() or args.backup.stat().st_size == 0):
        parser.error('提交前必须提供有效的 --backup')
    with SessionLocal() as db:
        admin = db.query(User).filter_by(username=args.admin_user).one()
        report = run(db, admin, apply=args.apply)
        if args.apply:
            db.commit()
        else:
            db.rollback()
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({'applied': args.apply, 'enabled': len(report['enabled_ids']),
                      'merged': len(report['merged']), 'skipped': dict(Counter(
                          row['reason'] for row in report['skipped'])),
                      'report': str(args.report)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
