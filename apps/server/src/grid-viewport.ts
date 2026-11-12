import type {PoolClient} from 'pg';
import {z} from 'zod';
import {BusinessError} from './errors';
import {gridAssetsSelect} from './grid-assets-query';
const value=z.coerce.number().finite();
const schema=z.object({west:value.min(-180).max(180),east:value.min(-180).max(180),south:value.min(-85).max(85),north:value.min(-85).max(85),
 region:z.enum(['tokyo23','tokyo26','kanagawa','saitama','chiba','all']).default('tokyo23'),q:z.string().trim().max(128).default(''),state:z.enum(['','ready','disabled']).default(''),
 zoneId:z.coerce.number().int().positive().optional(),after:z.coerce.number().int().min(0).max(2147483647).default(0),limit:z.coerce.number().int().min(1).max(100).default(100)}).strict();
const PAYLOAD_BUDGET=2*1024*1024;
/** BBox intersection is a broad phase; preserve holes in the actual returned GeoJSON. No schema/index changes. */
export async function gridViewport(client:PoolClient,actorId:number,query:unknown){
 const parsed=schema.safeParse(query);if(!parsed.success)throw new BusinessError('VALIDATION','地图范围或筛选条件无效',400);
 const input=parsed.data,width=input.east>input.west?input.east-input.west:input.east-input.west+360;
 if(input.south>=input.north||input.west===input.east||width>12||input.north-input.south>12)throw new BusinessError('VALIDATION','地图范围过大或无效，请放大后重试',400);
 await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');await client.query("SET LOCAL statement_timeout='5s'");
 if(!(await client.query("SELECT id FROM users WHERE id=$1 AND role='admin' AND is_active=true AND status='active'",[actorId])).rowCount)throw new BusinessError('FORBIDDEN','仅在岗管理员可查看基础网格地图',403);
 const regions=input.region==='all'?['tokyo23','tokyo26','kanagawa','saitama','chiba']:[input.region],term='%'+input.q.replace(/[\\%_]/g,'\\$&')+'%';
 const {rows}=await client.query(`WITH matching AS (
 ${gridAssetsSelect},
 LATERAL (SELECT min((p->>0)::double precision) AS west,max((p->>0)::double precision) AS east,
  min((p->>1)::double precision) AS south,max((p->>1)::double precision) AS north
  FROM jsonb_array_elements(CASE WHEN g.geometry->>'type'='Polygon' THEN jsonb_build_array(g.geometry::jsonb->'coordinates') WHEN g.geometry->>'type'='MultiPolygon' THEN g.geometry::jsonb->'coordinates' ELSE '[]'::jsonb END) polygon
  CROSS JOIN LATERAL jsonb_array_elements(polygon) ring CROSS JOIN LATERAL jsonb_array_elements(ring) p
  WHERE jsonb_typeof(p->0)='number' AND jsonb_typeof(p->1)='number') bbox
 WHERE g.id>$1 AND ($11::int IS NULL OR g.zone_id=$11) AND coalesce(s.source->>'region_code','tokyo23')=ANY($2::text[]) AND ($3='' OR g.state=$3)
  AND ($4='' OR g.name ILIKE $5 OR g.source_id ILIKE $5 OR g.id::text=$4 OR g.stations::text ILIKE $5 OR g.keywords::text ILIKE $5)
  AND bbox.north>=$6::double precision AND bbox.south<=$7::double precision AND (($8::double precision<$9::double precision AND bbox.east>=$8::double precision AND bbox.west<=$9::double precision) OR ($8::double precision>$9::double precision AND (bbox.east>=$8::double precision OR bbox.west<=$9::double precision)))
 ), paged AS (SELECT * FROM matching ORDER BY id LIMIT $10)
 SELECT p.*,octet_length(g.geometry::text)+octet_length(row_to_json(p)::text)+256 AS bytes
 FROM paged p JOIN bd_work_grid g ON g.id=p.id ORDER BY p.id`,[input.after,regions,input.state,input.q,term,input.south,input.north,input.west,input.east,input.limit+1,input.zoneId??null]);
 let bytes=0;const selected:typeof rows=[];
 for(const row of rows.slice(0,input.limit)){if(bytes+row.bytes>PAYLOAD_BUDGET){if(!selected.length)throw new BusinessError('MAP_GEOMETRY_TOO_LARGE','该网格边界过大，请使用列表查看和选择',409);break;}bytes+=row.bytes;selected.push(row);}
 const {rows:geometries}=selected.length?await client.query('SELECT id,geometry FROM bd_work_grid WHERE id=ANY($1::int[]) ORDER BY id',[selected.map(row=>row.id)]):{rows:[]};
 const byId=new Map(geometries.map(g=>[g.id,g.geometry]));
 const features=selected.map(row=>{const {bytes:_,...properties}=row;return {type:'Feature',id:row.id,properties,geometry:byId.get(row.id)};});
 const hasMore=rows.length>selected.length;
 return {type:'FeatureCollection',features,hasMore,nextCursor:hasMore?selected.at(-1)?.id??null:null,limit:input.limit,bounds:{west:input.west,east:input.east,south:input.south,north:input.north},bboxOnly:true};
}
