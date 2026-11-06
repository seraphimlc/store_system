#!/usr/bin/env python3
"""从固定关东OSM PBF提取其他地区原始地理要素，缓存可复用；不访问业务库。"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

TOKYO_CITIES={str(i) for i in (*range(13201,13216),*range(13218,13226),13227,13228,13229)}
PREFECTURES={'JP-14':'kanagawa','JP-11':'saitama','JP-12':'chiba'}


def collect(pbf,output):
    import osmium
    from shapely.geometry import shape,mapping,LineString
    from shapely import union_all,make_valid
    from app.services.bd_natural_grids import boundary_kind
    geo=osmium.geom.GeoJSONFactory()
    class Collector(osmium.SimpleHandler):
        def __init__(self):
            super().__init__();self.boundaries={};self.cities={};self.roads=[];self.coasts=[];self.masks=[];self.invalid=[];self.dividers=[]
        def way(self,way):
            tags=dict(way.tags)
            kind=boundary_kind(tags,length_m=0)
            coast=tags.get('natural')=='coastline'
            if not kind and not coast:return
            try:geometry=json.loads(geo.create_linestring(way))
            except (RuntimeError,osmium.InvalidLocationError):
                self.invalid.append(['way',way.id]);return
            if coast:self.coasts.append(geometry)
            if kind:
                self.roads.append({'type':'way','id':way.id,'tags':tags,
                    'geometry':[{'lon':x,'lat':y} for x,y in geometry['coordinates']]})
        def area(self,area):
            tags=dict(area.tags)
            admin=tags.get('boundary')=='administrative'
            pref=PREFECTURES.get(tags.get('ISO3166-2')) if admin else None
            ref=tags.get('ref','')[:5]
            city=admin and ref in TOKYO_CITIES
            municipal=admin and tags.get('admin_level') in ('7','8') and len(ref)==5 and ref[:2] in ('11','12','13','14')
            facility=tags.get('landuse')=='military' or tags.get('aeroway')=='aerodrome'
            # 河、湖、山地留在街区内，由干道外轮廓覆盖，不再挖成孔洞。
            if not pref and not city and not municipal and not facility:return
            try:geometry=make_valid(shape(json.loads(geo.create_multipolygon(area))))
            except (RuntimeError,ValueError,osmium.InvalidLocationError):
                self.invalid.append(['area',area.orig_id()]);return
            if geometry.is_empty:return
            if pref:self.boundaries[pref]=mapping(geometry)
            if city:self.cities[ref]=mapping(geometry)
            if municipal or facility:
                parts=[geometry] if geometry.geom_type=='Polygon' else list(getattr(geometry,'geoms',()))
                for index,part in enumerate(parts):
                    if part.geom_type=='Polygon':self.dividers.append({'type':'way','id':-int(area.id)*100-index,
                        'tags':{'boundary':'administrative' if municipal else 'facility','name':tags.get('name',''),'ref':ref},
                        'geometry':[{'lon':x,'lat':y} for x,y in part.exterior.coords]})
    output.mkdir(parents=True,exist_ok=True)
    collector=Collector()
    print('Reading OSM PBF with real locations and assembled administrative/water polygons',flush=True)
    collector.apply_file(str(pbf),locations=True,idx='flex_mem')
    if set(collector.cities)!=TOKYO_CITIES:
        raise RuntimeError('Missing Tokyo city boundaries: '+','.join(sorted(TOKYO_CITIES-set(collector.cities))))
    if set(collector.boundaries)!=set(PREFECTURES.values()):raise RuntimeError('Missing prefecture boundaries')
    collector.boundaries['tokyo26']=mapping(union_all([shape(g) for g in collector.cities.values()]))
    digest=hashlib.sha256(pbf.read_bytes()).hexdigest()
    for code,boundary in collector.boundaries.items():
        polygon=shape(boundary)
        # 裁剪和精度处理留给生成器；收集阶段保留完整OSM线，避免端点裂缝。
        roads=[element for element in collector.roads if LineString([(p['lon'],p['lat']) for p in element['geometry']]).intersects(polygon)]
        masks=[g for g in collector.masks if shape(g).intersects(polygon)]
        coasts=[g for g in collector.coasts if shape(g).intersects(polygon)]
        doc={'region_code':code,'boundary':boundary,'roads':roads,'exclusions':masks,'coastlines':coasts,
             'municipalities':sorted(TOKYO_CITIES) if code=='tokyo26' else None,
             'source':{'geometry':'OpenStreetMap / Geofabrik (ODbL)','pbf_file':pbf.name,'pbf_sha256':digest,
                       'region_code':code,'railway_collected':True,'captured_at':datetime.now(timezone.utc).isoformat(),
                       'invalid_source_geometries':collector.invalid}}
        path=output/(code+'.source.json')
        temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(doc,ensure_ascii=False,separators=(',',':')));temporary.replace(path)
        dividers=[element for element in collector.dividers if LineString(
            [(p['lon'],p['lat']) for p in element['geometry']]).intersects(polygon)]
        (output/(code+'.dividers.json')).write_text(json.dumps(dividers,ensure_ascii=False,separators=(',',':')))
        print(json.dumps({'region':code,'roads':len(roads),'exclusions':len(masks),'coastlines':len(coasts)},ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pbf',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=Path('data/bd_zones/regions'))
    args=parser.parse_args();collect(args.pbf,args.output)
