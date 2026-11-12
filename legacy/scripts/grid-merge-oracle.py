"""Development-only fixed geometry oracle; no database or production source access."""
import json
import hashlib
from types import SimpleNamespace
from shapely.geometry import box, Polygon, mapping, shape
from shapely import union_all
from shapely.ops import transform
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.services import bd_zones as z, bd_grid_regions as merge, bd_zone_rules as rules

square=box(0,0,1000,1000)
cases=[
 ('one',[square],[],True),
 ('edge',[square,box(1000,0,2000,1000)],[],True),
 ('corner',[square,box(1000,1000,2000,2000)],[],False),
 ('gap',[square,box(1100,0,2100,1000)],[],False),
 ('overlap',[square,box(500,0,1500,1000)],[],False),
 ('duplicate',[square,square],[],False),
 ('hole',[square.difference(box(200,200,800,800))],[],True),
 ('island-in-hole',[union_all([square.difference(box(200,200,800,800)),box(400,400,600,600)])],[],True),
 ('external-island',[union_all([square,box(2000,0,2100,100)])],[],False),
 ('bowtie',[Polygon([(0,0),(1000,1000),(1000,0),(0,1000),(0,0)])],[],False),
 ('thin',[box(0,0,5000,100)],[],True),
 ('neck',[union_all([box(0,0,1000,1000),box(1000,490,2000,510),box(2000,0,3000,1000)])],[],True),
 ('river30',[square,box(1030,0,2030,1000)],[box(1000,-100,1030,1100)],True),
 ('river100',[square,box(1100,0,2100,1000)],[box(1000,-100,1100,1100)],True),
 ('river101',[square,box(1101,0,2101,1000)],[box(1000,-100,1101,1100)],False),
 ('river-partial',[square,box(1060,0,2060,1000)],[box(1000,-100,1030,1100)],False),
 ('river-unrelated',[square,box(1060,0,2060,1000)],[box(3000,0,3060,1000)],False),
 ('river-chain',[square,box(1030,0,2030,1000),box(2060,0,3060,1000)],[box(1000,-100,1030,1100),box(2030,-100,2060,1100)],True),
 ('river-missing-link',[square,box(1030,0,2030,1000),box(2060,0,3060,1000)],[box(1000,-100,1030,1100)],False),
]
out=[]
for name,geometries,rivers,allowed in cases:
    geographic=[z.geographic(g) for g in geometries]
    river=union_all(rivers,grid_size=.001)
    rules.load_river_geometry=lambda _root,value=river:value
    row=dict(name=name,grids=geographic,rivers=[mapping(g) for g in rivers],allowed=allowed)
    try:
        result=merge.merged_geometry([SimpleNamespace(geometry=g) for g in geographic])
        assert allowed,name
        metrics=z._shape_metrics(result)
        row.update(legacy_wkb_sha256=hashlib.sha256(result.normalize().wkb).hexdigest(),wkb_sha256=hashlib.sha256(transform(lambda x,y:(x+0.,y+0.),result).normalize().wkb).hexdigest(),metric=mapping(result),area=result.area,acceptable=z._acceptable_shape(result),
                   solidity=metrics['solidity'],aspect=metrics['aspect'],compactness=metrics['compactness'])
    except Exception as exc:
        if allowed: raise
        row['error']=str(exc)
    out.append(row)
print(json.dumps(dict(cases=out,projection=[dict(lon=lon,lat=lat,point=z._TO_METERS(lon,lat)) for lon,lat in [(139.7,35.6),(140,36),(138,35),(139.833333333333,36)]]),allow_nan=True))
