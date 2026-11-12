import initGeos from 'geos-wasm';
import proj4 from 'proj4';

export type GridPoint=[number,number];
export type GridPolygon={type:'Polygon';coordinates:GridPoint[][]};
export type GridMultiPolygon={type:'MultiPolygon';coordinates:GridPoint[][][]};
export type GridGeometry=GridPolygon|GridMultiPolygon;
type Collection={type:'GeometryCollection';geometries:(GridGeometry|Collection)[]};
export type JsonGeometry=GridGeometry|Collection|{type:'LineString';coordinates:GridPoint[]}|{type:'MultiLineString';coordinates:GridPoint[][]}|{type:'Point';coordinates:GridPoint}|{type:'MultiPoint';coordinates:GridPoint[]};
export class GridGeometryError extends Error{
 constructor(public readonly code:'INVALID_GRID_GEOMETRY'|'GRID_NOT_CONNECTED'|'GRID_OVERLAP'|'GEOMETRY_ENGINE',message:string){super(message);}
}
const invalid=()=>new GridGeometryError('INVALID_GRID_GEOMETRY','网格边界无效，请重新生成');
const projection=proj4('EPSG:4326','+proj=tmerc +lat_0=36 +lon_0=139.833333333333 +k=0.9999 +x_0=0 +y_0=0 +ellps=GRS80 +units=m +no_defs');
let engine:ReturnType<typeof initGeos>|undefined;
function geos(){return engine??=initGeos({errorHandler:()=>{},noticeHandler:()=>{}}).catch(error=>{engine=undefined;throw error;});}
function validate(value:GridGeometry,geographic=false){
 if(!value||!['Polygon','MultiPolygon'].includes(value.type)||!Array.isArray(value.coordinates))throw invalid();
 const polygons=value.type==='Polygon'?[value.coordinates]:value.coordinates;
 if(!polygons.length)throw invalid();
 for(const rings of polygons){if(!Array.isArray(rings)||!rings.length)throw invalid();for(const ring of rings){
  if(!Array.isArray(ring)||ring.length<4)throw invalid();
  for(const point of ring)if(!Array.isArray(point)||point.length!==2||!point.every(Number.isFinite)||(geographic&&(Math.abs(point[0])>180||Math.abs(point[1])>90)))throw invalid();
  if(ring[0][0]!==ring.at(-1)![0]||ring[0][1]!==ring.at(-1)![1])throw invalid();
 }}
}
function transform(value:GridGeometry,point:(p:GridPoint)=>GridPoint):GridGeometry{
 if(value.type==='Polygon')return {type:'Polygon',coordinates:value.coordinates.map(ring=>ring.map(point))};
 return {type:'MultiPolygon',coordinates:value.coordinates.map(rings=>rings.map(ring=>ring.map(point)))};
}
export function gridPointToMetric(point:GridPoint){return projection.forward(point) as GridPoint;}
export function gridPointToGeographic(point:GridPoint){return projection.inverse(point) as GridPoint;}
export function gridToMetric(value:GridGeometry){validate(value,true);return transform(value,p=>projection.forward(p) as GridPoint);}
export function gridToGeographic(value:GridGeometry){validate(value);return transform(value,p=>projection.inverse(p) as GridPoint);}
function polygonParts(value:GridGeometry|Collection):GridPolygon[]{
 if(value.type==='Polygon')return [value];
 if(value.type==='MultiPolygon')return value.coordinates.map(coordinates=>({type:'Polygon',coordinates}));
 return value.geometries.flatMap(polygonParts);
}

/** Owned GEOS arena after initialization; async streams retain resources until completion. No native geometry escapes. */
export async function arena<T>(operation:(scope:Scope)=>T|Promise<T>):Promise<T>{
 const g=await geos(),owned=new Set<number>(),reader=g.GEOSGeoJSONReader_create(),writer=g.GEOSGeoJSONWriter_create(),out=g.Module._malloc(8);
 function own(pointer:number){if(!pointer)throw new GridGeometryError('GEOMETRY_ENGINE','几何计算失败');owned.add(pointer);return pointer;}
 function read(value:JsonGeometry){const text=JSON.stringify(value),size=text.length*4+1,pointer=g.Module._malloc(size);try{g.Module.stringToUTF8(text,pointer,size);return own(g.GEOSGeoJSONReader_readGeometry(reader,pointer));}finally{g.Module._free(pointer);}}
 function json(pointer:number):JsonGeometry{const text=g.GEOSGeoJSONWriter_writeGeometry(writer,pointer,-1);if(!text)throw invalid();try{return JSON.parse(g.Module.UTF8ToString(text));}finally{g.GEOSFree(text);}}
 function scalar(fn:(p:number,out:number)=>number,p:number){if(fn(p,out)!==1)throw invalid();return g.Module.getValue(out,'double');}
 const scope={g,own,read,json,release:(p:number)=>{if(owned.delete(p))g.GEOSGeom_destroy(p);},area:(p:number)=>scalar(g.GEOSArea,p),length:(p:number)=>scalar(g.GEOSLength,p)};
 try{return await operation(scope);}finally{for(const pointer of owned)g.GEOSGeom_destroy(pointer);g.GEOSGeoJSONReader_destroy(reader);g.GEOSGeoJSONWriter_destroy(writer);g.Module._free(out);}
}
export type Scope={g:Awaited<ReturnType<typeof initGeos>>;own:(p:number)=>number;read:(v:JsonGeometry)=>number;json:(p:number)=>JsonGeometry;release:(p:number)=>void;area:(p:number)=>number;length:(p:number)=>number};
export function union(s:Scope,values:number[],precision=.001){
 const clones:number[]=[],buffer=s.g.Module._malloc(4*values.length);let collection=0;
 try{
  for(const p of values){const clone=s.g.GEOSGeom_clone(p);if(!clone)throw invalid();clones.push(clone);}
  s.g.Module.HEAPU32.set(clones,buffer/4);
  collection=s.g.GEOSGeom_createCollection(7,buffer,clones.length);
  if(!collection)throw invalid();
  s.own(collection);return s.own(s.g.GEOSUnaryUnionPrec(collection,precision));
 }finally{if(!collection)for(const clone of clones)s.g.GEOSGeom_destroy(clone);s.g.Module._free(buffer);}
}
function outline(s:Scope,p:number){
 const pieces=polygonParts(s.json(p) as GridGeometry|Collection).map(poly=>s.read({type:'Polygon',coordinates:[poly.coordinates[0]]}));
 return union(s,pieces);
}
function riverConnected(s:Scope,land:number,rivers:number|undefined){
 if(s.g.GEOSGeomTypeId(land)===3)return true;
 if(!rivers||s.g.GEOSisEmpty(rivers)!==0)return false;
 const parts=polygonParts(s.json(land) as GridMultiPolygon).map(part=>({geometry:s.read(part),
  minX:Math.min(...part.coordinates[0].map(p=>p[0])),maxX:Math.max(...part.coordinates[0].map(p=>p[0])),
  minY:Math.min(...part.coordinates[0].map(p=>p[1])),maxY:Math.max(...part.coordinates[0].map(p=>p[1]))})).sort((a,b)=>a.minX-b.minX);
 if(!parts.length)return false;
 const graph=parts.map(()=>new Set<number>());let buffered:number|undefined;
 for(let i=0;i<parts.length;i++)for(let j=i+1;j<parts.length;j++){
  if(parts[j].minX-parts[i].maxX>100)break;
  const dx=Math.max(0,parts[j].minX-parts[i].maxX,parts[i].minX-parts[j].maxX),dy=Math.max(0,parts[i].minY-parts[j].maxY,parts[j].minY-parts[i].maxY);
  if(Math.hypot(dx,dy)>100)continue;
  const sequence=s.g.GEOSNearestPoints(parts[i].geometry,parts[j].geometry);if(!sequence)throw invalid();
  // GEOSGeom_createLineString takes ownership of the coordinate sequence.
  let crossing:number;try{crossing=s.own(s.g.GEOSGeom_createLineString(sequence));}catch(error){s.g.GEOSCoordSeq_destroy(sequence);throw error;}
  const width=s.length(crossing);
  // Legacy STRtree dwithin rejects >100m before the 1mm tolerance in river_crossing.
  if(width<=.01||width>100)continue;
  let covered=s.g.GEOSCovers(rivers,crossing);
  if(covered===2)throw invalid();
  if(!covered){buffered??=s.own(s.g.GEOSBuffer(rivers,.01,16));covered=s.g.GEOSCovers(buffered,crossing);if(covered===2)throw invalid();}
  if(covered){graph[i].add(j);graph[j].add(i);}
 }
 const reached=new Set([0]),pending=[0];while(pending.length){for(const next of graph[pending.pop()!])if(!reached.has(next)){reached.add(next);pending.push(next);}}
 return reached.size===parts.length;
}
export function shapeMetrics(s:Scope,p:number){
 if(s.g.GEOSGeomTypeId(p)!==3||s.g.GEOSisEmpty(p)!==0)return {solidity:0,aspect:Infinity,compactness:0,acceptable:false};
 const shell=outline(s,p),area=s.area(shell),hull=s.own(s.g.GEOSConvexHull(shell)),rectangle=s.own(s.g.GEOSMinimumRotatedRectangle(hull));
 const corners=(s.json(rectangle) as GridPolygon).coordinates[0];
 const sides=corners.slice(0,4).map((point,i)=>Math.hypot(point[0]-corners[i+1][0],point[1]-corners[i+1][1]));
 const solidity=area/Math.max(s.area(hull),1),aspect=Math.max(...sides)/Math.max(Math.min(...sides),.001),perimeter=s.length(shell),compactness=4*Math.PI*area/Math.max(perimeter**2,1);
 const eroded=s.own(s.g.GEOSBuffer(shell,-Math.min(150,Math.sqrt(area)*.06),16));
 const substantial=polygonParts(s.json(eroded) as GridGeometry|Collection).filter(poly=>s.area(s.read(poly))>area*.04).length;
 return {solidity,aspect,compactness,acceptable:solidity>=.65-1e-5&&aspect<=3+1e-5&&substantial<=1};
}
export function normalizedWkb(s:Scope,geometry:number){
 // JSON roundtrip canonicalizes signed zero, whose sign differs across projection runtimes.
 const p=s.read(s.json(geometry));
 if(s.g.GEOSNormalize(p)!==0)throw invalid();
 const writer=s.g.GEOSWKBWriter_create(),size=s.g.Module._malloc(4);let bytes=0;
 try{s.g.GEOSWKBWriter_setByteOrder(writer,1);s.g.GEOSWKBWriter_setOutputDimension(writer,2);s.g.GEOSWKBWriter_setIncludeSRID(writer,0);
  bytes=s.g.GEOSWKBWriter_write(writer,p,size);if(!bytes)throw invalid();const length=s.g.Module.getValue(size,'i32');
  return Array.from(s.g.Module.HEAPU8.subarray(bytes,bytes+length),byte=>byte.toString(16).padStart(2,'0')).join('');
 }finally{if(bytes)s.g.GEOSFree(bytes);s.g.Module._free(size);s.g.GEOSWKBWriter_destroy(writer);}
}
export interface GridMergeGeometry{metric:GridGeometry;geometry:GridGeometry;displayGeometry:GridGeometry;areaM2:number;normalizedWkbHex:string;shape:{solidity:number;aspect:number;compactness:number;acceptable:boolean}}
/** Metric inputs already use EPSG:6677. River evidence must be tagged river faces, never centerlines. */
export async function mergeMetricGrids(grids:GridGeometry[],riverFaces:GridGeometry[]=[]):Promise<GridMergeGeometry>{
 if(!Array.isArray(grids)||grids.length===0||grids.length>5000||!Array.isArray(riverFaces))throw invalid();
 return arena(s=>{
  const normalized=grids.map(geometry=>{validate(geometry);const raw=s.read(geometry);if(s.g.GEOSisValid(raw)!==1||s.g.GEOSisEmpty(raw)!==0)throw invalid();return s.own(s.g.GEOSGeom_setPrecision(s.own(s.g.GEOSMakeValid(raw)),.001,0));});
  const combined=union(s,normalized);if(s.g.GEOSisValid(combined)!==1||s.g.GEOSisEmpty(combined)!==0||![3,6].includes(s.g.GEOSGeomTypeId(combined)))throw invalid();
  const land=outline(s,combined);
  const rivers=s.g.GEOSGeomTypeId(land)!==3&&riverFaces.length?union(s,riverFaces.map(face=>{validate(face);const p=s.read(face);if(s.g.GEOSisValid(p)!==1)throw invalid();return p;})):undefined;
  if(!riverConnected(s,land,rivers))throw new GridGeometryError('GRID_NOT_CONNECTED','网格必须共边连续或隔不超过100米的已知河道；不能跨普通空白、大水域或仅连接角点');
  const area=s.area(combined);
  if(normalized.reduce((sum,p)=>sum+s.area(p),0)-area>.01)throw new GridGeometryError('GRID_OVERLAP','网格存在面积重叠，不能合并');
  const metric=s.json(combined) as GridGeometry;
  return {metric,geometry:gridToGeographic(metric),displayGeometry:gridToGeographic(s.json(land) as GridGeometry),areaM2:area,normalizedWkbHex:normalizedWkb(s,combined),shape:shapeMetrics(s,combined)};
 });
}
/** Reject invalid geographic topology before projection, exactly as legacy merged_geometry. */
export async function mergeGrids(grids:GridGeometry[],riverFaces:GridGeometry[]=[]){
 if(!Array.isArray(grids)||!grids.length||grids.length>5000)throw invalid();
 await arena(s=>{for(const grid of grids){validate(grid,true);if(s.g.GEOSisValid(s.read(grid))!==1)throw invalid();}});
 return mergeMetricGrids(grids.map(gridToMetric),riverFaces.map(gridToMetric));
}
/** Development acceptance utility: compare geometry coverage with the legacy oracle, in square metres. */
export async function gridCoverageDifference(a:GridGeometry,b:GridGeometry){return arena(s=>s.area(s.own(s.g.GEOSSymDifference(s.read(a),s.read(b)))));}

export interface OsmWaterElement{type:string;id:number;tags?:Record<string,string>;geometry?:{lon:number;lat:number}[];members?:{type:string;role?:string;geometry?:{lon:number;lat:number}[]}[]}
/** Legacy exclusions + relation polygonize, after the strict tagged-river filter. Returns EPSG:6677. */
export async function osmRiverFaces(elements:OsmWaterElement[]):Promise<GridGeometry[]>{
 return arena(s=>{
  const elementsById=new Map<string,OsmWaterElement>();
  for(const element of elements){const tags=element.tags??{};if(tags.water==='river'||tags.waterway==='riverbank')elementsById.set(element.type+':'+element.id,element);}
  const faces:number[]=[];
  function polygonize(lines:number[]){
   if(!lines.length)return s.read({type:'Polygon',coordinates:[]});
   const noded=union(s,lines,0),buffer=s.g.Module._malloc(4);
   try{s.g.Module.HEAPU32[buffer/4]=noded;return union(s,[s.own(s.g.GEOSPolygonize(buffer,1))],0);}finally{s.g.Module._free(buffer);}
  }
  for(const element of elementsById.values()){
   const tags=element.tags??{};if(tags.natural!=='water'&&tags.landuse!=='reservoir'&&tags.waterway!=='riverbank')continue;
   let raw:number;
   if(element.type==='relation'){
    const outer:number[]=[],inner:number[]=[];
    for(const member of element.members??[]){if(member.type!=='way'||(member.geometry?.length??0)<2)continue;
     const points=member.geometry!.map(p=>[p.lon,p.lat] as GridPoint);if(points.some(p=>!p.every(Number.isFinite)))throw invalid();
     (member.role==='inner'?inner:outer).push(s.read({type:'LineString',coordinates:points}));
    }
    raw=s.own(s.g.GEOSDifference(polygonize(outer),polygonize(inner)));
   }else{
    const points=(element.geometry??[]).map(p=>[p.lon,p.lat] as GridPoint);
    if(points.length<4||points[0][0]!==points.at(-1)![0]||points[0][1]!==points.at(-1)![1])continue;
    raw=s.read({type:'Polygon',coordinates:[points]});
   }
   if(s.g.GEOSisEmpty(raw)===1)continue;
   const projected=s.read(gridToMetric(s.json(raw) as GridGeometry));
   const fixed=s.own(s.g.GEOSGeom_setPrecision(s.own(s.g.GEOSMakeValid(projected)),.001,0));faces.push(fixed);
  }
  if(!faces.length)return [];
  return polygonParts(s.json(union(s,faces)) as GridGeometry|Collection);
 });
}
/** For server-side river snapshots already projected with the legacy precision pipeline. */
export async function mergeGridsWithMetricRivers(grids:GridGeometry[],rivers:GridGeometry[]){
 if(!Array.isArray(grids)||!grids.length||grids.length>5000)throw invalid();
 await arena(s=>{for(const grid of grids){validate(grid,true);if(s.g.GEOSisValid(s.read(grid))!==1)throw invalid();}});
 return mergeMetricGrids(grids.map(gridToMetric),rivers);
}
