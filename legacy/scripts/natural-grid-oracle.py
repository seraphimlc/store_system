"""Natural geometry oracle with fixed synthetic inputs. No database/network/source files."""
import json,os,sys,math
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shapely.geometry import box,LineString
from app.services import bd_zones as z,bd_natural_grids as natural

def way(identity,coords,**tags):return dict(type='way',id=identity,tags=tags,geometry=[dict(lon=x,lat=y) for x,y in z.geographic(LineString(coords))['coordinates']])
base=dict(boundary=z.geographic(box(0,0,2000,1000)),excluded=[],coastlines=[],capturedAt='synthetic',source={'geometry':'synthetic'})
cases=[('grades',[way(1,[(1000,-100),(1000,1100)],highway='primary'),way(2,[(-100,500),(2100,500)],highway='residential'),way(3,[(500,-100),(500,1100)],highway='secondary',bridge='yes'),way(4,[(700,-100),(700,1100)],highway='trunk',tunnel='yes'),way(5,[(1500,-100),(1500,1100)],railway='rail',layer='-1')],[],[]),('rail-dangle',[way(1,[(1000,-100),(1000,1100)],railway='rail'),way(2,[(500,200),(500,400)],highway='secondary')],[],[]),('water',[way(1,[(-100,500),(2100,500)],highway='primary')],[box(900,-100,1100,1100),box(200,200,400,400)],[]),('numbered-tertiary',[way(1,[(1000,-100),(1000,1100)],highway='tertiary',ref='R16')],[],[]),('only-minor',[way(1,[(1000,-100),(1000,1100)],highway='residential')],[],[]),('divider',[way(1,[(1000,-100),(1000,1100)],highway='primary')],[],[way(99,[(-100,500),(2100,500)],waterway='river')]),('invalid-divider',[way(1,[(1000,-100),(1000,1100)],highway='primary')],[],[way(99,[(-100,500),(2100,500)],note='invented')]),('short-underpass',[way(1,[(950,500),(1050,500)],highway='primary',tunnel='yes')],[],[])]
output=[]
for name,elements,water,dividers in cases:
    inputs={**base,'elements':elements,'excluded':[z.geographic(g) for g in water],'dividers':dividers}
    row=dict(name=name,input=inputs)
    try:row['expected']=natural.prepare(inputs['boundary'],elements,inputs['excluded'],[],captured_at=inputs['capturedAt'],source=inputs['source'],dividers=dividers)
    except z.ZoneDataError as error:row['error']=str(error)
    output.append(row)
tags=[dict(highway='primary'),dict(highway='secondary_link'),dict(highway='residential'),dict(highway='tertiary'),dict(highway='tertiary',ref='R16'),dict(highway='unclassified',ref='X',access='private'),dict(highway='primary',tunnel='yes'),dict(highway='trunk',layer='-1'),dict(highway='primary',location='underwater'),dict(railway='rail'),dict(railway='rail',bridge='yes'),dict(railway='tram',layer='invalid'),dict(highway='primary',layer='NaN')]
classifications=[dict(tags=tag,length=length,expected=natural.boundary_kind(tag,length_m=length)) for tag in tags for length in (None,100,150,150.001,1000)]
print(json.dumps(dict(cases=output,classifications=classifications),ensure_ascii=False,allow_nan=False))
