"""在已完成的原子面上补充真实编号干道/町丁目界，不改变作业范围。"""
import hashlib
from shapely import STRtree,union_all,set_precision
from shapely.geometry import shape,LineString,mapping
from shapely.ops import split,transform
from app.services import bd_zones as z,bd_natural_grids as natural
from app.services.bd_grid_geometry import coverage_matches


def refine(doc,elements):
    lines=[];retained=[]
    for element in elements:
        tags=element.get('tags',{})
        road=bool(tags.get('ref')) and tags.get('highway') in ('tertiary','tertiary_link','unclassified')
        administrative=tags.get('boundary')=='administrative' and tags.get('admin_level') in ('9','10')
        if not road and not administrative:
            raise z.ZoneDataError('补充分割必须为有路线编号的真实道路或真实町丁目边界')
        coordinates=element.get('geometry',[])
        if len(coordinates)<2:continue
        line=transform(z._TO_METERS,LineString([(p['lon'],p['lat']) for p in coordinates]))
        if road and not natural.boundary_kind(tags,length_m=line.length):continue
        lines.append(set_precision(line,.001));retained.append(element['id'])
    tree=STRtree(lines);geometries=[];split_count=0;unchanged=[]
    for cell in doc['road_cells']:
        face=set_precision(shape(cell['metric_geometry']),.001)
        candidates=[lines[int(i)] for i in tree.query(face,predicate='intersects')]
        if not candidates:geometries.append(face);continue
        # split直接保留原面边界，避免独立polygonize/裁剪引入叠加微缝。
        parts=[set_precision(part,.001) for part in split(face,union_all(candidates)).geoms
               if part.geom_type=='Polygon' and not part.is_empty]
        combined=union_all(parts,grid_size=.001)
        if not parts or not coverage_matches(face,combined):
            # 补充分割是可选细化；若节点舍入改变已有面，保留原面并列明未采用，
            # 不把微缝带进基础网格，也不让少量补充线破坏已核验的完整覆盖。
            parts=[face];unchanged.append(cell['id'])
        split_count+=max(0,len(parts)-1);geometries.extend(parts)
    geometries.sort(key=lambda g:(g.centroid.x,g.centroid.y,g.area))
    combined=union_all(geometries,grid_size=.001)
    if not coverage_matches(z.projected(doc['work_area']),combined):
        raise z.ZoneDataError('补充道路切分改变有效作业范围')
    code=doc['region_code']
    cells=[{'id':code+'-N-'+hashlib.sha256(g.normalize().wkb).hexdigest()[:16],
            'metric_geometry':mapping(g),'area_km2':g.area/1e6,'merchant_ids':[],'store_count':0} for g in geometries]
    return {**doc,'road_cells':cells,'snapshot_id':natural.canonical_sha(cells)[:12],
            'natural_audit':{**doc['natural_audit'],'signed_refinement_sha256':natural.canonical_sha(elements),
                             'supplementary_signed_route_ids':retained,'supplementary_split_count':split_count,
                             'supplementary_unchanged_precision_cell_ids':unchanged}}


def exclude_existing(doc,existing):
    """检查点生成后已有新地区入库时，重新按当前正式责任裁剪；不改旧网格。"""
    if not existing:return doc
    tree=STRtree(existing)
    work=set_precision(z.projected(doc['work_area']),.001)
    clip_to_work=natural.land_clipper(work)
    occupied=union_all([existing[int(i)] for i in tree.query(work,predicate='intersects')],grid_size=.001)
    if occupied.is_empty:return doc
    overlap=work.intersection(occupied).area
    cells=[];removed=0.
    for cell in doc['road_cells']:
        geom=set_precision(shape(cell['metric_geometry']),.001)
        hits=tree.query(geom,predicate='intersects')
        result=geom.difference(union_all([existing[int(i)] for i in hits],grid_size=.001)) if len(hits) else geom
        # 上轮局部细分可能带极小数值外溢；所有新面再次限定于同一真实作业域。
        result=clip_to_work(result)
        for part in z.polygons(result):
            if part.is_empty:continue
            cells.append({'id':doc['region_code']+'-N-'+hashlib.sha256(part.normalize().wkb).hexdigest()[:16],
                'metric_geometry':mapping(part),'area_km2':part.area/1e6,'merchant_ids':[],'store_count':0})
    if not cells:raise z.ZoneDataError('地区已完全被现有正式责任范围覆盖')
    clipped=union_all([shape(cell['metric_geometry']) for cell in cells],grid_size=.001)
    expected=work.difference(occupied)
    if not coverage_matches(expected,clipped):raise z.ZoneDataError('按当前责任裁剪后覆盖校验失败')
    return {**doc,'road_cells':cells,'work_area':z.geographic(clipped),'area_km2':clipped.area/1e6,
            'snapshot_id':natural.canonical_sha(cells)[:12],
            'source':{**doc['source'],'additional_existing_responsibility_excluded_km2':overlap/1e6},
            'natural_audit':{**doc['natural_audit'],'additional_existing_responsibility_excluded_km2':overlap/1e6}}
