import json
from pathlib import Path
from shapely.geometry import shape,Polygon,MultiPolygon,mapping
from shapely.ops import unary_union,transform
from shapely.strtree import STRtree
from shapely.validation import explain_validity
from pyproj import Transformer
root=Path(__file__).parent;data=json.loads((root/'production-source.json').read_text());project=Transformer.from_crs(4326,6677,always_xy=True).transform
area=lambda g:transform(project,g).area
holes=lambda g:sum(len(p.interiors) for p in ([g] if g.geom_type=='Polygon' else g.geoms))
def fill(g):
 ps=[g] if g.geom_type=='Polygon' else list(g.geoms);out=unary_union([Polygon(p.exterior) for p in ps]);assert out.is_valid and out.geom_type in ['Polygon','MultiPolygon'];assert out.covers(g);return out
old=[shape(r['geometry']) for r in data['grids']];new=[fill(g) for g in old];tree=STRtree(new);conflicts=[]
for i,g in enumerate(new):
 for j in tree.query(g,predicate='intersects'):
  j=int(j)
  if j<=i:continue
  a=area(g.intersection(new[j]));b=area(old[i].intersection(old[j]))
  if a-b>.01:conflicts.append({'a':data['grids'][i]['id'],'b':data['grids'][j]['id'],'addedOverlapM2':a-b})
gridpatch=[]
for r,a,b in zip(data['grids'],old,new):
 if holes(a):gridpatch.append({'id':r['id'],'geometry':mapping(b),'holesRemoved':holes(a),'addedAreaM2':area(b)-area(a)})
regions=[];regionpatch=[]
for r in data['regions']:
 if r['state']=='archived':continue
 indices=[i for i,g in enumerate(data['grids']) if g['zone_id']==r['id']];assert indices,('empty region',r['id'])
 original=shape(r['geometry']);filled=fill(original);members=unary_union([new[i] for i in indices]);missing=area(members.difference(filled));extra=area(filled.difference(members));regions.append({'id':r['id'],'members':len(indices),'missingM2':missing,'extraM2':extra,'holesRemoved':holes(original),'valid':filled.is_valid})
 if holes(original):regionpatch.append({'id':r['id'],'geometry':mapping(filled)})
ids={r['id'] for r in data['regions']};orphans=[r['id'] for r in data['grids'] if r['zone_id'] is not None and r['zone_id'] not in ids]
report={'grids':len(old),'changedGrids':len(gridpatch),'holesRemoved':sum(p['holesRemoved'] for p in gridpatch),'bytesBefore':sum(len(json.dumps(r['geometry'],separators=(',',':'))) for r in data['grids']),'bytesAfter':sum(len(json.dumps(mapping(g),separators=(',',':'))) for g in new),'addedOverlap':conflicts,'invalidOriginalGridIds':[r['id'] for r,g in zip(data['grids'],old) if not g.is_valid],'unassignedGrids':sum(r['zone_id'] is None for r in data['grids']),'orphanGridIds':orphans,'activeRegions':len(regions),'changedRegions':len(regionpatch),'incompleteRegions':[r for r in regions if r['missingM2']>.1],'regions':regions}
(root/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));(root/'patch.json').write_text(json.dumps({'grids':gridpatch,'regions':regionpatch},separators=(',',':')));assert not conflicts and not orphans and not report['incompleteRegions']
print(json.dumps({k:v for k,v in report.items() if k!='regions'},ensure_ascii=False))
