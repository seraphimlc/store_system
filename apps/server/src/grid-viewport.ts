import {createHash} from 'node:crypto';
import type {PoolClient} from 'pg';
import {z} from 'zod';
import {BusinessError} from './errors';
import {gridAssetsSelect} from './grid-assets-query';
const value=z.coerce.number().finite();
const schema=z.object({west:value.min(-180).max(180),east:value.min(-180).max(180),south:value.min(-85).max(85),north:value.min(-85).max(85),
 region:z.enum(['tokyo23','tokyo26','kanagawa','saitama','chiba','all']).default('tokyo23'),q:z.string().trim().max(128).default(''),state:z.enum(['','ready','disabled']).default(''),
 gridId:z.coerce.number().int().positive().max(2147483647).optional(),zoneId:z.coerce.number().int().positive().optional(),after:z.coerce.number().int().min(0).max(2147483647).default(0),limit:z.coerce.number().int().min(1).max(100).default(100)}).strict();
const PAYLOAD_BUDGET=2*1024*1024;
type Geometry={type:string;coordinates:unknown};
type Metadata={fingerprint:string;hash:string;bytes:number;west:number;east:number;south:number;north:number};
// Only immutable, content-derived geometry metadata; authorization and task state are never cached.
const derived=new Map<string,Metadata>();
function metadata(geometry:Geometry,fingerprint:string):Metadata{
 let west=Infinity,east=-Infinity,south=Infinity,north=-Infinity;
 function points(v:unknown){if(!Array.isArray(v))return;if(typeof v[0]==='number'&&typeof v[1]==='number'){west=Math.min(west,v[0]);east=Math.max(east,v[0]);south=Math.min(south,v[1]);north=Math.max(north,v[1]);}else for(const p of v)points(p);}
 if(['Polygon','MultiPolygon'].includes(geometry.type))points(geometry.coordinates);
 const text=JSON.stringify(geometry);return {fingerprint,hash:createHash('sha256').update(text).digest('hex'),bytes:Buffer.byteLength(text),west,east,south,north};
}
/** Fresh metadata manifest avoids geometry transfer and repeated coordinate expansion on cache hits. */
export async function gridViewport(client:PoolClient,actorId:number,query:unknown,mode?:string){
 const parsed=schema.safeParse(query);if(!parsed.success)throw new BusinessError('VALIDATION','地图范围或筛选条件无效',400);
 const input=parsed.data,width=input.east>input.west?input.east-input.west:input.east-input.west+360;
 if(input.south>=input.north||input.west===input.east||width>12||input.north-input.south>12)throw new BusinessError('VALIDATION','地图范围过大或无效，请放大后重试',400);
 const manifest=mode==='manifest',load=mode?.startsWith('load:')??false;
 const keys=load?(mode!.slice(5).split(',').slice(0,64)):[];
 const requested=new Map(keys.flatMap(key=>{const m=/^current:(\d+):([a-f0-9]{64})$/.exec(key);return m&&Number(m[1])<=2147483647?[[Number(m[1]),key] as const]:[];}));
 await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');await client.query("SET LOCAL statement_timeout='5s'");
 if(!(await client.query("SELECT id FROM users WHERE id=$1 AND role='admin' AND is_active=true AND status='active'",[actorId])).rowCount)throw new BusinessError('FORBIDDEN','仅在岗管理员可查看基础网格地图',403);
 const regions=input.region==='all'?['tokyo23','tokyo26','kanagawa','saitama','chiba']:[input.region],term='%'+input.q.replace(/[\\%_]/g,'\\$&')+'%';
 // Content fingerprints are read live, including direct SQL geometry edits without revision bumps.
 const {rows:versions}=await client.query<{id:number;fingerprint:string}>(`SELECT g.id,md5(g.geometry::text) AS fingerprint FROM bd_work_grid g JOIN bd_zone_release s ON s.id=g.release_id
 WHERE g.id>$1 AND coalesce(s.source->>'region_code','tokyo23')=ANY($2::text[]) AND ($3::int IS NULL OR g.id=$3)
 AND ($4::int[] IS NULL OR g.id=ANY($4::int[])) ORDER BY g.id`,[input.after,regions,input.gridId??null,load?[...requested.keys()]:null]);
 const entries=new Map<number,Metadata>(),missing=versions.filter(row=>!derived.has(row.id+':'+row.fingerprint));
 // Bound database result sizes even on the first process load of a large region.
 for(let offset=0;offset<missing.length;offset+=100){
  const batch=missing.slice(offset,offset+100),fingerprints=new Map(batch.map(row=>[row.id,row.fingerprint]));
  const {rows}=await client.query<{id:number;geometry:Geometry}>('SELECT id,geometry FROM bd_work_grid WHERE id=ANY($1::int[]) ORDER BY id',[batch.map(row=>row.id)]);
  for(const row of rows){const fingerprint=fingerprints.get(row.id)!;const entry=metadata(row.geometry,fingerprint);entries.set(row.id,entry);derived.set(row.id+':'+fingerprint,entry);if(derived.size>5000)derived.delete(derived.keys().next().value!);}
 }
 for(const row of versions){const entry=entries.get(row.id)??derived.get(row.id+':'+row.fingerprint);if(entry)entries.set(row.id,entry);}
 const ids=versions.filter(row=>{const b=entries.get(row.id)!;return input.gridId!==undefined||(b.north>=input.south&&b.south<=input.north&&(input.west<input.east?b.east>=input.west&&b.west<=input.east:b.east>=input.west||b.west<=input.east));}).map(row=>row.id);
 const limit=manifest?1000:input.limit;
 const {rows}=await client.query(`${gridAssetsSelect} WHERE g.id=ANY($1::int[]) AND ($2='' OR g.state=$2)
 AND ($3='' OR g.name ILIKE $4 OR g.source_id ILIKE $4 OR g.id::text=$3 OR g.stations::text ILIKE $4 OR g.keywords::text ILIKE $4)
 AND ($5::int IS NULL OR g.zone_id=$5) ORDER BY g.id LIMIT $6`,[ids,input.state,input.q,term,input.zoneId??null,load?64:limit+1]);
 if(load){
  const allowed=rows.filter(row=>requested.get(row.id)===`current:${row.id}:${entries.get(row.id)!.hash}`);
  const {rows:geometries}=await client.query('SELECT id,geometry FROM bd_work_grid WHERE id=ANY($1::int[]) ORDER BY id',[allowed.map(row=>row.id)]);
  return {geometryCacheVersion:1,geometries:Object.fromEntries(geometries.map(row=>[requested.get(row.id)!,row.geometry]))};
 }
 let bytes=0;const selected:typeof rows=[];
 for(const row of rows.slice(0,limit)){const entry=entries.get(row.id)!;const size=Buffer.byteLength(JSON.stringify(row))+256+(manifest?0:entry.bytes);if(!manifest&&entry.bytes>PAYLOAD_BUDGET)throw new BusinessError('MAP_GEOMETRY_TOO_LARGE','该网格边界过大，请使用列表查看和选择',409);if(bytes+size>PAYLOAD_BUDGET)break;bytes+=size;selected.push(row);}
 const {rows:geometries}=!manifest&&selected.length?await client.query('SELECT id,geometry FROM bd_work_grid WHERE id=ANY($1::int[]) ORDER BY id',[selected.map(row=>row.id)]):{rows:[]};
 const byId=new Map(geometries.map(g=>[g.id,g.geometry]));
 const features=selected.map(properties=>({type:'Feature',id:properties.id,properties,geometry:manifest?null:byId.get(properties.id)}));
 const hasMore=rows.length>selected.length;
 const result={type:'FeatureCollection',features,hasMore,nextCursor:hasMore?selected.at(-1)?.id??null:null,limit,bounds:{west:input.west,east:input.east,south:input.south,north:input.north},bboxOnly:true};
 return manifest?{...result,geometryCacheVersion:1,geometryRefs:selected.map((row,index)=>({path:['features',index,'geometry'],key:`current:${row.id}:${entries.get(row.id)!.hash}`}))}:result;
}
