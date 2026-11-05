#!/usr/bin/env python3
"""Extract verified geography, preview AI names, then explicitly apply the full plan.

Example: python scripts/bd_enrich_grid_metadata.py --pbf /path/kanto.osm.pbf
Apply:   python scripts/bd_enrich_grid_metadata.py --apply --admin-user admin --backup /path/pre-enrichment.dump
The source file must be a real OSM PBF or a GeoJSON FeatureCollection with OSM tags.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv()
from app.db import SessionLocal
from app.models import BdRegion, BdWorkGrid, User
from app.services import bd_grid_enrichment as enrichment


def write_json(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2))
    temporary.replace(path)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sources=parser.add_mutually_exclusive_group()
    sources.add_argument('--pbf',type=Path)
    sources.add_argument('--features',type=Path)
    parser.add_argument('--plan',type=Path,default=Path('data/bd_zones/grid-naming-plan.json'))
    parser.add_argument('--batch-size',type=int,default=60)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--admin-user')
    parser.add_argument('--backup',type=Path,help='已完成的当前数据库备份；PostgreSQL须使用pg_dump')
    args=parser.parse_args()
    if not 1<=args.batch_size<=100:
        parser.error('--batch-size must be between 1 and 100')
    if args.apply and (not args.admin_user or not args.backup or not args.backup.is_file() or not args.backup.stat().st_size):
        parser.error('--apply requires an admin user and an existing nonempty database backup')
    source=args.pbf or args.features
    if source:
        if not source.is_file():
            parser.error(f'Geographic source not found: {source}')
        with SessionLocal() as db:
            regions=db.query(BdRegion).order_by(BdRegion.id).all()
            grids=db.query(BdWorkGrid).order_by(BdWorkGrid.id).all()
            if not grids:
                parser.error('No current grids')
            signature=enrichment.fingerprint(regions,grids)
            print(f'Extracting source geography for {len(regions)} regions / {len(grids)} grids',flush=True)
            if args.pbf:
                features=enrichment.pbf_features(source)
            else:
                data=json.loads(source.read_text())
                if data.get('type')!='FeatureCollection' or not isinstance(data.get('features'),list):
                    parser.error('Expected a GeoJSON FeatureCollection')
                features=data['features']
            if not features:
                parser.error('Source contains no geographic features')
            geo=enrichment.geographic_metadata(grids,features)
            if not any(v['stations'] or v['keywords'] for v in geo.values()):
                parser.error('Source has no verified station or landmark intersecting current grids')
            inputs=enrichment.naming_inputs(regions,grids,geo)
        checkpoint=args.plan.with_suffix('.checkpoint.json')
        previous=json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
        responses=previous.get('responses',{}) if previous.get('fingerprint')==signature else {}
        region_names={};grid_names={}
        for group in inputs:
            members=group['grids']
            chunks=[members[start:start+args.batch_size] for start in range(0,len(members),args.batch_size)] or [[]]
            for number,chunk in enumerate(chunks):
                payload={**group,'grids':chunk}
                if number:payload['region_name']=region_names.get(group['region_id'],'')
                digest=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
                response=responses.get(digest)
                if response is None:
                    response=enrichment.generate_names(payload,include_region=group['region_id'] is not None)
                    responses[digest]=response
                    write_json(checkpoint,{'fingerprint':signature,'responses':responses})
                grid_names.update(enrichment.validate_names(response,[g['id'] for g in chunk],include_region=group['region_id'] is not None))
                if group['region_id'] is not None and number==0:
                    region_names[group['region_id']]=response['region_name'].strip()
                print(f'Named {len(grid_names)} / {len(geo)} grids',flush=True)
        digest=hashlib.sha256()
        with source.open('rb') as handle:
            for block in iter(lambda:handle.read(1024*1024),b''):digest.update(block)
        plan={'fingerprint':signature,'source':{'file':source.name,'sha256':digest.hexdigest(),
             'attribution':'OpenStreetMap contributors (ODbL)'},
             'regions':[{'id':r.id,'name':region_names[r.id]} for r in regions],
             'grids':[{'id':g.id,'name':grid_names[g.id],'stations':geo[g.id]['stations'],
                       'keywords':geo[g.id]['keywords']} for g in grids]}
        write_json(args.plan,plan)
        print(json.dumps({'plan':str(args.plan),'regions':len(regions),'grids':len(grids),
            'grids_with_stations':sum(bool(v['stations']) for v in geo.values()),
            'grids_with_landmarks':sum(bool(v['keywords']) for v in geo.values())},ensure_ascii=False),flush=True)
    elif not args.apply:
        parser.error('Provide --pbf or --features; no geographic names will be invented without a source')
    if args.apply:
        plan=json.loads(args.plan.read_text())
        with SessionLocal() as db:
            user=db.query(User).filter_by(username=args.admin_user).one_or_none()
            counts=enrichment.apply_plan(db,user,plan)
            db.commit()
        print(f'Applied {counts[0]} region names and {counts[1]} grid metadata rows',flush=True)


if __name__=='__main__':
    main()
