"""Fixed ward preview oracle; synthetic geometry only, no database or provider."""
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shapely.geometry import box
from shapely.ops import unary_union
from app.services import bd_zones as zones

def document(geometries,counts):
    merged=unary_union(geometries)
    return dict(schema_version=1,generation_version=3,ward_code='13104',ward_name='新宿区',snapshot_id='fixed-preview-source',captured_at='2026-11-11T00:00:00Z',source={'geometry':'synthetic'},boundary=zones.geographic(merged),work_area=zones.geographic(merged),area_km2=merged.area/1e6,excluded_area_km2=0,store_count=sum(counts),road_count=0,issues=[],grids=[dict(id='fixed-grid-'+str(i),geometry=zones.geographic(g),area_km2=g.area/1e6,store_count=counts[i]) for i,g in enumerate(geometries)])
base=[box(-10000+x*1000,-40000+y*1000,-9000+x*1000,-39000+y*1000) for y in range(4) for x in range(4)]
cases=[('balanced',document(base,[4]*16),16,25),('one-zone',document(base,[4]*16),64,25),('area-cap',document(base,[4]*16),64,4),('uneven-counts',document(base,[1,20,0,3,8,2,0,14,1,5,0,19,2,0,6,7]),20,8),('no-stores',document(base,[0]*16),400,25),('disconnected',document(base+[box(10000,-40000,11000,-39000)],[4]*17),40,25),('impossible-ratio',document([box(0,-40000,2000,-39000),box(2000,-40000,4000,-39000),box(10000,-40000,10500,-39000)],[4,4,4]),100,25),('pending-grid',document(base+[box(10000,-40000,10500,-39500)],[4]*16+[2]),16,25),('all-pending',document([box(0,-40000,500,-39500)],[1]),10,25),('invalid-stores',document(base,[4]*16),0,25),('invalid-area',document(base,[4]*16),20,1)]
cases.extend([
    ('single-eligible',document([base[0]],[4]),100,25),
    ('target-exceeds-grids',document(base,[4]*16),1,25),
    ('projection-gap',document([box(-10000,-40000,-9000,-39000),box(-8999.999,-40000,-7999.999,-39000)],[4,4]),100,25),
    ('hole-retained',document([box(-10000,-40000,-8000,-39000).difference(box(-9700,-39700,-9500,-39500)),box(-8000,-40000,-7000,-39000)],[4,8]),100,25),
])
rows=[]
for name,doc,stores,area in cases:
    zones.load_snapshot=lambda _code,doc=doc:doc
    row=dict(name=name,snapshot=doc,targetStores=stores,maxAreaKm2=area)
    try:row['expected']=zones.preview('13104',stores,area)
    except zones.ZoneDataError as error:row['error']=str(error)
    rows.append(row)
print(json.dumps(rows,ensure_ascii=False,allow_nan=False))
