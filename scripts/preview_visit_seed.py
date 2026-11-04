#!/usr/bin/env python3
"""为真浏览器验收造一份**独立临时库**（不碰本地 PG）：批次 → 片区任务 → 网格任务 → 店铺任务。

⚠️ 本脚本会**清空**目标库再写演示数据 → 安全闸只放行带 preview 的临时 SQLite。

用法：DATABASE_URL="sqlite:///./data/preview_visit.db" python scripts/preview_visit_seed.py
"""
import os
import sys
import uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import date
from app.db import Base, SessionLocal
import app.db as appdb
from app.auth import hash_password
from app.models import (User, Person, BdTeam, BdTeamMember, BdZoneRelease, BdZone, BdWorkGrid,
                        BdGridStore, BdWorkStore, BdStoreVisitLog, BdMainTask, BdRegionTask,
                        BdGridTask, BdStoreTask)
from app.services import bd_zones as z
from app.services import bd_hierarchical_tasks as tasks
from shapely.geometry import box, Point

NAMES = [('雄飛堂薬局 西大泉店', 'normal'), ('已经没有的咖啡屋', 'closed'),
         ('西大泉中央公園前店', 'uncertain'), ('ほのぼの不動産', 'normal'),
         ('西大泉駅東口マート', 'abnormal'), ('ハPharmacie 泉水', 'normal')]


def main():
    # ⚠️ 安全闸：本脚本会**清空**目标库再写入演示数据 → 只许对着临时 SQLite 跑。
    url = os.environ.get('DATABASE_URL', '')
    if not url.startswith('sqlite:///'):
        raise SystemExit('拒绝执行：DATABASE_URL 必须是 sqlite:///… 的临时库（本脚本会清空该库）')
    if '/data/preview_' not in url and 'preview' not in url:
        raise SystemExit('拒绝执行：临时库文件名要带 preview，避免误伤其它库')
    engine = appdb._ENGINE
    Base.metadata.create_all(engine)
    db = SessionLocal()
    for model in (BdStoreTask, BdGridTask, BdRegionTask, BdMainTask, BdStoreVisitLog, BdWorkStore,
                  BdGridStore, BdWorkGrid, BdZone, BdZoneRelease, BdTeamMember, BdTeam, User, Person):
        db.query(model).delete()
    db.commit()

    db.add(Person(code='demo-captain', display_name='演示队长'))
    db.add(Person(code='demo-worker', display_name='演示队员'))
    db.add(Person(code='demo-admin', display_name='演示管理员'))
    captain = User(username='demo-captain', person_code='demo-captain', display_name='演示队长',
                   role='leader', password_hash=hash_password('demo123'), status='active', is_active=True)
    worker = User(username='demo-worker', person_code='demo-worker', display_name='演示队员',
                  role='staff', password_hash=hash_password('demo123'), status='active', is_active=True)
    admin = User(username='demo-admin', person_code='demo-admin', display_name='演示管理员',
                 role='admin', password_hash=hash_password('demo123'), status='active', is_active=True)
    db.add_all([captain, worker, admin]); db.flush()
    team = BdTeam(name='演示队'); db.add(team); db.flush()
    db.add(BdTeamMember(team_id=team.id, person_code='demo-captain', role='leader', start_date=date.today()))
    db.add(BdTeamMember(team_id=team.id, person_code='demo-worker', role='member', start_date=date.today()))

    release = BdZoneRelease(fingerprint='preview', source={'region_code': 'tokyo23'}, parameters={},
                             audit={}, reviews=[], created_by=admin.id)
    db.add(release); db.flush()
    geo = z.geographic(box(0, 0, 2000, 1000))
    zone = BdZone(release_id=release.id, source_id='zone-preview', name='西大泉片区', geometry=geo,
                  area_km2=2.0, store_count=0, review=[], state='ready',
                  team_id=team.id, leader_id=captain.id)
    db.add(zone); db.flush()
    grid = BdWorkGrid(release_id=release.id, zone_id=zone.id, source_id='grid-preview', geometry=geo,
                      area_km2=2.0, store_count=0, state='ready')
    db.add(grid); db.flush()

    lon, lat = z.geographic(Point(500, 500))['coordinates']
    for index, (name, status) in enumerate(NAMES):
        sid = f'preview-{index}'
        db.add(BdGridStore(release_id=release.id, grid_id=grid.id, source_id=sid))
        db.add(BdWorkStore(id=sid, name=name, address=f'東京都練馬区西大泉{index}丁目', lat=lat, lon=lon,
                           status=status))
    grid.store_count = len(NAMES); zone.store_count = len(NAMES)
    db.flush()

    # 批次 → 片区任务 → 网格任务 → 店铺任务（走真实服务层，行为与线上一致）
    task = tasks.create_main_task(db, admin, '2026年11月第一轮巡店', [zone.id]); db.flush()
    rt = db.query(BdRegionTask).filter_by(main_task_id=task.id).one()
    tasks.assign_region_task(db, admin, rt.id, team.id, rt.revision)
    gt = db.query(BdGridTask).filter_by(region_task_id=rt.id).one()
    tasks.assign_grid_task(db, captain, gt.id, worker.id, gt.revision)
    db.flush()
    # 已完成 2 家 → 作业进展自动算成 2/6
    stores = db.query(BdStoreTask).filter_by(grid_task_id=gt.id).order_by(BdStoreTask.id).all()
    for store in stores[:2]:
        tasks.complete_store(db, worker, store.id, store.revision, str(uuid.uuid4()),
                             'abnormal' if store.store_id == 'preview-1' else 'normal', '演示巡店')
    db.commit()
    print(f'batch_task_id={task.id} region_task_id={rt.id} grid_task_id={gt.id} '
          f'stores={len(stores)} progress={gt.progress}%')
    print('登录：demo-worker / demo123（队员）  demo-captain / demo123（队长）  demo-admin / demo123（管理员）')
    db.close()


if __name__ == '__main__':
    main()
