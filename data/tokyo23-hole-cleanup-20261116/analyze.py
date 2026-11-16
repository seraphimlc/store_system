import json
from pathlib import Path
from shapely.geometry import shape,Polygon,MultiPolygon,mapping
from shapely.ops import transform,unary_union
from shapely.strtree import STRtree
from shapely import make_valid
from pyproj import Transformer
root=Path(__file__).parent;backup=sorted(root.glob('before-*.json'))[-1];data=json.loads(backup.read_text());project=Transformer.from_crs(4326,6677,always_xy=True).transform
old=[shape(g['geometry']) for g in data['grids']];new=[];stats=[];invalid=[]
for row,g in zip(data['grids'],old):
 ps=[g] if g.geom_type=='Polygon' else list(g.geoms);clean=[Polygon(p.exterior) for p in ps];v=clean[0] if g.geom_type=='Polygon' else MultiPolygon(clean)
 if not g.is_valid or not v.is_valid:invalid.append(row['id'])
 assert all(list(a.exterior.coords)==list(b.exterior.coords) for a,b in zip(ps,clean))
 new.append(v);holes=sum(len(p.interiors) for p in ps)
 stats.append({'id':row['id'],'holes':holes,'bytesBefore':len(json.dumps(row['geometry'],separators=(',',':')).encode()),'bytesAfter':len(json.dumps(mapping(v),separators=(',',':')).encode())})
tree=STRtree(new);collisions=[]
for i,g in enumerate(new):
 for j in tree.query(g,predicate='intersects'):
  j=int(j)
  if j<=i:continue
  overlap=g.intersection(new[j]);area=transform(project,overlap).area
  if area>.01:collisions.append({'a':data['grids'][i]['id'],'b':data['grids'][j]['id'],'areaM2':round(area,3),'beforeM2':round(transform(project,old[i].intersection(old[j])).area,3)})
regions=[];regionids={r['id']:r for r in data['regions']};orphans=[g['id'] for g in data['grids'] if g['zone_id'] is not None and g['zone_id'] not in regionids];candidates=[]
for r in data['regions']:
 if r['state']=='archived':continue
 members=[i for i,g in enumerate(data['grids']) if g['zone_id']==r['id']];original=shape(r['geometry']);union=unary_union([new[i] for i in members]);oldunion=unary_union([old[i] for i in members]);parts=[original] if original.geom_type=='Polygon' else list(original.geoms);cleanparts=[Polygon(p.exterior) for p in parts];filled=cleanparts[0] if original.geom_type=='Polygon' else MultiPolygon(cleanparts)
 safe=filled if filled.is_valid else make_valid(filled);originalsafe=original if original.is_valid else make_valid(original)
 missing=transform(project,union.difference(safe)).area;extra=transform(project,safe.difference(union)).area
 regions.append({'id':r['id'],'name':r['name'],'members':len(members),'holes':sum(len(p.interiors) for p in parts),'valid':filled.is_valid,'missingMembersM2':round(missing,3),'extraBeyondMembersM2':round(extra,3),'originalMissingM2':round(transform(project,oldunion.difference(originalsafe)).area,3),'components':len(union.geoms) if union.geom_type=='MultiPolygon' else 1,'memberStates':list(set(data['grids'][i]['state'] for i in members))})
 candidates.append({'id':r['id'],'geometry':mapping(filled)})
report={'backup':str(backup),'grids':len(old),'changed':sum(s['holes']>0 for s in stats),'holesRemoved':sum(s['holes'] for s in stats),'bytesBefore':sum(s['bytesBefore'] for s in stats),'bytesAfter':sum(s['bytesAfter'] for s in stats),'invalid':invalid,'overlaps':collisions,'orphanGridIds':orphans,'unassigned':sum(g['zone_id'] is None for g in data['grids']),'regions':regions}
(root/'analysis.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));(root/'candidates.json').write_text(json.dumps({'grids':[{'id':r['id'],'geometry':mapping(g)} for r,g in zip(data['grids'],new)],'regions':candidates},separators=(',',':')))
print(json.dumps({k:v for k,v in report.items() if k!='regions'},ensure_ascii=False));print(json.dumps({'activeRegions':len(regions),'empty':[r for r in regions if not r['members']],'incomplete':[r for r in regions if r['missingMembersM2']>.1],'invalidRegions':[r for r in regions if not r['valid']],'regionsWithExtra':sum(r['extraBeyondMembersM2']>.1 for r in regions)},ensure_ascii=False))
