#!/usr/bin/env python3
"""Preview names for ALL current grids from existing addresses; apply and verify once.

Generate: python scripts/bd_name_all_grids.py --ai
Apply:   python scripts/bd_name_all_grids.py --apply --admin-user admin --backup /path/current.dump
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1]/'.env')
from sqlalchemy import select, text
from app.db import SessionLocal
from app.models import BdWorkGrid, BdRegion, BdMainTask, BdRegionTask, BdGridTask, User
from app.services import bd_grid_address_names as naming, bd_grid_enrichment as enrichment
from app.services import bd_grid_metadata as metadata, bd_hierarchical_tasks as tasks
from scripts.bd_enrich_grid_metadata import write_json

PROTECTED = {
    'bd_work_grid': ['name', 'keywords', 'revision', 'updated_at'],
    'bd_region': ['name', 'revision', 'updated_at'],
    'bd_grid_store': [], 'bd_main_task': [], 'bd_region_task': [], 'bd_grid_task': [],
    'bd_store_task': [], 'bd_task_event': [], 'formal_records': [], 'person_daily_stats': [],
    'month_perf_records': [], 'payroll_period_rows': [],
}


def protected_state(db):
    result = {}
    for table, excluded in PROTECTED.items():
        if db.bind.dialect.name == 'postgresql':
            query = text(f'''SELECT COUNT(*) AS rows,md5(COALESCE(string_agg(digest,'' ORDER BY digest),'')) AS digest
                FROM (SELECT md5((to_jsonb(t)-CAST(:excluded AS text[]))::text) AS digest FROM {table} t) d''')
            result[table] = dict(db.execute(query, {'excluded': excluded}).mappings().one())
        else:
            from app.db import Base
            columns = [col for col in Base.metadata.tables[table].columns if col.name not in excluded]
            values = [hashlib.sha256(json.dumps(dict(r),sort_keys=True,default=str).encode()).hexdigest()
                      for r in db.execute(select(*columns)).mappings()]
            result[table] = {'rows': len(values), 'digest': hashlib.sha256(''.join(sorted(values)).encode()).hexdigest()}
    return result


def verify_once(db, user, plan, before):
    """One full acceptance pass after all writes, before committing the transaction."""
    grids = db.query(BdWorkGrid.id, BdWorkGrid.zone_id, BdWorkGrid.state, BdWorkGrid.name,
                     BdWorkGrid.stations, BdWorkGrid.keywords).order_by(BdWorkGrid.id).all()
    regions = db.query(BdRegion.id, BdRegion.name).order_by(BdRegion.id).all()
    expected = {g['id']: g for g in plan['grids']}
    assert len(grids) == len(expected) and {g.id for g in grids} == set(expected), '网格覆盖不完整'
    assert all(g.name and g.name.strip() and len(g.name) <= 128 for g in grids), '存在空名称或超长名称'
    assert len({metadata.normalized(g.name) for g in grids}) == len(grids), '存在重复网格名称'
    region_names = {r['id']: r['name'] for r in plan['regions']}
    assert {r.id:r.name for r in regions} == region_names, '片区命名不一致'
    assert len({metadata.normalized(r.name) for r in regions}) == len(regions), '存在重复片区名称'
    for row in grids:
        entry = expected[row.id]
        assert row.name == entry['name'] and row.stations == entry['stations'], f'网格{row.id}结果不符'
        assert row.keywords == metadata.clean_terms(entry['keywords']) and row.keywords, f'网格{row.id}检索词不符'
        assert metadata.matches(row.name, metadata.search_text(row._asdict())), f'网格{row.id}无法按主名检索'
    searchable = metadata.region_search_texts(db, list(region_names))
    for row in grids:
        if row.zone_id is not None and row.state != 'disabled':
            assert row.name in searchable[row.zone_id], f'网格{row.id}未进入创建批次检索'
    searches = []
    selectors = [
        ('address', lambda g:g['basis']=='address', lambda g:g['candidates'][0]),
        ('building', lambda g:bool(g['building_keywords']), lambda g:g['building_keywords'][0]),
        ('nearby_station', lambda g:bool(g['nearby_stations']), lambda g:g['nearby_stations'][0]['name']),
        ('fallback', lambda g:g['basis']=='area_fallback', lambda g:g['name']),
    ]
    for kind, predicate, keyword in selectors:
        example = next((g for g in plan['grids'] if predicate(g)), None)
        if example:
            term = keyword(example)
            assert db.query(BdWorkGrid.id).filter(BdWorkGrid.id==example['id'],metadata.grid_match(term)).first(), f'{kind}搜索失败'
            searches.append({'kind':kind,'grid_id':example['id'],'keyword':term})
    existing = db.query(BdMainTask.id,BdGridTask.grid_id).join(BdRegionTask,
        BdRegionTask.main_task_id==BdMainTask.id).join(BdGridTask,BdGridTask.region_task_id==BdRegionTask.id).join(
            BdWorkGrid,BdWorkGrid.id==BdGridTask.grid_id).order_by(BdGridTask.id).first()
    if existing:
        _, tree = tasks.task_tree(db,user,existing[0],keyword=expected[existing[1]]['name'])
        assert any(g['grid'].id==existing[1] for r in tree for g in r['grids']), '已有批次新名称检索失败'
    after = protected_state(db)
    assert before == after, '名称/关键字之外的数据发生变更'
    return {**naming.summary(plan), 'named_grids':len(grids), 'named_regions':len(regions),
            'searchable_grids':len(grids),'search_examples':searches,'protected_tables':after,
            'checks':{'full_coverage':True,'unique_names':True,'keyword_arrays':True,'inside_stations_preserved':True,
                      'batch_creation_search':True,'existing_batch_search':existing is not None,'protected_data_unchanged':True}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',type=Path,default=Path('data/bd_zones/address-naming-plan.json'))
    parser.add_argument('--ai',action='store_true')
    parser.add_argument('--batch-size',type=int,default=200)
    parser.add_argument('--workers',type=int,default=1)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--admin-user')
    parser.add_argument('--backup',type=Path)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 300 or not 1 <= args.workers <= 4:
        parser.error('batch-size: 1..300; workers: 1..4')
    if args.apply:
        if not args.admin_user or not args.backup or not args.backup.is_file() or not args.backup.stat().st_size:
            parser.error('--apply requires admin-user and an existing nonempty database backup')
        plan = json.loads(args.plan.read_text())
        with SessionLocal() as db:
            user = db.query(User).filter_by(username=args.admin_user).one_or_none()
            from app.services import bd_zone_operations as ops
            ops.require_admin(user)
            before = protected_state(db)
            enrichment.apply_plan(db,user,plan)
            db.flush()
            report = verify_once(db,user,plan,before)
            db.commit()
        report['backup'] = str(args.backup)
        report['source'] = plan['source']
        report_path = args.plan.with_suffix('.verification.json')
        write_json(report_path,report)
        print(json.dumps({'applied_and_verified':True,'report':str(report_path),**naming.summary(plan)},ensure_ascii=False),flush=True)
        return
    rail = json.loads(Path('scripts/bd_kanto_rail.json').read_text())
    with SessionLocal() as db:
        plan = naming.build_plan(db,rail_stations=rail['stations'])
    plan['source']['rail_source'] = rail['source']
    records = naming.choice_records(plan)
    # Persist complete deterministic coverage before any paid provider call.
    write_json(args.plan,plan)
    if args.ai:
        checkpoint = args.plan.with_suffix('.choices.json')
        signature = hashlib.sha256(json.dumps(records,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        previous = json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
        choices = previous.get('choices',{}) if previous.get('signature')==signature else {}
        batches = [records[i:i+args.batch_size] for i in range(0,len(records),args.batch_size)]
        pending = [batch for batch in batches if not all(r['key'] in choices for r in batch)]
        print(json.dumps({'prepared':naming.summary(plan),'ai_batches':len(pending)},ensure_ascii=False),flush=True)
        # Establish provider behavior with one batch before scheduling the remainder.
        if pending:
            choices.update(naming.select_names(pending.pop(0)))
            write_json(checkpoint,{'signature':signature,'choices':choices})
            print(f'AI selected {len(choices)}/{len(records)} names',flush=True)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(naming.select_names,batch):batch for batch in pending}
            try:
                for future in as_completed(futures):
                    choices.update(future.result())
                    write_json(checkpoint,{'signature':signature,'choices':choices})
                    print(f'AI selected {len(choices)}/{len(records)} names',flush=True)
            except BaseException:
                for future in futures:future.cancel()
                raise
        naming.apply_choices(plan,choices)
    write_json(args.plan,plan)
    print(json.dumps({'plan':str(args.plan),**naming.summary(plan)},ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
