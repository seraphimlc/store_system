#!/usr/bin/env python3
"""用当前数据库商户定位重算网格数量并组合成连通片区；默认只预览。"""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--region', default='all', choices=['all', 'tokyo23', 'tokyo26', 'kanagawa', 'saitama', 'chiba'])
    parser.add_argument('--target-stores', type=int, default=8000,
                        help='片区目标商户数，默认8000')
    parser.add_argument('--max-area-km2', type=float, default=25.,
                        help='片区面积硬上限，默认25 km²')
    parser.add_argument('--admin-user', required=True, help='现有在岗管理员账号')
    parser.add_argument('--apply', action='store_true', help='同一事务内刷新商户映射并替换基础片区')
    args = parser.parse_args()
    if not os.environ.get('DATABASE_URL'):
        parser.error('必须显式设置DATABASE_URL，避免误连默认空库')
    from app.db import SessionLocal
    from app.models import User
    from app.services.bd_grid_region_builder import plan
    from app.services.bd_zone_operations import OperationError
    from app.services.bd_zones import ZoneDataError
    try:
        with SessionLocal() as db:
            user = db.query(User).filter_by(username=args.admin_user).one_or_none()
            report = plan(db, user, region=args.region, target_stores=args.target_stores,
                          max_area_km2=args.max_area_km2, apply=args.apply)
            if args.apply:
                db.commit()
            else:
                db.rollback()
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except (OperationError, ZoneDataError) as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
