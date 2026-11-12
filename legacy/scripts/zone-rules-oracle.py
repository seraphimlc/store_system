"""Synthetic consolidation oracle; no database, original files or provider calls."""
import json,sys,os
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shapely.geometry import box,mapping
from shapely.ops import unary_union
from app.services import bd_zone_rules as rules,bd_zone_balance as balance,bd_zone_coverage as coverage,bd_zones as zones
cases=[]
def add(name,geoms,counts,rivers=None,width=100,area=None):
    cases.append((name,geoms,counts,rivers,width,sum(g.area for g in geoms)/1e6 if area is None else area))
add('adjacent',[box(i*1000,0,(i+1)*1000,1000) for i in range(3)],[2,3,4])
add('empty-outside',[box(0,0,2000,2000),box(4000,0,5000,1000)],[5,0])
add('contained',[box(0,0,4000,4000).difference(box(1000,1000,3000,3000)),box(1500,1500,2000,2000)],[10,2])
add('nested',[box(0,0,10000,10000).difference(box(1000,1000,9000,9000)),box(2000,2000,8000,8000).difference(box(3000,3000,7000,7000)),box(4000,4000,5000,5000)],[10,2,1])
add('all-empty',[box(0,0,1000,1000)],[0])
add('overload',[box(0,0,1000,1000),box(1000,0,2000,1000)],[8000,8000])
for width in [30,60,100,100.0005,100.1,180]:
    add('river-'+str(width),[box(0,0,1000,1000),box(1000+width,0,2000+width,1000)],[5,5],box(1000,-100,1000+width,1100))
for name,river,limit in [('no-evidence',None,100),('partial',box(1000,-100,1030,1100),100),('unrelated',box(3000,0,3060,1000),100),('disabled',box(1000,-100,1060,1100),0),('invalid-width',None,-1)]:
    add(name,[box(0,0,1000,1000),box(1060,0,2060,1000)],[5,5],river,limit)
rows=[]
for name,geoms,counts,river,width,area in cases:
    graph=coverage.face_graph(geoms);row=dict(name=name,geometries=[mapping(g) for g in geoms],counts=counts,topology=[sorted(graph[i]) for i in range(len(geoms))],members=[[i] for i in range(len(geoms))],rivers=[] if river is None else [mapping(river)],maxRiverWidthM=width,areaKm2=area)
    try:
        groups,excluded,policy=rules.consolidate([set(part) for part in row['members']],geoms,counts,graph,balance.BalanceOptions(),area_km2=area,rivers=river,max_river_width_m=width)
        row['expected']=dict(groups=[sorted(part) for part in groups],excluded=sorted(excluded),policy=policy)
    except zones.ZoneDataError as error:row['error']=str(error)
    rows.append(row)
print(json.dumps(rows,ensure_ascii=False,allow_nan=False))
