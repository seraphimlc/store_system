import json,gzip,hashlib
from pathlib import Path
from shapely.geometry import shape,mapping,Polygon,MultiPolygon
from shapely.ops import transform
from pyproj import Transformer
from PIL import Image,ImageDraw,ImageFont
root=Path(__file__).parent;source=json.loads((root/'source.json').read_text());project=Transformer.from_crs(4326,6677,always_xy=True).transform
compact=lambda v:json.dumps(v,ensure_ascii=False,separators=(',',':')).encode()
fonts=list(Path('/System/Library/Fonts').rglob('*PingFang*'))+list(Path('/System/Library/Fonts').rglob('*Hiragino Sans GB*'))
font=lambda n:ImageFont.truetype(str(fonts[0]),n) if fonts else ImageFont.load_default(size=n)
im=Image.new('RGB',(1800,1530),'#f8fafc');d=ImageDraw.Draw(im)
d.text((35,20),'网格内部孔洞清理 · 本地离线对照（不修改正式数据）',font=font(32),fill='#0f172a')
reports=[];features=[]
for idx,row in enumerate(source['grids']):
 old=shape(row['geometry']);parts=list(old.geoms) if old.geom_type=='MultiPolygon' else [old];cleanparts=[Polygon(p.exterior) for p in parts];new=MultiPolygon(cleanparts) if old.geom_type=='MultiPolygon' else cleanparts[0];candidate=mapping(new);added=new.difference(old)
 assert old.is_valid and new.is_valid
 assert all(list(a.exterior.coords)==list(b.exterior.coords) for a,b in zip(parts,cleanparts))
 assert new.covers(old)
 collisions=[]
 for other in source['neighbors']:
  if other['id']==row['id']:continue
  geom=shape(other['geometry'])
  if not added.intersects(geom):continue
  overlap=transform(project,added.intersection(geom)).area
  if overlap>.01:collisions.append({'gridId':other['id'],'overlapM2':round(overlap,3)})
 count=lambda ps:sum(len(p.exterior.coords)+sum(len(h.coords) for h in p.interiors) for p in ps)
 before=compact(row['geometry']);after=compact(candidate);area=transform(project,old).area;newarea=transform(project,new).area
 r={'id':row['id'],'name':row['name'],'sourceRevision':row['revision'],'zoneId':row['zone_id'],'holesBefore':sum(len(p.interiors) for p in parts),'holesAfter':0,'verticesBefore':count(parts),'verticesAfter':count(cleanparts),'jsonBytesBefore':len(before),'jsonBytesAfter':len(after),'gzipBytesBefore':len(gzip.compress(before,mtime=0)),'gzipBytesAfter':len(gzip.compress(after,mtime=0)),'bytesReductionPercent':round((1-len(after)/len(before))*100,2),'geometryAreaM2Before':round(area,3),'geometryAreaM2After':round(newarea,3),'filledAreaM2':round(newarea-area,3),'areaIncreasePercent':round((newarea/area-1)*100,2),'neighborOverlaps':collisions,'exteriorCoordinatesUnchanged':True,'valid':True,'sourceSha256':hashlib.sha256(before).hexdigest()};reports.append(r)
 features.append({'type':'Feature','id':row['id'],'properties':{'name':row['name'],'previewOnly':True,'sourceRevision':row['revision']},'geometry':candidate})
 y=85+idx*475;d.text((35,y),row['name']+'  #'+str(row['id']),font=font(25),fill='#0f172a')
 metricold=transform(project,old);metricnew=transform(project,new);xmin,ymin,xmax,ymax=metricnew.bounds
 for col,geom in enumerate([metricold,metricnew]):
  box=(35+col*880,y+47,865+col*880,y+382);d.rounded_rectangle(box,radius=12,fill='white',outline='#cbd5e1',width=2)
  d.text((box[0]+14,box[1]+10),'清理前' if col==0 else '清理后（仅外轮廓）',font=font(21),fill='#475569')
  scale=min(740/(xmax-xmin),255/(ymax-ymin));cx=(box[0]+box[2])/2;cy=(box[1]+box[3])/2+16
  coords=lambda ring:[(cx+(x-(xmin+xmax)/2)*scale,cy-(z-(ymin+ymax)/2)*scale) for x,z in ring.coords]
  ps=list(geom.geoms) if geom.geom_type=='MultiPolygon' else [geom]
  for poly in ps:
   d.polygon(coords(poly.exterior),fill='#e0f2fe');d.line(coords(poly.exterior),fill='#a21caf',width=3)
   for hole in poly.interiors:d.polygon(coords(hole),fill='white');d.line(coords(hole),fill='#a21caf',width=2)
 d.text((40,y+397),f"孔洞 {r['holesBefore']} → 0   坐标点 {r['verticesBefore']} → {r['verticesAfter']}   JSON {len(before)/1000:.2f} → {len(after)/1000:.2f} KB（减少 {r['bytesReductionPercent']}%）",font=font(21),fill='#334155')
 d.text((40,y+429),f"填平面积 {r['filledAreaM2']:,.0f} m²（+{r['areaIncreasePercent']}%） · 相邻网格正面积重叠 {len(collisions)} 个 · 外轮廓原样保留",font=font(21),fill='#334155')
(root/'report.json').write_text(json.dumps({'method':'drop-interior-rings-only','databaseModified':False,'metricCRS':'EPSG:6677','grids':reports},ensure_ascii=False,indent=2));(root/'candidate.geojson').write_bytes(compact({'type':'FeatureCollection','features':features}));im.save(root/'comparison.png')
print(json.dumps(reports,ensure_ascii=False,indent=2))
