#!/usr/bin/env python3
"""以当前数据库商户坐标重算正式网格；默认预览，--apply 才提交。"""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--region', default='all', choices=['all', 'tokyo23', 'tokyo26', 'kanagawa', 'saitama', 'chiba'])
    parser.add_argument('--admin-user', required=True, help='审计使用的现有管理员用户名')
    parser.add_argument('--apply', action='store_true', help='明确提交；否则只输出统计并回滚')
    args = parser.parse_args()
    if not os.environ.get('DATABASE_URL'):
        parser.error('必须显式设置DATABASE_URL，避免误连默认空库')
    from app.db import SessionLocal
    from app.models import User
    from app.services.bd_grid_density import refresh
    from app.services.bd_zone_operations import OperationError
    from app.services.bd_zones import ZoneDataError
    try:
        with SessionLocal() as db:
            user = db.query(User).filter_by(username=args.admin_user).one_or_none()
            report = refresh(db, user, region=args.region, apply=args.apply)
            if args.apply:
                db.commit()
            else:
                db.rollback()
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except (OperationError, ZoneDataError) as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
