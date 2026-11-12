"""Scope preview oracle: synthetic complete topology/bindings; no database or network."""
import copy
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shapely.geometry import box,mapping,shape
from shapely.ops import unary_union
from app.services import bd_zones as zones, bd_zone_coverage as coverage, bd_zone_balance as balance

def document(width=2,height=2,shops=100,hole=False):
    geoms=[box(x*500,y*500,(x+1)*500,(y+1)*500) for y in range(height) for x in range(width) if not(hole and x==1 and y==1)]
    work=unary_union(geoms)
    cells=[dict(id='cell-'+str(i),metric_geometry=mapping(g),geometry=zones.geographic(g),store_count=shops,merchant_ids=['store-'+str(i)+'-'+str(k) for k in range(shops)]) for i,g in enumerate(geoms)]
    return dict(ward_code='tokyo23',ward_name='东京23区整体',scope_ward_count=23,schema_version=1,metric_crs='EPSG:6677',coverage_revision=2,snapshot_id='fixed-scope',captured_at='2026-11-11T00:00:00Z',source={'geometry':'synthetic'},store_count=len(cells)*shops,area_km2=work.area/1e6,excluded_area_km2=0,road_count=0,boundary=zones.geographic(work),work_area=zones.geographic(work),road_cells=cells,topology=[sorted(coverage.face_graph(geoms)[i]) for i in range(len(geoms))],merchant_audit=dict(inside_boundary=len(cells)*shops,inside_work=len(cells)*shops,unlocated_in_work=0,excluded_unreviewed=0,source_duplicates=0,invalid_coordinates=0),review_merchants=[])
base=document()
cases=[('ready',copy.deepcopy(base),400,2,True,True),('hole',document(3,3,50,True),400,2,True,True),('zero-stores',document(2,2,0),400,2,True,True),('larger',document(4,4,20),100,4,True,True),('legacy-area',document(4,4,20),100,4,False,True),('memberships-off',copy.deepcopy(base),400,2,True,False)]
for name in ['duplicate-merchant','binding-mismatch','audit-missing','excluded-review','unlocated','source-duplicate','invalid-coordinate','uncovered','outside','overlap','duplicate-cell','topology-fallback','geographic','wrong-crs','empty']:
    doc=copy.deepcopy(base)
    if name=='duplicate-merchant':doc['road_cells'][1]['merchant_ids'][0]=doc['road_cells'][0]['merchant_ids'][0]
    elif name=='binding-mismatch':doc['road_cells'][0]['store_count']-=1
    elif name=='audit-missing':doc.pop('coverage_revision');doc.pop('merchant_audit')
    elif name=='excluded-review':doc['merchant_audit']['excluded_unreviewed']=1;doc['review_merchants']=[{'id':'review-1'}]
    elif name=='unlocated':doc['merchant_audit']['unlocated_in_work']=1
    elif name=='source-duplicate':doc['merchant_audit']['source_duplicates']=1
    elif name=='invalid-coordinate':doc['merchant_audit']['invalid_coordinates']=1
    elif name=='uncovered':doc['work_area']=zones.geographic(box(0,0,1500,1000))
    elif name=='outside':doc['work_area']=zones.geographic(box(0,0,500,1000))
    elif name=='overlap':
        extra=copy.deepcopy(doc['road_cells'][0]);extra['id']='overlap-cell';extra['merchant_ids']=['extra-'+str(i) for i in range(100)];doc['road_cells'].append(extra);doc.pop('topology');doc['store_count']+=100;doc['merchant_audit']['inside_work']+=100;doc['merchant_audit']['inside_boundary']+=100
    elif name=='duplicate-cell':doc['road_cells'][1]['id']=doc['road_cells'][0]['id']
    elif name=='topology-fallback':doc.pop('topology')
    elif name=='geographic':
        for cell in doc['road_cells']:cell.pop('metric_geometry')
        doc.pop('metric_crs')
    elif name=='wrong-crs':doc['metric_crs']='EPSG:4326'
    elif name=='empty':doc['road_cells']=[]
    cases.append((name,doc,400,2,True,True))
def geometry_document(geoms, shops=20):
    doc=document(1,1,0)
    work=unary_union(geoms)
    doc['road_cells']=[dict(id='shape-cell-'+str(i),metric_geometry=mapping(g),store_count=shops,merchant_ids=['shape-store-'+str(i)+'-'+str(k) for k in range(shops)]) for i,g in enumerate(geoms)]
    doc['topology']=[sorted(coverage.face_graph(geoms)[i]) for i in range(len(geoms))]
    doc['store_count']=len(geoms)*shops;doc['area_km2']=work.area/1e6
    doc['boundary']=zones.geographic(work);doc['work_area']=zones.geographic(work)
    doc['merchant_audit']['inside_boundary']=doc['merchant_audit']['inside_work']=doc['store_count']
    return doc
long=[box(x*500,y*500,(x+1)*500,(y+1)*500) for y in range(2) for x in range(8)]
curved=[box(x*500,y*500,(x+1)*500,(y+1)*500) for y in range(6) for x in range(6) if x<2 or y<2 or y>=4]
cases.extend([
 ('long-strip',geometry_document(long),400,4,True,True),
 ('curved-coast',geometry_document(curved),400,8,True,True),
 ('legacy-curved',geometry_document(curved),400,8,False,True),
 ('indivisible-large',geometry_document([box(0,0,3000,1000)],400),400,4,True,True),
 ('independent-residual',geometry_document([box(0,0,1000,1000),box(3000,0,3500,500)],100),400,4,True,True),
])
for name in ['generated-ready','generated-exclusion','generated-binding-rejected','generated-all-empty']:
    doc=copy.deepcopy(base)
    if name!='generated-ready':
        doc=geometry_document([box(0,0,1000,1000),box(3000,0,3500,500)],100)
        doc['road_cells'][1]['store_count']=0
        doc['road_cells'][1]['merchant_ids']=[] if name!='generated-binding-rejected' else ['hidden-binding']
        doc['store_count']=100;doc['merchant_audit']['inside_work']=doc['merchant_audit']['inside_boundary']=100
    if name=='generated-all-empty':
        for cell in doc['road_cells']:cell['store_count']=0;cell['merchant_ids']=[]
    doc['oracle_generation_rules']=True
    cases.append((name,doc,400,2,True,True))
for width in [60,101]:
    doc=geometry_document([box(0,0,1000,1000),box(1000+width,0,2000+width,1000)],5)
    doc['oracle_generation_rules']=True;doc['oracle_rivers']=[mapping(box(1000,-100,1000+width,1100))]
    cases.append(('generated-river-'+str(width),doc,8000,25,True,True))
rows=[]
for name,doc,stores,area,balanced,members in cases:
    options=balance.BalanceOptions(area,stores,.25,.75,1.35).validate() if balanced else None
    generation=doc.pop('oracle_generation_rules',False)
    river_faces=doc.pop('oracle_rivers',[])
    row=dict(riverFaces=river_faces,generationRules=generation,name=name,document=doc,targetStores=stores,maxAreaKm2=area,balance=vars(options) if options else None,includeMemberships=members)
    try:row['expected']=coverage.scope_preview(doc,stores,area,balance_options=options,include_memberships=members,generation_rules=generation,river_geometry=unary_union([shape(g) for g in river_faces]) if river_faces else None)
    except zones.ZoneDataError as error:row['error']=str(error)
    rows.append(row)
print(json.dumps(rows,ensure_ascii=False,allow_nan=False))
