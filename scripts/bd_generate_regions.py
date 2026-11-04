#!/usr/bin/env python3
"""追加生成其他地区正式网格；任一地区失败不触碰既有地区。"""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def add_facilities(doc,root,region):
    from shapely.geometry import Polygon,LineString,mapping
    from app.services import bd_zones as z
    path=root/(region+'.dividers.json')
    if path.exists():
        elements=json.loads(path.read_text())
        doc['facility_boundaries']=[{'osm_id':element['id'],'name':element['tags'].get('name',''),
            'metric_geometry':mapping(z.projected(mapping(Polygon([(p['lon'],p['lat']) for p in element['geometry']]))))}
            for element in elements if element.get('tags',{}).get('boundary')=='facility'
            and len(element.get('geometry',[]))>=4]
        doc['river_boundaries']=[{'osm_id':element['id'],'metric_geometry':mapping(z.projected(mapping(
            LineString([(p['lon'],p['lat']) for p in element['geometry']]))))}
            for element in elements if element.get('tags',{}).get('waterway')=='river'
            and len(element.get('geometry',[]))>=2]
    return doc


def build(path):
    from app.services import bd_natural_grids as natural
    from app.services.bd_zone_merchants import bind_database
    from shapely.geometry import mapping,Point
    from shapely import STRtree,union_all
    from app.services import bd_zones as z
    from app.db import SessionLocal
    from app.models import BdWorkGrid
    started=time.monotonic();raw=json.loads(path.read_text())
    signed_path=path.with_name(raw['region_code']+'.signed-roads.json')
    if signed_path.exists():raw['roads']+=json.loads(signed_path.read_text())
    # 不同采集日期的行政边界可能有细小偏差；既有正式陆地归属优先，
    # 将裁剪量明确写审计，不让追加地区侵入既有责任范围。
    boundary=z.projected(raw['boundary'])
    with SessionLocal() as db:
        existing=[z.projected(geometry) for geometry, in db.query(BdWorkGrid.geometry)]
    tree=STRtree(existing)
    overlaps=[existing[int(i)] for i in tree.query(boundary,predicate='intersects')]
    occupied=union_all(overlaps,grid_size=.001)
    overlap_area=boundary.intersection(occupied).area
    masks=raw['exclusions']+([z.geographic(occupied)] if overlaps else [])
    metadata={**raw['source'],'existing_responsibility_excluded_km2':overlap_area/1e6}
    divider_path=path.with_name(raw['region_code']+'.dividers.json')
    dividers=json.loads(divider_path.read_text()) if divider_path.exists() else []
    local_path=path.with_name(raw['region_code']+'.local-boundaries.json')
    if local_path.exists():dividers+=json.loads(local_path.read_text())
    doc=natural.prepare(raw['boundary'],raw['roads'],masks,raw['coastlines'],
        region_code=raw['region_code'],captured_at=datetime.now(timezone.utc).isoformat(),source=metadata,dividers=dividers)
    geometry_finished=time.monotonic()
    doc=add_facilities(bind_database(doc),path.parent,raw['region_code'])
    if overlaps:
        for review in doc.get('review_merchants',[]):
            if review.get('lng') is not None and review.get('lat') is not None and occupied.covers(
                    z.projected(mapping(Point(review['lng'],review['lat'])))):
                review['reason']='existing_region_responsibility'
    bound=time.monotonic()
    # 真实几何和只读商户绑定后的检查点，失败修正仅重做后续阶段。
    checkpoint=path.with_name(raw['region_code']+'.bound.json')
    temporary=checkpoint.with_suffix('.tmp')
    temporary.write_text(json.dumps(doc,ensure_ascii=False,separators=(',',':'),allow_nan=False))
    temporary.replace(checkpoint)
    doc=natural.normalise(doc)
    result=natural.candidate(doc)
    result['timing']={'geometry':geometry_finished-started,'merchants':bound-geometry_finished,
                      'normalise':time.monotonic()-bound,'total':time.monotonic()-started}
    return result


def main():
    from app.services.bd_regions import REGIONS
    from app.services import bd_natural_grids as natural,bd_zones as z
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--regions',nargs='+',choices=[code for code in REGIONS if code!='tokyo23'],default=['tokyo26','kanagawa','saitama','chiba'])
    parser.add_argument('--root',type=Path,default=Path('data/bd_zones/regions'))
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--admin-user')
    parser.add_argument('--resume-bound',action='store_true',help='显式从已完成的几何/商户检查点继续，仅重做归并与审计')
    args=parser.parse_args()
    if not os.getenv('DATABASE_URL'):parser.error('Explicit DATABASE_URL is required')
    if args.apply and not args.admin_user:parser.error('--apply requires --admin-user')
    failures=[]
    for region in args.regions:
        result=None
        try:
            print('Generating '+region,flush=True)
            if args.resume_bound:
                doc=json.loads((args.root/(region+'.bound.json')).read_text())
                if doc.get('region_code')!=region:raise z.ZoneDataError('检查点地区不匹配')
                doc=add_facilities(doc,args.root,region)
                started=time.monotonic()
                refinement_paths=[args.root/(region+suffix) for suffix in ('.signed-roads.json','.local-boundaries.json')]
                if any(path.exists() for path in refinement_paths):
                    elements=[element for path in refinement_paths if path.exists() for element in json.loads(path.read_text())]
                    if doc['natural_audit'].get('signed_refinement_sha256')!=natural.canonical_sha(elements):
                        from app.services.bd_region_refine import refine
                        from app.services.bd_zone_merchants import bind_database
                        doc=bind_database(refine(doc,elements))
                        checkpoint=args.root/(region+'.bound.json');temporary=checkpoint.with_suffix('.tmp')
                        temporary.write_text(json.dumps(doc,ensure_ascii=False,separators=(',',':'),allow_nan=False))
                        temporary.replace(checkpoint)
                from app.db import SessionLocal
                from app.models import BdWorkGrid
                from app.services.bd_region_refine import exclude_existing
                from app.services.bd_zone_merchants import bind_database
                from app.services import bd_zones as z
                with SessionLocal() as db:
                    existing=[z.projected(geometry) for geometry, in db.query(BdWorkGrid.geometry)]
                aligned_path=args.root/(region+'.aligned.json')
                alignment_identity=natural.canonical_sha([z.geographic(g) for g in existing])
                if aligned_path.exists():
                    saved=json.loads(aligned_path.read_text())
                else:saved={}
                if saved.get('alignment_identity')==alignment_identity and saved.get('refinement_identity')==doc['natural_audit'].get('signed_refinement_sha256'):
                    doc=saved['document']
                else:
                    refinement_identity=doc['natural_audit'].get('signed_refinement_sha256')
                    doc=bind_database(exclude_existing(doc,existing))
                    temporary=aligned_path.with_suffix('.tmp');temporary.write_text(json.dumps({
                        'alignment_identity':alignment_identity,'refinement_identity':refinement_identity,'document':doc},
                        ensure_ascii=False,separators=(',',':'),allow_nan=False));temporary.replace(aligned_path)
                result=natural.candidate(natural.normalise(doc))
                result['timing']={'resumed_from_bound':True,'normalise':time.monotonic()-started}
            else:
                result=build(args.root/(region+'.source.json'))
            audit=result['audit']
            if not audit.get('quality_pass') or not audit.get('geometric_pass') or not audit.get('merchant_pass'):
                result['status']='quality_failed'
            elif args.apply:
                from app.db import SessionLocal
                from app.models import User
                (args.root/(region+'.generated.json')).write_text(json.dumps(result,
                    ensure_ascii=False,separators=(',',':'),allow_nan=False))
                with SessionLocal() as db:
                    user=db.query(User).filter_by(username=args.admin_user).one()
                    release=natural.import_candidate(db,user,result,append_region=True)
                    db.commit();result.update(status='imported',applied=True,release_id=release.id)
            (args.root/(region+'.generated.json')).write_text(json.dumps(result,ensure_ascii=False,separators=(',',':'),allow_nan=False))
            summary={'region':region,'status':result['status'],'grids':len(result['features']),
                     'area_km2':result['source_document']['area_km2'],
                     'merchant_audit':audit['merchant_audit'],'violations':audit.get('normalisation',{}).get('violations',[]),
                     'timing':result['timing'],'geojson_sha256':audit['geojson_sha256']}
            print(json.dumps(summary,ensure_ascii=False),flush=True)
            if result['status']=='quality_failed':failures.append(region)
        except (OSError,ValueError) as exc:
            if result is not None:
                result.update(status='failed',applied=False,error=str(exc))
                (args.root/(region+'.generated.json')).write_text(json.dumps(result,
                    ensure_ascii=False,separators=(',',':'),allow_nan=False))
            if hasattr(exc,'audit'):
                (args.root/(region+'.error.json')).write_text(json.dumps(
                    {'region':region,'status':'failed','reason':str(exc),'audit':exc.audit},
                    ensure_ascii=False,separators=(',',':')))
            failures.append(region);print(json.dumps({'region':region,'status':'failed','reason':str(exc)},ensure_ascii=False),flush=True)
    if failures:raise SystemExit('Not imported: '+','.join(failures))


if __name__=='__main__':main()
