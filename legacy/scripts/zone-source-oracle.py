"""Offline source parity: synthetic geometry/CSV only. No database or source file contents."""
import csv,io,json,os,sys,tempfile,contextlib
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pathlib import Path
from shapely.geometry import box,Point,LineString
from app.services import bd_zones as z,bd_zone_coverage as coverage

def row(identity,x,y,name='synthetic'):
    lon,lat=z.geographic(Point(x,y))['coordinates'];return dict(place_id=identity,name=name,lat=str(lat),lon=str(lon))
base=dict(boundary=z.geographic(box(10000,-40000,12000,-39000)),roads=[z.geographic(LineString([(10300,-40100),(10300,-38900)]))],excluded=[z.geographic(box(11400,-39600,11600,-39400))],coastlines=[],capturedAt='2026-11-11T00:00:00Z',source={'merchants':'fixed-synthetic'})
rows=[row('small',10100,-39500,'小街块, "quoted"\n二行'),row('main',11000,-39400),row('water',11500,-39500),row('main',11050,-39450),row('',10500,-39500),row('outside',13000,-39500),dict(place_id='invalid',name='',lon='NaN',lat='20'),dict(place_id='infinite',lon='139',lat='inf')]
cases=[('source-audit',base,rows),('empty',base,[]),('all-invalid',base,[dict(place_id='bad',lon='0x8b',lat='35'),dict(place_id='missing',lon='',lat='')]),('duplicate-across-batch',base,[row('same',11000,-39500)]*20001),('boundary-tie',base,[row('edge',10300,-39500),row('boundary',10000,-39500)]),('coast',dict(base,coastlines=[z.geographic(LineString([(11000,-40100),(11000,-38900)]))]),[row('land',10500,-39500),row('sea',11500,-39500)])]
cases.extend([
    ('all-excluded',dict(base,excluded=[base['boundary']]),[]),
    ('outside-id-does-not-consume',base,[row('repeat',13000,-39500),row('repeat',10500,-39500)]),
])
out=[]
for name,inputs,merchants in cases:
    item=dict(name=name,input=inputs,merchants=merchants)
    with tempfile.TemporaryDirectory(prefix='zone-source-oracle-') as directory:
        path=Path(directory)/'merchants.csv'
        with path.open('w',newline='',encoding='utf-8-sig') as file:
            writer=csv.DictWriter(file,fieldnames=['place_id','name','lat','lon']);writer.writeheader();writer.writerows(merchants)
        try:
            with contextlib.redirect_stdout(io.StringIO()):result=coverage.prepare_source(inputs['boundary'],inputs['roads'],inputs['excluded'],inputs['coastlines'],path,captured_at=inputs['capturedAt'],source=inputs['source'])
            item['expected']=result
        except z.ZoneDataError as error:item['error']=str(error)
    out.append(item)
print(json.dumps(out,ensure_ascii=False,allow_nan=False))
