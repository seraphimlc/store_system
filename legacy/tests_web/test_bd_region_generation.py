"""地区追加只读旧范围；未知地区/重复地区/跨界商户与网格覆盖拒绝。"""
import copy,json,re
import pytest
from shapely.geometry import box,LineString,Point
import app.db as appdb
from app.models import User,BdWorkGrid,BdZoneRelease,BdGridStore
from app.services import bd_natural_grids as natural,bd_zones as z,bd_grid_regions as regions,bd_zone_operations as ops
from app.services.bd_zone_merchants import bind_rows
from tests_web.test_bd_zone_operations import seed,login


def source(region='kanagawa',x=5000,identity='region-shop'):
    line=z.geographic(LineString([(x+500,-100),(x+500,1100)]))
    elements=[{'type':'way','id':1,'tags':{'highway':'primary'},
               'geometry':[{'lon':lon,'lat':lat} for lon,lat in line['coordinates']]}]
    doc=natural.prepare(z.geographic(box(x,0,x+1000,1000)),elements,[],[],
                        captured_at='synthetic',source={},region_code=region)
    lon,lat=z.geographic(Point(x+100,500))['coordinates']
    return natural.candidate(natural.normalise(bind_rows(doc,[{'place_id':identity,'name':identity,'lon':lon,'lat':lat}],{'backend':'synthetic'})))


def test_append_new_region_keeps_existing_and_filters_map(client,seed):
    with appdb.SessionLocal() as db:
        admin=db.get(User,seed['users']['ops-admin'])
        old=db.get(BdWorkGrid,seed['grid']);before=copy.deepcopy(old.geometry)
        result=source()
        release=natural.import_candidate(db,admin,result,append_region=True);db.commit()
        assert db.get(BdWorkGrid,old.id).geometry==before
        assert db.query(BdWorkGrid).count()==2
        assert len(regions.management_grids(db,admin,region='kanagawa'))==1
        assert len(regions.management_grids(db,admin,region='tokyo23'))==1
        assert release.source['region_code']=='kanagawa'
        assert all(cell['id'].startswith('kanagawa-') for cell in result['source_document']['road_cells'])
        assert db.query(BdGridStore).count()==2
        with pytest.raises(ops.OperationError,match='重复追加'):
            natural.import_candidate(db,admin,result,append_region=True)
    login(client,'ops-admin')
    response=client.get('/zones?region=kanagawa&mode=manage')
    assert response.status_code==200
    config=json.loads(re.search('<script id="grid-region-config"[^>]*>(.*?)</script>',response.text,re.S).group(1))
    assert len(config['grids'])==1
    assert all(grid['id']!=seed['grid'] for grid in config['grids'])
    response=client.get('/zones?region=all&mode=manage')
    assert response.status_code==200
    config=json.loads(re.search('<script id="grid-region-config"[^>]*>(.*?)</script>',response.text,re.S).group(1))
    assert len(config['grids'])==2
    assert seed['grid'] in {grid['id'] for grid in config['grids']}
    assert len({grid['release_id'] for grid in config['grids']})==2
    assert re.search(r'<option\s+value="all"[^>]*selected',response.text)
    assert client.get('/zones?region=all&mode=assign').status_code==200
    assert client.get('/zones?region=unknown').status_code==404


@pytest.mark.parametrize('x,identity,reason',[(0,'unique-new','空间重叠'),(5000,'shop-synthetic','商户')])
def test_append_rejects_existing_coverage_or_merchant(seed,x,identity,reason):
    with appdb.SessionLocal() as db:
        admin=db.get(User,seed['users']['ops-admin'])
        with pytest.raises(ops.OperationError,match=reason):
            natural.import_candidate(db,admin,source(x=x,identity=identity),append_region=True)
        assert db.query(BdWorkGrid).count()==1 and db.query(BdZoneRelease).count()==1


def test_append_failed_quality_never_writes(seed):
    with appdb.SessionLocal() as db:
        admin=db.get(User,seed['users']['ops-admin'])
        result=source();result['source_document']['natural_audit']['quality_pass']=False
        result['audit']['quality_pass']=False
        with pytest.raises(ops.OperationError,match='检查未通过'):
            natural.import_candidate(db,admin,result,append_region=True)
        assert db.query(BdWorkGrid).count()==1


def test_region_catalogue_rejects_unknown():
    with pytest.raises(z.ZoneDataError,match='地区'):
        natural.prepare(z.geographic(box(0,0,100,100)),[],[],[],captured_at='',source={},region_code='made-up')


def test_collector_assembles_true_admin_boundaries_and_excludes_small_roads(tmp_path):
    from scripts.bd_collect_regions import collect,TOKYO_CITIES
    import osmium
    path=tmp_path/'regions.osm'
    nodes=''.join(f'<node id="{i}" lat="{lat}" lon="{lon}" version="1"/>' for i,(lon,lat) in enumerate(
        [(139,35),(140,35),(140,36),(139,36)],1))
    way='<way id="10" version="1"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/><nd ref="1"/></way>'
    road='<way id="11" version="1"><nd ref="1"/><nd ref="3"/><tag k="highway" v="primary"/></way>'
    small='<way id="12" version="1"><nd ref="2"/><nd ref="4"/><tag k="highway" v="residential"/></way>'
    relations=[]
    for i,code in enumerate(sorted(TOKYO_CITIES),100):
        relations.append(f'<relation id="{i}" version="1"><member type="way" ref="10" role="outer"/><tag k="type" v="multipolygon"/><tag k="boundary" v="administrative"/><tag k="ref" v="{code}"/></relation>')
    for i,code in enumerate(('JP-11','JP-12','JP-14'),200):
        relations.append(f'<relation id="{i}" version="1"><member type="way" ref="10" role="outer"/><tag k="type" v="multipolygon"/><tag k="boundary" v="administrative"/><tag k="ISO3166-2" v="{code}"/></relation>')
    path.write_text('<osm version="0.6">'+nodes+way+road+small+''.join(relations)+'</osm>')
    destination=tmp_path/'output'
    collect(path,destination)
    docs={code:json.loads((destination/(code+'.source.json')).read_text()) for code in ('tokyo26','saitama','chiba','kanagawa')}
    assert len(docs['tokyo26']['municipalities'])==26
    assert all(len(doc['roads'])==1 and doc['roads'][0]['id']==11 for doc in docs.values())
    assert all(doc['source']['pbf_sha256'] and doc['boundary']['type'] in ('Polygon','MultiPolygon') for doc in docs.values())


def test_real_dividers_split_only_at_declared_source_geometry():
    boundary=z.geographic(box(0,0,2000,1000))
    def element(identity,coords,tags):
        return {'type':'way','id':identity,'tags':tags,
                'geometry':[{'lon':lon,'lat':lat} for lon,lat in z.geographic(LineString(coords))['coordinates']]}
    roads=[element(1,[(1000,-100),(1000,1100)],{'highway':'primary'})]
    river=element(2,[(-100,500),(2100,500)],{'waterway':'river'})
    doc=natural.prepare(boundary,roads,[],[],captured_at='synthetic',source={},region_code='saitama',dividers=[river])
    assert len(doc['road_cells'])==4
    assert doc['natural_audit']['geometric_pass']
    assert doc['natural_audit']['additional_boundaries'][0]['osm_id']==2
    with pytest.raises(z.ZoneDataError,match='来源标签'):
        natural.prepare(boundary,roads,[],[],captured_at='synthetic',source={},region_code='saitama',
                        dividers=[element(3,[(-100,500),(2100,500)],{'highway':'residential'})])


def test_coverage_tolerance_is_confined_to_real_boundary_not_internal_gaps():
    from app.services.bd_grid_geometry import coverage_matches
    from shapely import union_all
    expected=box(0,0,1000,1000)
    assert coverage_matches(expected,box(.001,0,1000,1000))
    assert not coverage_matches(expected,box(.1,0,1000,1000))
    internal_gap=union_all([box(0,0,499.999,1000),box(500.001,0,1000,1000)])
    assert not coverage_matches(expected,internal_gap)


def test_land_clipping_tiles_never_create_generated_boundaries():
    from shapely import union_all
    from shapely.geometry import Polygon
    land=box(-1000,-1000,21000,21000).difference(box(9000,9000,11000,11000))
    clip=natural.land_clipper(land)
    line=LineString([(-2000,10000),(22000,10000)])
    assert clip(line).equals(line.intersection(land))
    inside=LineString([(100,100),(20000,20000)])
    assert clip(inside).equals(inside.intersection(land))
    doc=natural.prepare(z.geographic(land),[{'type':'way','id':1,'tags':{'highway':'primary'},
        'geometry':[{'lon':lon,'lat':lat} for lon,lat in z.geographic(line)['coordinates']]}],
        [],[],captured_at='synthetic',source={},region_code='saitama')
    assert len(doc['road_cells'])==2




def test_nested_blocks_are_one_bounded_grid_and_empty_rounding_sliver_is_audited():
    from shapely.geometry import mapping
    from shapely import union_all
    outer=box(0,0,1600,1600).difference(box(200,200,1400,1400))
    island=box(300,300,1300,1300)
    tiny=box(2000,0,2000.001,0.001)
    geometries=[outer,island,tiny]
    doc={'region_code':'tokyo26','boundary':z.geographic(union_all(geometries)),
         'work_area':z.geographic(union_all(geometries)),'natural_audit':{},
         'road_cells':[{'id':str(i),'metric_geometry':mapping(g),
            'merchant_ids':['store-'+str(i)] if i<2 else [],'store_count':1 if i<2 else 0}
            for i,g in enumerate(geometries)]}
    result=natural.normalise(doc)
    assert result['natural_audit']['quality_pass']
    assert len(result['road_cells'])==1
    assert len(result['road_cells'][0]['merchant_ids'])==2
    assert result['natural_audit']['excluded_numerical_empty_cell_ids']==['2']
    assert result['natural_audit']['normalisation']['natural_block_exceptions'][0]['reason']=='enclosed_complete_block'


def test_coverage_ignores_zero_area_lines_created_by_overlay():
    from shapely.geometry import GeometryCollection
    from app.services.bd_grid_geometry import coverage_matches
    polygon=box(0,0,1000,1000)
    expected=GeometryCollection([polygon,LineString([(1000,1000),(1200,1200)])])
    assert coverage_matches(expected,box(.001,0,1000,1000))
    assert not coverage_matches(expected,box(1,0,1000,1000))


def test_signed_route_refinement_splits_only_real_faces_and_preserves_work_area():
    from app.services.bd_region_refine import refine
    from shapely import union_all
    from shapely.geometry import shape
    result=source(region='kanagawa')
    doc=result['source_document']
    # 不足 2 km² 的干道两侧会先收成一块；新编号干道再把这一块切开。
    line=z.geographic(LineString([(4900,500),(6100,500)]))
    elements=[{'type':'way','id':42,'tags':{'highway':'tertiary','ref':'123','lanes':'1'},
               'geometry':[{'lon':lon,'lat':lat} for lon,lat in line['coordinates']]}]
    refined=refine(doc,elements)
    assert len(doc['road_cells'])==1
    assert len(refined['road_cells'])==2
    assert refined['natural_audit']['supplementary_split_count']==1
    before=union_all([shape(c['metric_geometry']) for c in doc['road_cells']],grid_size=.001)
    after=union_all([shape(c['metric_geometry']) for c in refined['road_cells']],grid_size=.001)
    assert before.equals(after)
    assert all(not c['merchant_ids'] for c in refined['road_cells'])
    bad=copy.deepcopy(elements);bad[0]['tags']={'highway':'residential'}
    with pytest.raises(z.ZoneDataError,match='路线编号'):refine(doc,bad)


@pytest.mark.parametrize('region,area,stores,accepted',[
    ('saitama',7,100,True),('chiba',11,100,False),('kanagawa',7,201,False),('tokyo23',7,100,False)])
def test_rural_natural_blocks_require_all_explicit_limits(region,area,stores,accepted):
    from shapely.geometry import mapping
    geometry=box(0,0,(area*1e6)**.5,(area*1e6)**.5)
    doc={'region_code':region,'boundary':z.geographic(geometry),'work_area':z.geographic(geometry),
         'natural_audit':{},'road_cells':[{'id':'whole','metric_geometry':mapping(geometry),
            'merchant_ids':[str(i) for i in range(stores)],'store_count':stores}]}
    result=natural.normalise(doc)
    assert result['natural_audit']['quality_pass'] is accepted
    if accepted:assert result['natural_audit']['normalisation']['natural_block_exceptions'][0]['reason']=='low_density_rural_block'


def test_road_block_policy_keeps_one_compact_arterial_face():
    from shapely.geometry import mapping
    geometry=box(0,0,5000,4000)
    outer=box(-3000,-3000,8000,7000)
    doc={'region_code':'kanagawa','cover_policy':'road-block','boundary':z.geographic(outer),
         'work_area':z.geographic(geometry),'natural_audit':{},
         'road_cells':[{'id':'block','metric_geometry':mapping(geometry),
            'merchant_ids':[str(i) for i in range(800)],'store_count':800}]}
    result=natural.normalise(doc)
    assert result['natural_audit']['quality_pass']
    assert result['natural_audit']['normalisation']['natural_block_exceptions'][0]['reason']=='indivisible_road_block'
    # 两条干道之间的完整走廊保持一块。只有细到长宽比超过 12 的才拒绝。
    corridor=box(0,0,8000,1000)
    corridor_doc={'region_code':'kanagawa','cover_policy':'road-block',
          'boundary':z.geographic(box(-3000,-3000,11000,4000)),
          'work_area':z.geographic(corridor),'natural_audit':{},
          'road_cells':[{'id':'corridor','metric_geometry':mapping(corridor),'merchant_ids':['shop'],'store_count':1}]}
    corridor_result=natural.normalise(corridor_doc)
    assert corridor_result['natural_audit']['quality_pass']
    assert corridor_result['natural_audit']['normalisation']['natural_block_exceptions'][0]['reason']=='indivisible_road_block'
    hairline=box(0,0,30000,200)
    thin={'region_code':'kanagawa','cover_policy':'road-block',
          'boundary':z.geographic(box(-3000,-3000,33000,3200)),
          'work_area':z.geographic(hairline),'natural_audit':{},
          'road_cells':[{'id':'strip','metric_geometry':mapping(hairline),'merchant_ids':['shop'],'store_count':1}]}
    assert not natural.normalise(thin)['natural_audit']['quality_pass']
    wide_mountain=box(0,0,20000,21600)
    wide_doc={'region_code':'saitama','cover_policy':'road-block',
        'boundary':z.geographic(box(-3000,-3000,23000,24600)),
        'work_area':z.geographic(wide_mountain),'natural_audit':{},
        'road_cells':[{'id':'wide','metric_geometry':mapping(wide_mountain),
            'merchant_ids':[str(i) for i in range(30)],'store_count':30}]}
    wide_result=natural.normalise(wide_doc)
    assert wide_result['natural_audit']['quality_pass']
    assert wide_result['road_cells'][0]['area_km2']==pytest.approx(432)
    huge=box(0,0,26000,26000)
    huge_doc={'region_code':'saitama','cover_policy':'road-block',
        'boundary':z.geographic(box(-3000,-3000,29000,29000)),
        'work_area':z.geographic(huge),'natural_audit':{},
        'road_cells':[{'id':'huge','metric_geometry':mapping(huge),
            'merchant_ids':[str(i) for i in range(30)],'store_count':30}]}
    assert not natural.normalise(huge_doc)['natural_audit']['quality_pass']
    mountain=box(0,0,8000,7500)
    mountain_doc={'region_code':'kanagawa','cover_policy':'road-block',
        'boundary':z.geographic(box(-3000,-3000,11000,10500)),
        'work_area':z.geographic(mountain),'natural_audit':{},
        'road_cells':[{'id':'mountain','metric_geometry':mapping(mountain),
            'merchant_ids':[str(i) for i in range(20)],'store_count':20}]}
    mountain_result=natural.normalise(mountain_doc)
    assert mountain_result['natural_audit']['quality_pass']
    assert mountain_result['natural_audit']['normalisation']['natural_block_exceptions'][0]['reason']=='indivisible_road_block'
    crowded=dict(mountain_doc)
    crowded['road_cells']=[{'id':'crowded','metric_geometry':mapping(mountain),
        'merchant_ids':[str(i) for i in range(4000)],'store_count':4000}]
    assert not natural.normalise(crowded)['natural_audit']['quality_pass']


def test_empty_speck_below_100_square_metres_is_not_its_own_grid():
    from shapely.geometry import mapping
    from shapely import union_all
    block=box(0,0,1000,1000)
    speck=box(2000,0,2010,5)
    work=union_all([block,speck],grid_size=.001)
    doc={'region_code':'kanagawa','cover_policy':'road-block','boundary':z.geographic(work.buffer(50)),
         'work_area':z.geographic(work),'natural_audit':{},
         'road_cells':[
             {'id':'block','metric_geometry':mapping(block),'merchant_ids':['shop'],'store_count':1},
             {'id':'speck','metric_geometry':mapping(speck),'merchant_ids':[],'store_count':0}]}
    result=natural.normalise(doc)
    assert result['natural_audit']['quality_pass']
    assert [cell['merchant_ids'] for cell in result['road_cells']]==[['shop']]
    assert result['natural_audit']['excluded_numerical_empty_cell_ids']==['speck']


def test_precision_gap_under_one_square_metre_joins_the_touching_face():
    from shapely import union_all
    block=box(0,0,100,100)
    gap=box(100,0,100.05,10)
    filled=natural.absorb_precision_gaps([{'geom':block,'members':{0},'count':1}],
                                          union_all([block,gap],grid_size=.001))
    assert filled[0]['geom'].area==pytest.approx(block.area+gap.area)
    wide=box(100,0,110,10)
    unchanged=natural.absorb_precision_gaps([{'geom':block,'members':{0},'count':1}],
                                             union_all([block,wide],grid_size=.001))
    assert unchanged[0]['geom'].area==pytest.approx(block.area)
    left=box(0,0,10,10)
    right=box(9.9,0,20,10)
    trimmed=natural.trim_sliver_overlaps([
        {'geom':left,'members':{0},'count':0},
        {'geom':right,'members':{1},'count':0}])
    merged=union_all([item['geom'] for item in trimmed],grid_size=.001)
    assert abs(sum(item['geom'].area for item in trimmed)-merged.area)<=.01
    assert merged.area==pytest.approx(left.union(right).area)


def test_road_block_absorbs_a_sliver_into_the_neighbouring_face():
    from shapely.geometry import mapping
    from shapely import union_all
    block=box(0,0,5000,4000)
    sliver=box(5000,0,5200,50)
    work=union_all([block,sliver],grid_size=.001)
    doc={'region_code':'kanagawa','cover_policy':'road-block','boundary':z.geographic(work.buffer(100)),
         'work_area':z.geographic(work),'natural_audit':{},
         'road_cells':[
             {'id':'block','metric_geometry':mapping(block),'merchant_ids':['shop'],'store_count':1},
             {'id':'sliver','metric_geometry':mapping(sliver),'merchant_ids':[],'store_count':0}]}
    result=natural.normalise(doc)
    assert result['natural_audit']['quality_pass']
    assert len(result['road_cells'])==1
    assert result['road_cells'][0]['merchant_ids']==['shop']
    crumbs=[box(5000,index*80,5600,index*80+60) for index in range(8)]
    neighbour=box(5000,800,6200,2000)
    wider=union_all([block,*crumbs,neighbour],grid_size=.001)
    mixed={'region_code':'kanagawa','cover_policy':'road-block','boundary':z.geographic(wider.buffer(100)),
           'work_area':z.geographic(wider),'natural_audit':{},
           'road_cells':[
               {'id':'block','metric_geometry':mapping(block),'merchant_ids':['shop'],'store_count':1},
               *[{'id':'crumb-'+str(index),'metric_geometry':mapping(crumb),'merchant_ids':[],'store_count':0}
                 for index,crumb in enumerate(crumbs)],
               {'id':'neighbour','metric_geometry':mapping(neighbour),'merchant_ids':['other'],'store_count':1}]}
    mixed_result=natural.normalise(mixed)
    assert mixed_result['natural_audit']['quality_pass']
    assert len(mixed_result['road_cells'])==2
    assert sorted(cell['merchant_ids'] for cell in mixed_result['road_cells'])==[['other'],['shop']]


@pytest.mark.parametrize('matching_boundary',[True,False])
def test_facility_exception_requires_full_source_boundary_evidence(matching_boundary):
    from shapely.geometry import mapping
    geometry=box(0,0,2500,2200)
    doc={'region_code':'kanagawa','boundary':z.geographic(geometry),'work_area':z.geographic(geometry),
         'natural_audit':{},'road_cells':[{'id':'facility','metric_geometry':mapping(geometry),
             'merchant_ids':[str(i) for i in range(400)],'store_count':400}],
         'facility_boundaries':[{'osm_id':42,'name':'synthetic-airfield',
             'metric_geometry':mapping(geometry if matching_boundary else box(10000,0,12000,2000))}]}
    result=natural.normalise(doc)
    assert result['natural_audit']['quality_pass'] is matching_boundary
    if matching_boundary:
        evidence=result['natural_audit']['normalisation']['natural_block_exceptions'][0]
        assert evidence['reason']=='verified_facility' and evidence['osm_id']==42


def test_real_local_boundary_can_refine_but_unverified_admin_level_cannot():
    from app.services.bd_region_refine import refine
    from shapely.geometry import shape
    from shapely import union_all
    doc=source()['source_document']
    geometry=z.geographic(LineString([(4900,500),(6100,500),(6100,1200),(4900,1200),(4900,500)]))
    element={'type':'way','id':-42,'tags':{'boundary':'administrative','admin_level':'10','name':'synthetic town'},
             'geometry':[{'lon':lon,'lat':lat} for lon,lat in geometry['coordinates']]}
    result=refine(doc,[element])
    assert len(doc['road_cells'])==1
    assert len(result['road_cells'])==2
    assert union_all([shape(c['metric_geometry']) for c in result['road_cells']]).equals(
        union_all([shape(c['metric_geometry']) for c in doc['road_cells']]))
    element['tags']['admin_level']='3'
    with pytest.raises(z.ZoneDataError,match='町丁目'):refine(doc,[element])


def test_assignment_redirect_keeps_generated_region(client,seed):
    from tests.helpers import form_token
    from tests_web.test_bd_zone_operations import csrf
    with appdb.SessionLocal() as db:
        admin=db.get(User,seed['users']['ops-admin'])
        release=natural.import_candidate(db,admin,source(),append_region=True)
        grid=db.query(BdWorkGrid).filter_by(release_id=release.id).first()
        region=regions.merge(db,admin,[{'id':grid.id,'revision':grid.revision,'zone_id':None,'zone_revision':None}],
                             'kanagawa-region')
        db.commit();identity=region.id;revision=region.revision
    login(client,'ops-admin')
    page=client.get(f'/zones?region=kanagawa&zone={identity}')
    response=client.post(f'/zones/{identity}/assign',data={'_ft':form_token(client),
        'csrf_token':csrf(page.text),'revision':revision,'team_id':seed['team']},follow_redirects=False)
    assert response.status_code==303
    assert f'region=kanagawa&zone={identity}' in response.headers['location']


def test_rural_shape_hard_limit_cannot_be_bypassed_by_low_density():
    from shapely.geometry import mapping
    geometry=box(0,0,7000,1000)
    doc={'region_code':'saitama','boundary':z.geographic(geometry),'work_area':z.geographic(geometry),
         'natural_audit':{},'road_cells':[{'id':'long','metric_geometry':mapping(geometry),
            'merchant_ids':['shop'],'store_count':1}]}
    assert not natural.normalise(doc)['natural_audit']['quality_pass']


def test_resumed_checkpoint_respects_newly_imported_neighbour_region():
    from app.services.bd_region_refine import exclude_existing
    from shapely.geometry import shape
    from shapely import union_all
    doc=source()['source_document']
    previous=box(4900,0,5100,1000)
    updated=exclude_existing(doc,[previous])
    combined=union_all([shape(c['metric_geometry']) for c in updated['road_cells']],grid_size=.001)
    assert combined.intersection(previous).area==0
    assert combined.area==pytest.approx(900000)
    assert updated['source']['additional_existing_responsibility_excluded_km2']==pytest.approx(.1)


def test_precision_collapsed_empty_face_is_audited_not_a_fake_land_component():
    from shapely.geometry import mapping
    real=box(0,0,1000,1000)
    tiny=box(2000,0,2000.0001,.0001)
    doc={'region_code':'kanagawa','boundary':z.geographic(real),'work_area':z.geographic(real),
         'natural_audit':{},'road_cells':[
          {'id':'real','metric_geometry':mapping(real),'merchant_ids':['shop'],'store_count':1},
          {'id':'precision-empty','metric_geometry':mapping(tiny),'merchant_ids':[],'store_count':0}]}
    result=natural.normalise(doc)
    assert result['natural_audit']['quality_pass']
    assert result['natural_audit']['geometric_pass']
    assert 'precision-empty' in result['natural_audit']['excluded_empty_cell_ids']
    assert len(result['road_cells'])==1 and result['road_cells'][0]['merchant_ids']==['shop']


def test_internal_region_seam_does_not_make_a_road_block_a_municipality():
    from shapely.geometry import MultiPolygon, mapping
    outer = box(0, 0, 10000, 8000)
    inner = box(2000, 2000, 6000, 6000)
    doc = {'region_code': 'tokyo26', 'cover_policy': 'road-block',
           'boundary': z.geographic(MultiPolygon([outer, inner])),
           'work_area': z.geographic(inner), 'natural_audit': {},
           'road_cells': [{'id': 'inner', 'metric_geometry': mapping(inner),
                           'merchant_ids': ['shop'], 'store_count': 1}]}
    result = natural.normalise(doc)
    assert result['natural_audit']['quality_pass']
    assert result['natural_audit']['normalisation']['natural_block_exceptions'][0]['reason'] == 'indivisible_road_block'
    whole = {'region_code': 'tokyo26', 'cover_policy': 'road-block',
             'boundary': z.geographic(outer), 'work_area': z.geographic(outer), 'natural_audit': {},
             'road_cells': [{'id': 'city', 'metric_geometry': mapping(outer),
                             'merchant_ids': ['shop'], 'store_count': 1}]}
    assert not natural.normalise(whole)['natural_audit']['quality_pass']


def test_city_centre_fragments_merge_into_a_compact_block_until_stores_or_shape_stop():
    from shapely.geometry import mapping
    from shapely import union_all
    blocks=[box(x*900,y*900,(x+1)*900,(y+1)*900) for y in range(2) for x in range(2)]
    work=union_all(blocks,grid_size=.001)
    def doc_for(store_count):
        cells=[]
        for index,geom in enumerate(blocks):
            cells.append({'id':'b'+str(index),'metric_geometry':mapping(geom),
                          'merchant_ids':[str(index)+'-'+str(n) for n in range(store_count)],
                          'store_count':store_count})
        return {'region_code':'tokyo26','cover_policy':'road-block','boundary':z.geographic(work.buffer(50)),
                'work_area':z.geographic(work),'natural_audit':{},'road_cells':cells}
    compact=natural.normalise(doc_for(10))
    assert compact['natural_audit']['quality_pass']
    assert len(compact['road_cells'])==1
    assert compact['road_cells'][0]['area_km2']==pytest.approx(3.24)
    assert compact['natural_audit']['normalisation']['natural_block_exceptions'][0]['reason']=='urban_compact_block'
    crowded=natural.normalise(doc_for(2000))
    assert crowded['natural_audit']['quality_pass']
    assert len(crowded['road_cells'])==2
    assert max(cell['store_count'] for cell in crowded['road_cells'])<=5000
    packed=natural.normalise(doc_for(3000))
    assert packed['natural_audit']['quality_pass']
    assert len(packed['road_cells'])==4
    row=[box(index*800,0,(index+1)*800,800) for index in range(6)]
    ribbon=union_all(row,grid_size=.001)
    ribbon_doc={'region_code':'tokyo26','cover_policy':'road-block','boundary':z.geographic(ribbon.buffer(50)),
                'work_area':z.geographic(ribbon),'natural_audit':{},
                'road_cells':[{'id':'r'+str(index),'metric_geometry':mapping(geom),
                               'merchant_ids':['s'+str(index)],'store_count':1} for index,geom in enumerate(row)]}
    ribbon_result=natural.normalise(ribbon_doc)
    assert ribbon_result['natural_audit']['quality_pass']
    assert len(ribbon_result['road_cells'])==2


def test_small_inhabited_riverbank_merges_into_qualified_rural_neighbour():
    from shapely.geometry import mapping
    from shapely import union_all
    large=box(0,0,2400,2250)
    small=box(2400,1000,2440,1300)
    doc={'region_code':'chiba','boundary':z.geographic(union_all([large,small])),
         'work_area':z.geographic(union_all([large,small])),'natural_audit':{},
         'road_cells':[{'id':str(i),'metric_geometry':mapping(geometry),
                       'merchant_ids':[str(i)],'store_count':1} for i,geometry in enumerate((large,small))]}
    result=natural.normalise(doc)
    assert result['natural_audit']['quality_pass']
    assert len(result['road_cells'])==1 and result['road_cells'][0]['store_count']==2
    assert result['road_cells'][0]['area_km2']==pytest.approx(5.412)
