"""Fixed row binding oracle. No actual merchant table or database access."""
import json,sys,os
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shapely.geometry import box,Point,mapping
from app.services import bd_zones as z,bd_zone_merchants as m
geoms=[box(10000,-40000,11000,-39000),box(11000,-40000,12000,-39000)]
doc=dict(metric_crs='EPSG:6677',captured_at='old-geometry-date',source={'geometry':'synthetic'},boundary=z.geographic(box(10000,-40000,12000,-39000)),work_area=z.geographic(box(10000,-40000,12000,-39000)),road_cells=[dict(id='cell-'+str(i),metric_geometry=mapping(g),area_km2=1,store_count=0,merchant_ids=[]) for i,g in enumerate(geoms)])
def row(identity,x=10500,y=-39500,name='synthetic'):
    lon,lat=z.geographic(Point(x,y))['coordinates'];return dict(place_id=identity,name=name,lat=lat,lon=lon)
base=[row(' a '),row('b',11500),row('a'),row('outside',13000),dict(place_id='invalid',name='invalid',lat=None,lon=None)]
cases=[('normal',doc,base),('boundary-owner',doc,[row('edge',11000),row('corner',10000,-40000)]),('global-invalid',doc,[dict(place_id='out-of-world',lat=91,lon=139),dict(place_id='',lat=35,lon=139),dict(place_id=None,lat=35,lon=139),dict(place_id=0,lat=35,lon=139),dict(place_id='0',lat=35,lon=139)]),('empty',doc,[]),('batch-duplicates',doc,[row('same')]*20001),('outside-does-not-consume',doc,[row('same',13000),row('same')])]
import copy
excluded=copy.deepcopy(doc);excluded['work_area']=z.geographic(geoms[0]);cases.append(('review-order',excluded,[row('excluded',11500),dict(place_id='bad',lat=None,lon=None),row('valid')]))
gap=copy.deepcopy(doc);gap['road_cells']=gap['road_cells'][:1];cases.append(('no-face',gap,[row('unmapped',11500),dict(place_id='bad',lat='',lon=139)]))
output=[]
for name,source,rows in cases:
    info=dict(backend='postgresql',database='store_settle',table=m.SOURCE_TABLE,total_count=len(rows),read_at='fixed-read-date')
    output.append(dict(name=name,document=source,rows=rows,sourceInfo=info,expected=m.bind_rows(source,rows,info)))
print(json.dumps(output,ensure_ascii=False,allow_nan=False))
