import {gridAssetsSelect} from './grid-assets-query';
import {gridViewport} from './grid-viewport';
import {regionMerge} from './region-merge';
import {regionSplit} from './region-split';
import {Body,Controller,Get,Inject,Injectable,Param,Post,Query,Req} from '@nestjs/common';
import type {Request} from 'express';
import {z} from 'zod';
import {Database} from './database';
import {AuthService} from './auth';
import {BusinessError} from './errors';
const identity=z.coerce.number().int().positive().max(2147483647);
function parse<T>(schema:z.ZodType<T>,value:unknown){const result=schema.safeParse(value);if(!result.success)throw new BusinessError('VALIDATION','片区参数无效',400);return result.data;}
@Injectable()
export class ZoneAssetsService{
 constructor(@Inject(Database)private readonly db:Database){}
 async viewport(actorId:number,value:unknown){try{return await this.db.transaction(client=>gridViewport(client,actorId,value));}catch(error){if((error as {code?:string}).code==='57014')throw new BusinessError('MAP_QUERY_TIMEOUT','地图读取超时，请放大地图或使用列表',503);throw error;}}
 async mergePreview(actorId:number,value:unknown){return this.db.transaction(client=>regionMerge(client,actorId,value));}
 async merge(actorId:number,value:unknown){return this.db.transaction(client=>regionMerge(client,actorId,value,true));}
 async splitPreview(actorId:number,id:number,value:unknown){
  const input=parse(z.object({revision:z.number().int().min(0)}).strict(),value);
  return this.db.transaction(client=>regionSplit(client,actorId,id,input.revision));
 }
 async split(actorId:number,id:number,value:unknown){
  const input=parse(z.object({revision:z.number().int().min(0),previewToken:z.string().regex(/^[a-f0-9]{64}$/),confirm:z.literal('SPLIT_REGION')}).strict(),value);
  return this.db.transaction(client=>regionSplit(client,actorId,id,input.revision,input.previewToken));
 }
 async grids(actorId:number,value:unknown){
  const input=parse(z.object({q:z.string().trim().max(128).default(''),region:z.enum(['tokyo23','tokyo26','kanagawa','saitama','chiba','all']).default('tokyo23'),state:z.enum(['','disabled','ready']).default(''),page:identity.max(10000).default(1)}).strict(),value);
  return this.db.transaction(async client=>{
   await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');await this.admin(client,actorId);
   const term='%'+input.q.replace(/[\\%_]/g,'\\$&')+'%';
   const {rows:[result]}=await client.query(`WITH matching AS (
    ${gridAssetsSelect}
    WHERE coalesce(s.source->>'region_code','tokyo23')=ANY($1::text[]) AND ($2='' OR g.state=$2)
     AND ($3='' OR g.name ILIKE $4 OR g.source_id ILIKE $4 OR g.id::text=$3 OR g.stations::text ILIKE $4 OR g.keywords::text ILIKE $4)
   ), paged AS (SELECT * FROM matching ORDER BY id LIMIT 30 OFFSET $5)
   SELECT (SELECT count(*)::int FROM matching) AS total,coalesce((SELECT jsonb_agg(p ORDER BY p.id) FROM paged p),'[]') AS items`,[input.region==='all'?['tokyo23','tokyo26','kanagawa','saitama','chiba']:[input.region],input.state,input.q,term,(input.page-1)*30]);
   return {...result,page:input.page,pageSize:30};
  });
 }
 async enableGrid(actorId:number,id:number,value:unknown){
  const input=parse(z.object({revision:z.number().int().min(0)}).strict(),value);
  return this.db.transaction(async client=>{
   const {rows:[actor]}=await client.query("SELECT username,display_name FROM users WHERE id=$1 AND role='admin' AND is_active=true AND status='active' FOR SHARE",[actorId]);
   if(!actor)throw new BusinessError('FORBIDDEN','仅在岗管理员可启用网格',403);
   const {rows:[snapshot]}=await client.query('SELECT release_id FROM bd_work_grid WHERE id=$1',[id]);
   if(!snapshot)throw new BusinessError('NOT_FOUND','网格不存在，请刷新后重试',404);
   await client.query('SELECT id FROM bd_zone_release WHERE id=$1 FOR UPDATE',[snapshot.release_id]);
   const {rows:[grid]}=await client.query('SELECT * FROM bd_work_grid WHERE id=$1 FOR UPDATE',[id]);
   if(!grid)throw new BusinessError('NOT_FOUND','网格不存在，请刷新后重试',404);
   if(grid.release_id!==snapshot.release_id||grid.revision!==input.revision)throw new BusinessError('REVISION_CONFLICT','网格已变更，请刷新后重试',409);
   if(grid.state!=='disabled')throw new BusinessError('GRID_STATE_CHANGED','网格状态已变更，请刷新后重试',409);
   const history=(await client.query('SELECT id FROM bd_store_visit_log WHERE grid_id=$1 LIMIT 1',[id])).rowCount;
   if(grid.zone_id!==null||grid.assignee_id!==null||grid.progress||grid.task_state!=='unvisited'||history)throw new BusinessError('GRID_PROTECTED','涉及已派发或已开展的网格，不能转为可用网格',409);
   const before={state:grid.state,revision:grid.revision},after={state:'ready',revision:grid.revision+1};
   await client.query("UPDATE bd_work_grid SET state='ready',revision=revision+1,updated_at=now() WHERE id=$1",[id]);
   await client.query("INSERT INTO bd_log(domain,action,ref_id,ref_label,field,old_value,new_value,actor,actor_name,note,created_at) VALUES('grid','enable_grid',$1,$2,'operations',$3,$4,$5,$6,'',now())",[id,String(id),JSON.stringify(before),JSON.stringify(after),actor.username,actor.display_name]);
   return {id,revision:after.revision};
  });
 }
 async regionMap(actorId:number,value:unknown){
  const input=parse(z.object({region:z.enum(['tokyo23','tokyo26','kanagawa','saitama','chiba','all']).default('tokyo23'),after:z.coerce.number().int().min(0).default(0),limit:z.coerce.number().int().min(1).max(100).default(50)}).strict(),value);
  return this.db.transaction(async client=>{await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');await this.admin(client,actorId);await client.query("SET LOCAL statement_timeout='5s'");
   const {rows}=await client.query(`SELECT r.id,r.name,r.geometry,r.revision,r.area_km2 AS "areaKm2",r.store_count AS stores FROM bd_region r JOIN bd_zone_release s ON s.id=r.release_id WHERE r.state<>'archived' AND r.id>$1 AND coalesce(s.source->>'region_code','tokyo23')=ANY($2::text[]) ORDER BY r.id LIMIT $3`,[input.after,input.region==='all'?['tokyo23','tokyo26','kanagawa','saitama','chiba']:[input.region],input.limit+1]);
   let bytes=0;const features=[];for(const row of rows.slice(0,input.limit)){const {geometry,...properties}=row;const feature={type:'Feature',id:row.id,properties,geometry};const size=Buffer.byteLength(JSON.stringify(feature));if(bytes+size>2*1024*1024){if(!features.length)throw new BusinessError('MAP_GEOMETRY_TOO_LARGE','片区边界过大，请使用目录查看',409);break;}features.push(feature);bytes+=size;}
   const hasMore=rows.length>features.length;return {type:'FeatureCollection',features,hasMore,nextCursor:hasMore?features.at(-1)?.id??null:null};
  });
 }
 async catalog(actorId:number,value:unknown){
  const input=parse(z.object({q:z.string().trim().max(128).default(''),region:z.enum(['tokyo23','tokyo26','kanagawa','saitama','chiba','all']).default('tokyo23'),page:identity.max(10000).default(1)}).strict(),value);
  return this.db.transaction(async client=>{
   await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');
   await this.admin(client,actorId);
   const term='%'+input.q.replace(/[\\%_]/g,'\\$&')+'%';
   const {rows:[row]}=await client.query(`WITH matching AS (
    SELECT r.id,r.name,r.source_id AS "sourceId",r.release_id AS "releaseId",r.area_km2 AS "areaKm2",r.state,r.revision,
      coalesce(s.source->>'region_code','tokyo23') AS "regionCode"
    FROM bd_region r JOIN bd_zone_release s ON s.id=r.release_id
    WHERE r.state<>'archived' AND coalesce(s.source->>'region_code','tokyo23')=ANY($1::text[])
      AND ($2='' OR r.name ILIKE $3 OR r.id::text=$2 OR EXISTS(SELECT 1 FROM bd_work_grid g
       WHERE g.zone_id=r.id AND g.state<>'disabled' AND (g.name ILIKE $3 OR g.source_id ILIKE $3 OR g.id::text=$2 OR g.stations::text ILIKE $3 OR g.keywords::text ILIKE $3)))
   ), paged AS (SELECT * FROM matching ORDER BY id LIMIT 30 OFFSET $4),
   enriched AS (SELECT p.*,(SELECT count(*)::int FROM bd_work_grid g WHERE g.zone_id=p.id) AS grids,
     (SELECT count(*)::int FROM bd_grid_store m JOIN bd_work_grid g ON g.id=m.grid_id WHERE g.zone_id=p.id) AS stores FROM paged p)
   SELECT (SELECT count(*)::int FROM matching) AS total,coalesce((SELECT jsonb_agg(p ORDER BY p.id) FROM enriched p),'[]') AS items`,[input.region==='all'?['tokyo23','tokyo26','kanagawa','saitama','chiba']:[input.region],input.q,term,(input.page-1)*30]);
   return {...row,page:input.page,pageSize:30,region:input.region};
  });
 }
 private async admin(client:import('pg').PoolClient,id:number){
  if(!(await client.query("SELECT id FROM users WHERE id=$1 AND role='admin' AND is_active=true AND status='active'",[id])).rowCount)throw new BusinessError('FORBIDDEN','仅在岗管理员可查看基础片区',403);
 }
 async detail(actorId:number,id:number){return this.db.transaction(async client=>{
  await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');await this.admin(client,actorId);
  const {rows:[region]}=await client.query(`SELECT r.id,r.name,r.source_id AS "sourceId",r.release_id AS "releaseId",r.area_km2 AS "areaKm2",r.state,r.revision,(SELECT name FROM bd_team WHERE id=r.team_id) AS "teamName",(SELECT display_name FROM users WHERE id=r.leader_id) AS "leaderName",
    coalesce(s.source->>'region_code','tokyo23') AS "regionCode" FROM bd_region r JOIN bd_zone_release s ON s.id=r.release_id WHERE r.id=$1 AND r.state<>'archived'`,[id]);
  if(!region)throw new BusinessError('NOT_FOUND','片区不存在',404);
  const {rows:[stats]}=await client.query(`WITH grids AS (SELECT id,source_id AS "sourceId",name,stations,keywords,state,area_km2 AS "areaKm2",revision FROM bd_work_grid WHERE zone_id=$1),
   mapped AS (SELECT g.id,count(DISTINCT m.source_id)::int AS total,count(DISTINCT v.store_id)::int AS visited
    FROM grids g LEFT JOIN bd_grid_store m ON m.grid_id=g.id LEFT JOIN bd_store_visit_log v ON v.grid_id=g.id AND v.store_id=m.source_id GROUP BY g.id),
   enriched AS (SELECT g.*,m.total,m.visited,CASE WHEN m.total=0 THEN 0 ELSE m.visited*100/m.total END AS progress FROM grids g JOIN mapped m USING(id)),
   status_counts AS (SELECT coalesce(CASE WHEN s.status IN('normal','abnormal','uncertain','closed') THEN s.status END,'unverified') AS status,count(*)::int AS n
    FROM bd_grid_store m JOIN grids g ON g.id=m.grid_id LEFT JOIN bd_work_store s ON s.id=m.source_id GROUP BY 1)
   SELECT coalesce((SELECT jsonb_agg(g ORDER BY g.id) FROM enriched g),'[]') AS grids,
    coalesce((SELECT jsonb_object_agg(status,n) FROM status_counts),'{}') AS statuses,
    (SELECT count(*)::int FROM bd_grid_store m JOIN grids g ON g.id=m.grid_id) AS total,
    (SELECT count(*)::int FROM bd_grid_store m JOIN grids g ON g.id=m.grid_id WHERE EXISTS(SELECT 1 FROM bd_store_visit_log v WHERE v.grid_id=m.grid_id AND v.store_id=m.source_id)) AS visited`,[id]);
  const grids=stats.grids as {progress:number}[];
  return {region,grids:stats.grids,stats:{total:stats.total,visited:stats.visited,progress:grids.length?Number((grids.reduce((sum,row)=>sum+row.progress,0)/grids.length).toFixed(1)):0,
   statuses:{normal:0,abnormal:0,uncertain:0,closed:0,unverified:0,...stats.statuses}}};
 });}
 async rename(actorId:number,id:number,value:unknown){
  const input=parse(z.object({revision:z.number().int().min(0),name:z.string().transform(value=>value.trim()).refine(value=>Array.from(value).length>0&&Array.from(value).length<=128)}).strict(),value);
  return this.db.transaction(async client=>{
   const {rows:[actor]}=await client.query("SELECT username,display_name FROM users WHERE id=$1 AND role='admin' AND is_active=true AND status='active' FOR SHARE",[actorId]);
   if(!actor)throw new BusinessError('FORBIDDEN','仅在岗管理员可修改片区',403);
   const {rows:[region]}=await client.query('SELECT id,name,state,revision FROM bd_region WHERE id=$1 FOR UPDATE',[id]);
   if(!region)throw new BusinessError('NOT_FOUND','片区不存在',404);
   if(region.revision!==input.revision)throw new BusinessError('REVISION_CONFLICT','片区已变化，请刷新后重试',409);
   if(region.state==='archived')throw new BusinessError('REGION_ARCHIVED','片区已归档',409);
   if(region.name===input.name)return {id,revision:region.revision,reused:true};
   await client.query('UPDATE bd_region SET name=$1,revision=revision+1,updated_at=now() WHERE id=$2',[input.name,id]);
   await client.query("INSERT INTO bd_log(domain,action,ref_id,ref_label,field,old_value,new_value,actor,actor_name,note,created_at) VALUES('zone','rename',$1,$2,'operations',$3,$4,$5,$6,'',now())",[id,String(id),JSON.stringify({name:region.name}),JSON.stringify({name:input.name}),actor.username,actor.display_name]);
   return {id,revision:region.revision+1,reused:false};
  });
 }
}
@Controller('zone-assets')
export class ZoneAssetsController{
 constructor(@Inject(AuthService)private readonly auth:AuthService,@Inject(ZoneAssetsService)private readonly service:ZoneAssetsService){}
 @Get('map-config')async mapConfig(@Req()req:Request){await this.auth.require(req,{admin:true});const key=process.env.GOOGLE_PUBLIC_API_KEY?.trim()??'';return {provider:'google',enabled:!!key,key:key||null};}
 @Get('region-map')async regionMap(@Req()req:Request,@Query()query:unknown){const {user}=await this.auth.require(req,{admin:true});return this.service.regionMap(user.id,query);}
 @Get('grid-viewport')async viewport(@Req()req:Request,@Query()query:unknown){const {user}=await this.auth.require(req,{admin:true});return this.service.viewport(user.id,query);}
 @Post('merge-preview')async mergePreview(@Req()req:Request,@Body()body:unknown){const {user}=await this.auth.require(req,{admin:true,write:true});return this.service.mergePreview(user.id,body);}
 @Post('merge')async merge(@Req()req:Request,@Body()body:unknown){const {user}=await this.auth.require(req,{admin:true,write:true});return this.service.merge(user.id,body);}
 @Post('regions/:id/split-preview')async splitPreview(@Req()req:Request,@Param('id')id:string,@Body()body:unknown){const {user}=await this.auth.require(req,{admin:true,write:true});return this.service.splitPreview(user.id,parse(identity,id),body);}
 @Post('regions/:id/split')async split(@Req()req:Request,@Param('id')id:string,@Body()body:unknown){const {user}=await this.auth.require(req,{admin:true,write:true});return this.service.split(user.id,parse(identity,id),body);}
 @Get('grids')async grids(@Req()req:Request,@Query()query:unknown){const {user}=await this.auth.require(req,{admin:true});return this.service.grids(user.id,query);}
 @Post('grids/:id/enable')async enable(@Req()req:Request,@Param('id')id:string,@Body()body:unknown){const {user}=await this.auth.require(req,{admin:true,write:true});return this.service.enableGrid(user.id,parse(identity,id),body);}
 @Get('regions')async catalog(@Req()req:Request,@Query()query:unknown){const {user}=await this.auth.require(req,{admin:true});return this.service.catalog(user.id,query);}
 @Get('regions/:id')async detail(@Req()req:Request,@Param('id')id:string){const {user}=await this.auth.require(req,{admin:true});return this.service.detail(user.id,parse(identity,id));}
 @Post('regions/:id/rename')async rename(@Req()req:Request,@Param('id')id:string,@Body()body:unknown){const {user}=await this.auth.require(req,{admin:true,write:true});return this.service.rename(user.id,parse(identity,id),body);}
}
