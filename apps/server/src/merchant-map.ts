import {Controller,Get,Inject,Injectable,Query,Req} from '@nestjs/common';
import type {Request} from 'express';import {z} from 'zod';import type {PoolClient} from 'pg';
import {MerchantSearchService} from './merchant-search';
import {Database} from './database';import {AuthService} from './auth';import {BusinessError} from './errors';
import {merchantView,merchantSummary,MERCHANT_POINT_LIMIT,MERCHANT_POINT_ZOOM} from '../../../packages/domain/src/merchant-viewport';
const number=z.preprocess(value=>typeof value==='string'&&value.trim()?Number(value):value,z.number().finite());
const optionalId=z.preprocess(value=>value===''||value===undefined?undefined:value,z.coerce.number().int().positive().optional());
const regionCode=z.enum(['tokyo23','tokyo26','kanagawa','saitama','chiba']);
const viewSchema=z.object({west:number,south:number,east:number,north:number,zoom:number,region:z.preprocess(value=>value===''||value===undefined?undefined:value,regionCode.optional()),zoneId:optionalId,gridId:optionalId}).strict();
const scopeSchema=z.object({region:regionCode,zoneId:optionalId}).strict();
const VISITS="'complete_store_task','update_store_result'";
// A city-sized gist scan of the source table exceeds the 5s guard on production. Assignment joins stay inside it.
const NARROW_SPAN=0.003;
const recent=`recent AS (SELECT store_source_id,max(created_at) AS last_visited_at FROM bd_task_event
 WHERE event_type IN (${VISITS}) AND created_at>=($5::timestamptz AT TIME ZONE 'UTC')-interval '30 days'
 AND created_at<=($5::timestamptz AT TIME ZONE 'UTC') AND store_source_id<>'' GROUP BY store_source_id)`;
function bounds(view:ReturnType<typeof merchantView>,alias:string){const p=alias;
 if(view.west<view.east)return (view.east-view.west)*(view.north-view.south)>=1?`${p}.lat BETWEEN $2::double precision AND $4::double precision AND ${p}.lon BETWEEN $1::double precision AND $3::double precision`:`point(${p}.lon,${p}.lat) <@ box(point($1::double precision,$2::double precision),point($3::double precision,$4::double precision))`;
 return `(point(${p}.lon,${p}.lat) <@ box(point($1::double precision,$2::double precision),point(180,$4::double precision)) OR point(${p}.lon,${p}.lat) <@ box(point(-180,$2::double precision),point($3::double precision,$4::double precision)))`;
}
function span(view:ReturnType<typeof merchantView>){const width=view.east>view.west?view.east-view.west:view.east-view.west+360;return width*(view.north-view.south);}
function plainBounds(view:ReturnType<typeof merchantView>,lat:string,lon:string){
 if(view.west<view.east)return `${lat} BETWEEN $2::double precision AND $4::double precision AND ${lon} BETWEEN $1::double precision AND $3::double precision`;
 return `${lat} BETWEEN $2::double precision AND $4::double precision AND (${lon}>=$1::double precision OR ${lon}<=$3::double precision)`;
}
function outline(geometry:unknown){try{let west=Infinity,east=-Infinity,south=Infinity,north=-Infinity;const value=typeof geometry==='string'?JSON.parse(geometry):geometry as {type?:string;coordinates?:unknown};
 const walk=(node:unknown)=>{if(!Array.isArray(node))return;if(typeof node[0]==='number'&&typeof node[1]==='number'){west=Math.min(west,node[0]);east=Math.max(east,node[0]);south=Math.min(south,node[1]);north=Math.max(north,node[1]);}else for(const child of node)walk(child);};
 if(value&&(value.type==='Polygon'||value.type==='MultiPolygon'))walk(value.coordinates);return Number.isFinite(west)?{west,east,south,north}:null;}catch{return null;}
}
const scopeWhere=`($6::int IS NULL OR g.id=$6) AND ($7::int IS NULL OR rg.id=$7) AND ($8::text[] IS NULL OR coalesce(rel.source->>'region_code','tokyo23')=ANY($8::text[]))`;
const scopeJoin=`bd_grid_store s JOIN bd_work_grid g ON g.id=s.grid_id JOIN bd_region rg ON rg.id=g.zone_id AND rg.state<>'archived' JOIN bd_zone_release rel ON rel.id=rg.release_id`;
@Injectable()
export class MerchantMapService{
 constructor(@Inject(Database)private readonly db:Database){}
 private async read<T>(actorId:number,operation:(client:PoolClient)=>Promise<T>){
  try{return await this.db.transaction(async client=>{await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');await client.query("SET LOCAL statement_timeout='5s'");
   if(!(await client.query("SELECT id FROM users WHERE id=$1 AND role='admin' AND is_active=true AND status='active'",[actorId])).rowCount)throw new BusinessError('FORBIDDEN','仅在岗管理员可查看商户地图',403);
   return operation(client);
  });}catch(error){if(error instanceof BusinessError)throw error;throw new BusinessError('MERCHANT_MAP_UNAVAILABLE','商户地图数据读取失败，请缩小范围后重试',503);}
 }
 async viewport(actorId:number,query:unknown,now=new Date()){
  const parsed=viewSchema.safeParse(query);if(!parsed.success)throw new BusinessError('VALIDATION','地图范围无效，请缩放后重试',400);
  let view:ReturnType<typeof merchantView>;try{view=merchantView(parsed.data);}catch{throw new BusinessError('VALIDATION','地图范围无效，请缩放后重试',400);}
  const scope=parsed.data.region||parsed.data.zoneId||parsed.data.gridId?{grid:parsed.data.gridId??null,zone:parsed.data.zoneId??null,regions:parsed.data.region?[parsed.data.region]:null}:null;
  return this.read(actorId,async client=>{
   // Source remains read-only. Ops coordinate corrections overlay source locations without duplication.
   const spatial=`visible AS (
    SELECT m.place_id,m.lat,m.lon FROM public.kanto_merchants_v20261029 m WHERE ${bounds(view,'m')}
     AND NOT EXISTS(SELECT 1 FROM bd_merchant_detail d WHERE d.store_id=m.place_id AND d.lat IS NOT NULL AND d.lon IS NOT NULL)
    UNION ALL
    SELECT m.place_id,d.lat,d.lon FROM bd_merchant_detail d JOIN public.kanto_merchants_v20261029 m ON m.place_id=d.store_id
     WHERE d.lat IS NOT NULL AND d.lon IS NOT NULL AND ${bounds(view,'d')})`;
   const assigned=`ids AS (SELECT DISTINCT s.source_id FROM ${scopeJoin} WHERE ${scopeWhere}),
    located AS (SELECT k.place_id,CASE WHEN d.lat IS NOT NULL AND d.lon IS NOT NULL THEN d.lat ELSE k.lat END AS lat,CASE WHEN d.lat IS NOT NULL AND d.lon IS NOT NULL THEN d.lon ELSE k.lon END AS lon
     FROM ids i JOIN public.kanto_merchants_v20261029 k ON k.place_id=i.source_id LEFT JOIN bd_merchant_detail d ON d.store_id=k.place_id),
    visible AS (SELECT * FROM located m WHERE ${plainBounds(view,'m.lat','m.lon')})`;
   const wide=!!scope&&span(view)>=NARROW_SPAN;
   const visible=wide?assigned:spatial;
   const membership=!scope||wide?'':` WHERE EXISTS (SELECT 1 FROM ${scopeJoin} WHERE s.source_id=m.place_id AND ${scopeWhere})`;
   const args=[view.west,view.south,view.east,view.north,now.toISOString(),...(scope?[scope.grid,scope.zone,scope.regions]:[])];
   const base=`FROM visible m LEFT JOIN recent r ON r.store_source_id=m.place_id${membership}`;
   if(view.zoom>=MERCHANT_POINT_ZOOM){const {rows}=await client.query(`WITH ${recent},${visible} SELECT m.place_id,m.lat,m.lon,r.last_visited_at ${base} ORDER BY m.place_id LIMIT 1001`,args);
    if(rows.length<=MERCHANT_POINT_LIMIT){const points=rows.map(row=>({id:row.place_id,lat:row.lat,lon:row.lon,covered:row.last_visited_at!==null}));return {mode:'points' as const,points,cells:[],point_limit:MERCHANT_POINT_LIMIT,summary:merchantSummary(points.length,points.filter(p=>p.covered).length),dense:false,as_of:now.toISOString()};}
   }
   const scale=scope?`$${args.length+1}`:'$6';
   const {rows}=await client.query(`WITH ${recent},${visible} SELECT floor((m.lon+180)/360.0*${scale}::double precision) AS bx,
    floor((1-ln(tan(radians(m.lat))+1.0/cos(radians(m.lat)))/pi())/2*${scale}::double precision) AS by,
    avg(m.lat) AS lat,avg(m.lon) AS lon,count(*)::int AS merchant_count,count(r.store_source_id)::int AS covered_count ${base} GROUP BY bx,by ORDER BY bx,by`,[...args,view.scale]);
   const cells=rows.map(row=>({id:`${view.bucketZoom}:${row.bx}:${row.by}`,lat:Number(row.lat),lon:Number(row.lon),...merchantSummary(row.merchant_count,row.covered_count)}));
   return {mode:'clusters' as const,cells,points:[],point_limit:MERCHANT_POINT_LIMIT,summary:merchantSummary(cells.reduce((sum,row)=>sum+row.merchant_count,0),cells.reduce((sum,row)=>sum+row.covered_count,0)),dense:view.zoom>=MERCHANT_POINT_ZOOM,as_of:now.toISOString()};
  });
 }
 async scopes(actorId:number,query:unknown){
  const parsed=scopeSchema.safeParse(query);if(!parsed.success)throw new BusinessError('VALIDATION','地区或片区无效',400);
  return this.read(actorId,async client=>{
   if(parsed.data.zoneId){const {rows}=await client.query(`SELECT g.id,g.name,g.geometry FROM bd_work_grid g JOIN bd_region r ON r.id=g.zone_id AND r.state<>'archived' JOIN bd_zone_release s ON s.id=r.release_id WHERE r.id=$1 AND coalesce(s.source->>'region_code','tokyo23')=$2 ORDER BY g.name,g.id`,[parsed.data.zoneId,parsed.data.region]);
    return {zones:[],grids:rows.flatMap(row=>{const box=outline(row.geometry);return box?[{id:row.id,name:row.name,...box}]:[];})};}
   const {rows}=await client.query(`SELECT r.id,r.name,r.geometry FROM bd_region r JOIN bd_zone_release s ON s.id=r.release_id WHERE r.state<>'archived' AND coalesce(s.source->>'region_code','tokyo23')=$1 ORDER BY r.name,r.id`,[parsed.data.region]);
   return {zones:rows.flatMap(row=>{const box=outline(row.geometry);return box?[{id:row.id,name:row.name,...box}]:[];}),grids:[]};
  });
 }
 async search(actorId:number,query:unknown){
  await this.read(actorId,async()=>{});
  // Reuse the global index and ranking; viewport bounds must never constrain search.
  const result=await new MerchantSearchService(this.db).search(actorId,query);
  return this.read(actorId,async client=>{
   const ids=result.items.map((item:any)=>item.merchantId);
   const {rows}=await client.query(`SELECT DISTINCT ON (store_source_id) store_source_id,new_value->'result'->>'business_status' AS business_status
    FROM bd_task_event WHERE store_source_id=ANY($1::text[]) AND event_type IN (${VISITS})
    ORDER BY store_source_id,created_at DESC,id DESC`,[ids]);
   const statuses=new Map(rows.map(row=>[row.store_source_id,row.business_status]));
   return {...result,items:result.items.map((item:any)=>({...item,businessStatus:statuses.get(item.merchantId)??null}))};
  });
 }
 async detail(actorId:number,identity:unknown,now=new Date()){
  if(typeof identity!=='string'||!identity||Array.from(identity).length>255)throw new BusinessError('VALIDATION','商户编号无效',400);
  return this.read(actorId,async client=>{const {rows:[row]}=await client.query(`SELECT m.place_id,m.name,m.address,
   CASE WHEN d.lat IS NOT NULL AND d.lon IS NOT NULL THEN d.lat ELSE m.lat END AS lat,
   CASE WHEN d.lat IS NOT NULL AND d.lon IS NOT NULL THEN d.lon ELSE m.lon END AS lon,m.phone,m.website,m.sources,m.prefecture,m.municipality,
   coalesce((SELECT e.new_value->>'location_accuracy' FROM bd_task_event e WHERE e.store_source_id=m.place_id AND e.event_type='verify_store_location' AND e.created_at<=($2::timestamptz AT TIME ZONE 'UTC') ORDER BY e.created_at DESC,e.id DESC LIMIT 1),m.location_accuracy) AS location_accuracy,
   (SELECT max(e.created_at) FROM bd_task_event e WHERE e.store_source_id=m.place_id AND e.event_type IN (${VISITS}) AND e.created_at<=($2::timestamptz AT TIME ZONE 'UTC')) AS last_visited_at
   FROM public.kanto_merchants_v20261029 m LEFT JOIN bd_merchant_detail d ON d.store_id=m.place_id WHERE m.place_id=$1`,[identity,now.toISOString()]);
   if(!row)throw new BusinessError('NOT_FOUND','商户不存在',404);
   const last:Date|null=row.last_visited_at;return {...row,covered:last!==null&&last.getTime()>=now.getTime()-30*86400000,last_visited_at:last?.toISOString()??null};
  });
 }
}
@Controller('merchant-map')
export class MerchantMapController{
 constructor(@Inject(AuthService)private readonly auth:AuthService,@Inject(MerchantMapService)private readonly service:MerchantMapService){}
 @Get('config')async config(@Req()req:Request){await this.auth.require(req,{admin:true});const key=process.env.GOOGLE_PUBLIC_API_KEY?.trim()??'';return {enabled:!!key,key:key||null,provider:'google'};}
 @Get('scopes')async scopes(@Req()req:Request,@Query()query:unknown){const {user}=await this.auth.require(req,{admin:true});return this.service.scopes(user.id,query);}
 @Get('data')async data(@Req()req:Request,@Query()query:unknown){const {user}=await this.auth.require(req,{admin:true});return this.service.viewport(user.id,query);}
 @Get('search')async search(@Req()req:Request,@Query()query:unknown){const {user}=await this.auth.require(req,{admin:true});return this.service.search(user.id,query);}
 @Get('detail')async detail(@Req()req:Request,@Query('identity')identity:unknown){const {user}=await this.auth.require(req,{admin:true});return this.service.detail(user.id,identity);}
}
