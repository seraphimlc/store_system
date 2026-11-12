"""Development-only zone snapshot oracle: fixed synthetic geometry, no database access."""
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shapely.geometry import box, LineString, MultiPolygon, Point
from app.services import bd_zones as zones

cases = [
    ('dense-blocks', box(0,0,2000,2000), [LineString([(x,-10),(x,2010)]) for x in (500,1000,1500)] + [LineString([(-10,y),(2010,y)]) for y in (500,1000,1500)], [], [(250,250),(750,750),(1750,1750)], []),
    ('round-even', box(0,0,2500,1000), [LineString([(x,-10),(x,1010)]) for x in (500,1000,1500,2000)], [], [(250,250)], []),
    ('four-blocks', box(0, 0, 2000, 2000), [LineString([(1000, -10), (1000, 2010)]), LineString([(-10, 1000), (2010, 1000)])], [], [(100,100), (1000,1000), (1999,1999)], []),
    ('single-block', box(0,0,1000,1000), [], [], [(0,0), (500,500), (1500,1500)], []),
    ('oversize-no-roads', box(0,0,3000,2000), [], [], [], []),
    ('water-split', box(0,0,3000,1000), [], [box(1400,-10,1600,1010)], [(200,200), (1500,200), (2500,200)], []),
    ('disconnected', MultiPolygon([box(0,0,1000,1000), box(1500,0,2500,1000)]), [], [], [(100,100), (2000,100)], []),
    ('small-tail', box(0,0,1500,1000), [LineString([(1000,-10),(1000,1010)]),LineString([(1250,-10),(1250,1010)])], [], [(1200,500)], []),
    ('hole', box(0,0,2000,2000), [], [box(500,500,1500,1500)], [(100,100),(1000,1000)], []),
    ('west-coast', box(0,0,2000,1000), [], [], [(500,500),(1500,500)], [LineString([(1000,-100),(1000,1100)])]),
    ('east-coast', box(0,0,2000,1000), [], [], [(500,500),(1500,500)], [LineString([(1000,1100),(1000,-100)])]),
    ('outside-roads', box(0,0,1000,1000), [LineString([(2000,0),(2000,2000)])], [], [], []),
    ('coast-failure', box(0,0,2000,1000), [], [], [], [LineString([(1000,500),(1000,500)])]),
    ('excluded-all', box(0,0,1000,1000), [], [box(-100,-100,1100,1100)], [], []),
]
rows=[]
for name,boundary,roads,excluded,merchants,coastlines in cases:
    points=[zones.geographic(Point(x,y))['coordinates'] for x,y in merchants]
    inputs=dict(code='13104',boundary=zones.geographic(boundary),roads=[zones.geographic(g) for g in roads],excluded=[zones.geographic(g) for g in excluded],merchants=points,coastlines=[zones.geographic(g) for g in coastlines],capturedAt='2026-11-11T00:00:00+00:00',source='synthetic-offline-oracle')
    row=dict(name=name,input=inputs)
    try:
        row['expected']=zones.prepare_snapshot(inputs['code'],inputs['boundary'],inputs['roads'],inputs['excluded'],points,captured_at=inputs['capturedAt'],source=inputs['source'],coastlines=inputs['coastlines'])
    except zones.ZoneDataError as error:
        row['error']=str(error)
    rows.append(row)
print(json.dumps(rows,ensure_ascii=False,allow_nan=False))
