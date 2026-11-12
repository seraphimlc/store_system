"""Offline OSM relation/water/ward oracle, with complete synthetic 23-ward input."""
import json,os,sys
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shapely.geometry import mapping
from shapely.ops import unary_union
from scripts.bd_collect_zones import relation_polygon,exclusions,ward_relations

def points(coords):return [dict(lon=x,lat=y) for x,y in coords]
def relation(identity,coordinates,tags=None,inner=None):
    return dict(type='relation',id=identity,tags=tags or {},members=[dict(type='way',role='outer',geometry=points(coordinates[:3])),dict(type='way',role='',geometry=points(coordinates[2:]))]+([] if inner is None else [dict(type='way',role='inner',geometry=points(inner))]))
ring=[(139,35),(139.01,35),(139.01,35.01),(139,35.01),(139,35)]
hole=[(139.002,35.002),(139.004,35.002),(139.004,35.004),(139.002,35.004),(139.002,35.002)]
water=[relation(1,ring,dict(natural='water'),hole),dict(type='way',id=2,tags=dict(natural='wood'),geometry=points(ring)),dict(type='way',id=3,tags=dict(landuse='reservoir'),geometry=points(ring)),dict(type='way',id=4,tags=dict(waterway='river'),geometry=points(ring[:3])),dict(type='way',id=5,tags=dict(waterway='riverbank'),geometry=points(ring[:3]))]
wards=[];raw={}
for i in range(23):
    x=139+i*.01;coords=[(x,35),(x+.01,35),(x+.01,35.01),(x,35.01),(x,35)]
    wards.append(relation(100+i,coords,dict(ref=str(13101+i)+'9',admin_level='7',name='任意区名')))
    raw[str(13101+i)]={'elements':[dict(type='way',id=200+i,tags=dict(highway='residential'),geometry=points([(x+.005,34.999),(x+.005,35.011)])),dict(type='way',id=300+i,tags=dict(railway='rail'),geometry=points([(x,35),(x+.01,35.01)]))]+(water if i==0 else []),'osm3s':{'timestamp_osm_base':'2026-11-11T00:00:00Z'}}
wards.append(relation(999,ring,dict(ref='27100',admin_level='7',name='中央区')))
coasts={'elements':[dict(type='way',id=900,tags=dict(natural='coastline'),geometry=points([(138.9,34.9),(138.9,35.1)]))]}
by_code=ward_relations(wards);road_geometries=[];elements={}
for code in sorted(by_code):
    for item in raw[code]['elements']:
        elements[(item['type'],item['id'])]=item
        if item['type']=='way' and item.get('tags',{}).get('highway') and len(item.get('geometry',[]))>1:road_geometries.append(dict(type='LineString',coordinates=[[p['lon'],p['lat']] for p in item['geometry']]))
expected=dict(boundary=mapping(unary_union([relation_polygon(by_code[code]) for code in sorted(by_code)])),roads=road_geometries,excluded=exclusions(elements.values()),coastlines=[dict(type='LineString',coordinates=[[p['lon'],p['lat']] for p in item['geometry']]) for item in coasts['elements']])
print(json.dumps(dict(input=dict(boundaries=dict(elements=wards),coastlines=coasts,wards=raw,capturedAt='2026-11-11T00:00:00Z'),expected=expected,relation=water[0],relationExpected=mapping(relation_polygon(water[0])),water=water,waterExpected=exclusions(water)),ensure_ascii=False))
