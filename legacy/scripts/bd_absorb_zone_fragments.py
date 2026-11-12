#!/usr/bin/env python3
"""把一个地区里不足阈值的网格并进同片区的共边邻格。未归属碎片只进入贴着的片区。"""
import argparse
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--region', required=True, choices=('kanagawa', 'saitama', 'chiba', 'tokyo26', 'tokyo23'))
    parser.add_argument('--max-km2', type=float, default=0.36)
    parser.add_argument('--target', choices=('longest-edge', 'smallest-neighbor'), default='longest-edge')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--admin-user')
    args = parser.parse_args()
    if not os.getenv('DATABASE_URL'):
        parser.error('Explicit DATABASE_URL is required')
    if args.apply and not args.admin_user:
        parser.error('--apply requires --admin-user')
    from app.db import SessionLocal
    from app.models import User
    from app.services.bd_grid_geometry import absorb_zone_fragments
    with SessionLocal() as db:
        user = db.query(User).filter_by(username=args.admin_user or 'admin').one()
        report = absorb_zone_fragments(
            db, user, args.region, max_area_km2=args.max_km2,
            target=args.target.replace('-', '_'), apply=args.apply)
        if args.apply:
            db.commit()
    summary = {key: report[key] for key in ('region', 'max_area_km2', 'removed_grid_count', 'remaining_small_ids', 'zones_updated')}
    summary['groups'] = len(report['groups'])
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
