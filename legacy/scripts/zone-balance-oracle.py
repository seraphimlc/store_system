"""Fixed nonlinear scope-balance oracle. No database/network; metric synthetic faces."""
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shapely.geometry import box, mapping, Polygon
from shapely.ops import unary_union
from app.services import bd_zone_balance as balance, bd_zone_coverage as coverage

def cells(width,height):
    return [box(x*1000,y*1000,(x+1)*1000,(y+1)*1000) for y in range(height) for x in range(width)]
base=cells(4,4)
cases=[
 ('balanced',base,[40]*16,dict(target_area=4,target_stores=160,area_weight=.25)),
 ('uneven',base,[0,160,2,5,4,10,30,80,3,0,1,100,40,1,4,70],dict(target_area=4,target_stores=160,area_weight=.25)),
 ('zero-stores',base,[0]*16,{}),
 ('sparse-large',cells(8,2),[1]*16,dict(target_area=10,target_stores=1000,area_weight=.15)),
 ('hole',[g for i,g in enumerate(cells(3,3)) if i!=4],[20]*8,dict(target_area=4,target_stores=100,area_weight=.3)),
 ('disconnected',base+[box(10000,0,10500,1000)],[40]*16+[0],dict(target_area=4,target_stores=160,area_weight=.25)),
 ('one',cells(1,1),[10000],{}),
 ('area-heavy',base,[40]*16,dict(target_area=4,target_stores=160,area_weight=.95)),
 ('stores-heavy',base,[40]*16,dict(target_area=4,target_stores=160,area_weight=.05)),
]
neck=[Polygon([(0,0),(1000,0),(1000,475),(1000,525),(1000,1000),(0,1000),(0,0)]),box(1000,475,2000,525),Polygon([(2000,0),(3000,0),(3000,1000),(2000,1000),(2000,525),(2000,475),(2000,0)])]
cases.extend([
 ('high-quota',cells(300,1),[100]*300,dict(target_area=2,target_stores=200,area_weight=.5,load_min=1,load_max=1.35)),
 ('commercial-neck',neck,[100]*3,{}),
 ('empty-neck',neck,[0]*3,{}),
])
rows=[]
for name,geoms,counts,raw in cases:
    options=balance.BalanceOptions(**raw).validate()
    groups=balance.partition(geoms,counts,coverage.face_graph(geoms),options)
    expected=[]
    for part in groups:
        geometry=unary_union([geoms[i] for i in sorted(part)])
        expected.append(dict(members=sorted(part),geometry=mapping(geometry),area=geometry.area,stores=sum(counts[i] for i in part),load=options.score(geometry.area,sum(counts[i] for i in part))))
    rows.append(dict(name=name,geometries=[mapping(g) for g in geoms],counts=counts,options=options.__dict__,expected=expected))
print(json.dumps(rows,ensure_ascii=False,allow_nan=False))
