import {mobileVisits} from './mobile-visits';
import {connectedPlan} from './batch-auto-plan';
import {configuredJsonModel,type ReportModel} from './report-model';
import {createHash,createHmac,timingSafeEqual} from 'node:crypto';
const autoSecret=process.env.NEXT_SESSION_SECRET||randomUUID();
import {batchMapRows,writeLocation} from './batch-locations';
import {readBatchEvents} from "./batch-events";
import {randomUUID} from 'node:crypto';import {gridCovers} from '../../../packages/domain/src/grid-coverage';import {unicodeCasefold} from '../../../packages/domain/src/unicode-casefold';
import {FilesInterceptor} from '@nestjs/platform-express';import {memoryStorage} from 'multer';import {pipeline} from 'node:stream/promises';import {createReadStream} from 'node:fs';import {stat} from 'node:fs/promises';import {PHOTO_BYTES,PHOTO_COUNT,photoPath,photoRef,photoScope,prepareBatchPhotos} from './batch-photos';
import {Body,Controller,Get,Inject,Injectable,Param,Post,Query,Req,Res,UseGuards,UseInterceptors,UploadedFiles,CanActivate,ExecutionContext} from '@nestjs/common';import type {Request,Response} from 'express';import type {PoolClient} from 'pg';import {z} from 'zod';import {Database} from './database';import {AuthService} from './auth';import {BusinessError} from './errors';import {batchProgress} from '../../../packages/domain/src/batch-progress';
const identity=z.coerce.number().int().positive().max(2147483647),revision=z.number().int().min(0),storeInput=z.object({revision,requestId:z.string().uuid(),status:z.enum(['normal','abnormal','uncertain','closed']),note:z.string().max(2000).default(''),outcome:z.enum(['completed','undecided','incomplete']).optional(),photoUrls:z.array(z.string().max(2048)).max(3).optional()}).strict();
function parse<T>(schema:z.ZodType<T>,value:unknown){const input=schema.safeParse(value);if(!input.success)throw new BusinessError('VALIDATION','批次任务参数、门店状态或备注无效',400);return input.data;}
interface Actor{id:number;role:string;code:string|null;name:string;username:string}
@Injectable()
export class BatchWorkService{
 constructor(@Inject(Database)private readonly db:Database){}
 private async actor(client:PoolClient,id:number,lock=false):Promise<Actor>{const row=(await client.query<Actor>(`SELECT id,role,person_code AS code,display_name AS name,username FROM users WHERE id=$1 AND is_active=true AND status='active' ${lock?'FOR SHARE':''}`,[id])).rows[0];if(!row)throw new BusinessError('STAFF_NOT_ACTIVE','当前账号未在岗，不能执行作业',403);return row;}
 private async membership(client:PoolClient,actor:Actor,region:any){return(await client.query(`SELECT m.role,t.name AS "teamName",(SELECT count(*)::int FROM bd_team_member x WHERE x.team_id=m.team_id AND x.role='leader' AND x.end_date IS NULL) AS leaders,(SELECT count(*)::int FROM users u JOIN bd_team_member x ON x.person_code=u.person_code WHERE x.team_id=m.team_id AND x.role='leader' AND x.end_date IS NULL AND u.is_active=true AND u.status='active' AND u.role IN('staff','leader')) AS "leaderAccounts" FROM bd_team_member m JOIN bd_team t ON t.id=m.team_id WHERE m.team_id=$1 AND m.person_code=$2 AND m.end_date IS NULL AND m.start_date<=(now() AT TIME ZONE 'Asia/Tokyo')::date AND t.status='active'`,[region.team_id,actor.code])).rows[0]??null;}
 private captain(actor:Actor,region:any,membership:any){return actor.id===region.leader_id&&membership?.role==='leader'&&membership.leaders===1&&membership.leaderAccounts===1&&['staff','leader'].includes(actor.role);}
 private async context(client:PoolClient,gridId:number,actor:Actor,lock=false,execute=false){
  const initial=(await client.query('SELECT g.region_task_id,r.main_task_id FROM bd_grid_task g JOIN bd_region_task r ON r.id=g.region_task_id WHERE g.id=$1',[gridId])).rows[0];if(!initial)throw new BusinessError('NOT_FOUND','网格任务不存在',404);
  const main=(await client.query(`SELECT * FROM bd_main_task WHERE id=$1 ${lock?'FOR UPDATE':''}`,[initial.main_task_id])).rows[0],region=(await client.query(`SELECT * FROM bd_region_task WHERE id=$1 ${lock?'FOR UPDATE':''}`,[initial.region_task_id])).rows[0],grid=(await client.query(`SELECT * FROM bd_grid_task WHERE id=$1 ${lock?'FOR UPDATE':''}`,[gridId])).rows[0];const membership=await this.membership(client,actor,region),captain=this.captain(actor,region,membership),member=!!membership&&membership.role==='member'&&actor.id!==region.leader_id&&['staff','leader'].includes(actor.role),owner=grid.assignee_id===actor.id&&(captain||member);
  if(execute?!owner:actor.role!=='admin'&&!captain&&!owner)throw new BusinessError('FORBIDDEN','不是当前网格负责人或当前团队授权人员',403);if((lock||execute)&&main.status==='disabled')throw new BusinessError('BATCH_DISABLED','该批次已禁用，不能修改',409);return {main,region,grid,membership,captain,owner};
 }
 async tree(actorId:number,mainId:number){return this.db.transaction(async client=>{await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');const {main,regions}=await this.treeData(client,actorId,mainId);return {main,regions};});}
 private async treeData(client:PoolClient,actorId:number,mainId:number){const actor=await this.actor(client,actorId),main=(await client.query('SELECT id,title,status,progress,revision FROM bd_main_task WHERE id=$1',[mainId])).rows[0];if(!main)throw new BusinessError('NOT_FOUND','批次不存在',404);
  const regions=(await client.query(`SELECT r.id,r.region_id AS "sourceId",jsonb_build_object('name',r.snapshot->>'name') AS snapshot,r.team_id AS "teamId",r.leader_id AS "leaderId",r.status,r.progress,r.revision,team.name AS "teamName" FROM bd_region_task r LEFT JOIN bd_team team ON team.id=r.team_id WHERE r.main_task_id=$1 AND ($2 OR EXISTS(SELECT 1 FROM bd_team_member m JOIN bd_team t ON t.id=m.team_id WHERE m.team_id=r.team_id AND m.person_code=$3 AND m.end_date IS NULL AND m.start_date<=(now() AT TIME ZONE 'Asia/Tokyo')::date AND t.status='active' AND ((r.leader_id=$4 AND m.role='leader' AND (SELECT count(*) FROM bd_team_member x WHERE x.team_id=m.team_id AND x.role='leader' AND x.end_date IS NULL)=1 AND (SELECT count(*) FROM users u JOIN bd_team_member x ON x.person_code=u.person_code WHERE x.team_id=m.team_id AND x.role='leader' AND x.end_date IS NULL AND u.is_active=true AND u.status='active' AND u.role IN('staff','leader'))=1) OR (m.role='member' AND r.leader_id IS DISTINCT FROM $4 AND EXISTS(SELECT 1 FROM bd_grid_task g WHERE g.region_task_id=r.id AND g.assignee_id=$4))))) ORDER BY r.id`,[mainId,actor.role==='admin',actor.code,actor.id])).rows;if(!regions.length&&actor.role!=='admin')throw new BusinessError('FORBIDDEN','不能查看此批次',403);
  const grids=(await client.query(`SELECT g.id,g.region_task_id AS "regionId",g.grid_id AS "sourceId",jsonb_build_object('name',g.snapshot->>'name','area_km2',g.snapshot->'area_km2','stations',g.snapshot->'stations','keywords',g.snapshot->'keywords') AS snapshot,g.assignee_id AS "assigneeId",u.display_name AS "assigneeName",g.status,g.progress,g.total_stores AS total,g.completed_stores AS completed,g.revision FROM bd_grid_task g LEFT JOIN users u ON u.id=g.assignee_id JOIN bd_region_task r ON r.id=g.region_task_id WHERE g.region_task_id=ANY($1::int[]) AND ($2 OR r.leader_id=$3 OR g.assignee_id=$3) ORDER BY g.id`,[regions.map(row=>row.id),actor.role==='admin',actor.id])).rows;
  const members=(await client.query("SELECT m.team_id AS team,m.role,u.id,u.display_name AS name FROM bd_team_member m JOIN users u ON u.person_code=m.person_code JOIN bd_team t ON t.id=m.team_id WHERE m.team_id=ANY($1::int[]) AND m.end_date IS NULL AND m.role IN('leader','member') AND t.status='active' AND u.is_active=true AND u.status='active' AND u.role IN('staff','leader') ORDER BY u.id",[regions.filter(row=>actor.role==='admin'||row.leaderId===actor.id).map(row=>row.teamId).filter(Boolean)])).rows;
  return {actor,main,regions:regions.map(row=>({...row,canAssign:actor.role!=='admin'&&row.leaderId===actor.id&&members.some(member=>member.team===row.teamId&&member.id===actor.id&&member.role==='leader'),members:members.filter(member=>member.team===row.teamId),grids:grids.filter(grid=>grid.regionId===row.id)}))};}
 async mobileTree(actorId:number,mainId:number){return this.db.transaction(async client=>{await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');const {main,regions}=await this.treeData(client,actorId,mainId);const totals=(await client.query<{id:number;grids:number;total:number;completed:number}>('SELECT region_task_id AS id,count(*)::int AS grids,sum(total_stores)::int AS total,sum(completed_stores)::int AS completed FROM bd_grid_task WHERE region_task_id=ANY($1::int[]) GROUP BY region_task_id',[regions.map((r:{id:number})=>r.id)])).rows;return {main,regions:regions.map(r=>{const summary=totals.find(t=>t.id===r.id)??{grids:0,total:0,completed:0};return {...r,canAssign:r.canAssign&&!['completed','disabled'].includes(main.status),progress:batchProgress('region',summary.total,summary.completed).progress,summary};})};});}
 async regionDetail(actorId:number,id:number){return this.db.transaction(async client=>{await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');const initial=(await client.query('SELECT main_task_id FROM bd_region_task WHERE id=$1',[id])).rows[0];if(!initial)throw new BusinessError('NOT_FOUND','片区不存在',404);const {main,regions}=await this.treeData(client,actorId,initial.main_task_id);const region=regions.find(r=>r.id===id);if(!region)throw new BusinessError('FORBIDDEN','不能查看此片区',403);const summary=(await client.query('SELECT count(*)::int AS grids,coalesce(sum(total_stores),0)::int AS total,coalesce(sum(completed_stores),0)::int AS completed FROM bd_grid_task WHERE region_task_id=$1',[id])).rows[0];return {...region,canAssign:region.canAssign&&!['completed','disabled'].includes(main.status),progress:batchProgress('region',summary.total,summary.completed).progress,mainId:main.id,summary};});}
 async regionMapConfig(actorId:number,id:number){await this.regionDetail(actorId,id);const key=process.env.GOOGLE_PUBLIC_API_KEY?.trim()??'';return {enabled:!!key,key:key||null,provider:'google'};}
 async regionMap(actorId:number,id:number){return this.db.transaction(async client=>{await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');const initial=(await client.query('SELECT main_task_id FROM bd_region_task WHERE id=$1',[id])).rows[0];if(!initial)throw new BusinessError('NOT_FOUND','片区不存在',404);const tree=await this.treeData(client,actorId,initial.main_task_id),region=tree.regions.find(r=>r.id===id);if(!region)throw new BusinessError('FORBIDDEN','不能查看此片区',403);const rows=(await client.query<{id:number;geometry:unknown}>("SELECT id,snapshot->'geometry' AS geometry FROM bd_grid_task WHERE id=ANY($1::int[]) ORDER BY id",[region.grids.map((g:{id:number})=>g.id)])).rows;return {regionId:id,features:rows.filter(r=>r.geometry).map(r=>({type:'Feature',id:r.id,geometry:r.geometry,properties:{name:region.grids.find((g:{id:number;snapshot:{name?:string}})=>g.id===r.id)?.snapshot.name,assigned:region.grids.find((g:{id:number;assigneeId:number|null})=>g.id===r.id)?.assigneeId!==null}})),enabled:!!process.env.GOOGLE_PUBLIC_API_KEY,key:process.env.GOOGLE_PUBLIC_API_KEY||null};});}
 async visits(actorId:number,gridId:number,value:unknown){const input=parse(z.object({q:z.string().trim().max(200).default(''),state:z.enum(['','pending','completed']).default(''),businessStatus:z.enum(['','normal','abnormal','uncertain','closed']).default(''),page:z.coerce.number().int().min(1).max(10000).default(1),lat:z.coerce.number().finite().min(-90).max(90).optional(),lon:z.coerce.number().finite().min(-180).max(180).optional(),focus:identity.optional()}).strict().refine(v=>(v.lat===undefined)===(v.lon===undefined)),value);const started=performance.now();const response=await this.db.transaction(async client=>{await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');const actor=await this.actor(client,actorId),ctx=await this.context(client,gridId,actor),visits=await mobileVisits(client,gridId,input,['completed','disabled'].includes(ctx.main.status));return {...visits,actorId:actor.id,main:{id:ctx.main.id,title:ctx.main.title,status:ctx.main.status},region:{id:ctx.region.id,name:ctx.region.snapshot.name,leaderId:ctx.region.leader_id},grid:ctx.grid,canExecute:ctx.owner&&ctx.membership?.role==='member'&&!['completed','disabled'].includes(ctx.main.status)};});
 const {actorId:loggedActor,main,region,grid,gridTotal,keywordMatches,normalizedMatches,missingName,missingAddress,missingSource,missingLocation,...result}=response;
 if(input.q&&input.page===1&&!input.focus){
  const reason=result.total>0?'':gridTotal===0?'empty_grid':keywordMatches>0?'filters_excluded':normalizedMatches>0?'normalization_candidate':'unresolved';
  try{await this.db.query(`INSERT INTO bd_store_search_log(actor_id,main_task_id,region_task_id,grid_task_id,query_text,page,task_state,business_status,used_location,total_in_grid,matched_count,keyword_match_count,normalized_match_count,missing_name_count,missing_address_count,missing_source_count,missing_location_count,duration_ms,no_result_reason,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,timezone('UTC',now()))`,[loggedActor,main.id,region.id,grid.id,input.q,input.page,input.state,input.businessStatus,input.lat!==undefined,gridTotal,result.total,keywordMatches,normalizedMatches,missingName,missingAddress,missingSource,missingLocation,Math.round(performance.now()-started),reason]);}
  catch{console.error(JSON.stringify({event:'store_search_log_failed'}));}
 }
 return {main,region,grid,...result};}

 async placeLookup(actorId:number,id:number){
  const before=await this.db.transaction(async client=>{const actor=await this.actor(client,actorId),store=(await client.query('SELECT * FROM bd_store_task WHERE id=$1',[id])).rows[0];if(!store)throw new BusinessError('NOT_FOUND','门店不存在',404);const ctx=await this.context(client,store.grid_task_id,actor,false,true);if(ctx.main.status==='completed')throw new BusinessError('BATCH_COMPLETED','批次已完成',409);const profile=(await client.query('SELECT * FROM bd_merchant_detail WHERE store_id=$1',[store.store_id])).rows[0];if(!profile||profile.lat===null||profile.lon===null)throw new BusinessError('LOCATION_REQUIRED','请先保存门店坐标',409);return {store,profile};});
  let googlePlaceId:string|null=null,state='unconfigured';const key=process.env.GOOGLE_PLACES_API_KEY;
  if(key){try{const response=await fetch('https://places.googleapis.com/v1/places:searchText',{method:'POST',headers:{'Content-Type':'application/json','X-Goog-Api-Key':key,'X-Goog-FieldMask':'places.id'},body:JSON.stringify({textQuery:before.store.snapshot.name,languageCode:'ja',locationBias:{circle:{center:{latitude:before.profile.lat,longitude:before.profile.lon},radius:50}}}),signal:AbortSignal.timeout(12000)});if(!response.ok)throw Error();const data=await response.json() as {places?:{id?:string}[]};const ids=[...new Set((data.places??[]).map(p=>p.id).filter((v):v is string=>typeof v==='string'&&v.length>0&&v.length<=255))];state=ids.length===1?'candidate':ids.length?'ambiguous':'no_match';googlePlaceId=ids.length===1?ids[0]:null;}catch{state='failed';}}
  return this.db.transaction(async client=>{const actor=await this.actor(client,actorId,true),ctx=await this.context(client,before.store.grid_task_id,actor,true,true);if(ctx.main.status==='completed')throw new BusinessError('BATCH_COMPLETED','批次已完成',409);const result=await client.query('UPDATE bd_merchant_detail SET google_place_id=$1,place_lookup_state=$2,revision=revision+1,updated_at=now() WHERE store_id=$3 AND revision=$4 RETURNING revision',[googlePlaceId,state,before.store.store_id,before.profile.revision]);if(!result.rowCount)throw new BusinessError('LOCATION_CHANGED','位置已变化，请重新查询',409);await this.event(client,actor,ctx,'store','lookup_store_place',{googlePlaceId:before.profile.google_place_id},{googlePlaceId,placeLookupState:state},before.store);return {googlePlaceId,placeLookupState:state,profileRevision:result.rows[0].revision};});
 }
 private async autoSnapshot(client:PoolClient,actorId:number,id:number,input:{memberIds:number[];includeBatch:boolean;useEfficiency:boolean}){
  const initial=(await client.query('SELECT main_task_id FROM bd_region_task WHERE id=$1',[id])).rows[0];if(!initial)throw new BusinessError('NOT_FOUND','片区不存在',404);const {actor,main,regions}=await this.treeData(client,actorId,initial.main_task_id),region=regions.find(r=>r.id===id);if(!region?.canAssign)throw new BusinessError('FORBIDDEN','仅当前片区有效队长可智能分配',403);if(['completed','disabled'].includes(main.status)||region.status==='completed')throw new BusinessError('BATCH_CLOSED','已完成或禁用批次不能重新分配',409);
  const ids=[...new Set(input.memberIds)].sort((a,b)=>a-b);if(!ids.length||ids.some(id=>!region.members.some((m:{id:number})=>m.id===id)))throw new BusinessError('ASSIGNEE_UNAVAILABLE','请选择本队在岗人员',409);
  const grids=(await client.query("SELECT g.id,g.revision,g.status,g.assignee_id,g.snapshot->'geometry' AS geometry,(SELECT count(*)::int FROM bd_store_task s WHERE s.grid_task_id=g.id AND s.status<>'completed') AS workload FROM bd_grid_task g WHERE g.region_task_id=$1 ORDER BY g.id",[id])).rows;
  const loads=input.includeBatch?(await client.query("SELECT g.assignee_id AS id,count(s.id)::int AS load FROM bd_grid_task g JOIN bd_region_task r ON r.id=g.region_task_id JOIN bd_store_task s ON s.grid_task_id=g.id AND s.status<>'completed' WHERE r.main_task_id=$1 AND r.id<>$2 AND g.status<>'completed' AND g.assignee_id=ANY($3::int[]) GROUP BY g.assignee_id",[main.id,id,ids])).rows:[];
  const rates=input.useEfficiency?(await client.query("SELECT actor_id AS id,count(*)::float/count(DISTINCT created_at::date) AS rate,count(*)::int AS samples FROM bd_task_event WHERE event_type='complete_store_task' AND actor_id=ANY($1::int[]) AND main_task_id<>$2 AND created_at>=now()-interval '60 days' GROUP BY actor_id",[ids,main.id])).rows:[];const eligible=rates.filter(r=>r.samples>=5),baseline=eligible.length?eligible.reduce((a,r)=>a+r.rate,0)/eligible.length:0;
  const members=ids.map(id=>({id,name:region.members.find((m:{id:number;name:string})=>m.id===id)!.name,load:loads.find(l=>l.id===id)?.load??0,efficiency:input.useEfficiency&&baseline&&eligible.some(r=>r.id===id)?Math.min(1.5,Math.max(.67,eligible.find(r=>r.id===id)!.rate/baseline)):1}));
  const value={actor:actor.id,main:{id:main.id,status:main.status,revision:main.revision},region:{id,revision:region.revision,teamId:region.teamId,leaderId:region.leaderId},grids,members,input};return {actor,main,region,grids,members,fingerprint:createHash('sha256').update(JSON.stringify(value)).digest('hex')};
 }
 async autoPreview(actorId:number,id:number,value:unknown,model:ReportModel=configuredJsonModel){const input=parse(z.object({memberIds:z.array(identity).min(1).max(200),includeBatch:z.boolean().default(true),useEfficiency:z.boolean().default(false)}).strict(),value);const snap=await this.db.transaction(c=>this.autoSnapshot(c,actorId,id,input));const grids=snap.grids.filter(g=>g.status!=='completed');if(!grids.length)throw new BusinessError('AUTO_ASSIGN_EMPTY','没有未完成网格',409);
  // Validate geometry before calling a paid provider. No database lock is held during the model request.
  const equal=Object.fromEntries(snap.members.map(m=>[String(m.id),1]));await connectedPlan(grids,snap.members,equal);let data:unknown;try{data=(await model('为巡店网格提供人员容量权重与简短理由。只返回JSON {"weights":{"<id>":1},"reason":"..."}。每名给定人员必须有唯一0.75—1.25有限权重。不要生成其他人员或任何派发。\n'+JSON.stringify({members:snap.members,workloads:grids.map(g=>({id:g.id,workload:g.workload})),rules:{includeBatch:input.includeBatch,useEfficiency:input.useEfficiency}}),'zh','batch-auto:'+snap.fingerprint)).data;}catch{throw new BusinessError('AUTO_AI_FAILED','智能方案生成失败，现有分配未改变，请重试',502);}
  const decision=parse(z.object({weights:z.record(z.string(),z.number().finite().min(.75).max(1.25)),reason:z.string().trim().min(1).max(1500)}).strict(),data);if(Object.keys(decision.weights).length!==snap.members.length||snap.members.some(m=>decision.weights[String(m.id)]===undefined))throw new BusinessError('AUTO_AI_INVALID','AI返回的人员不完整，请重新生成',409);
  const assignments=await connectedPlan(grids,snap.members,decision.weights),payload={actorId,regionId:id,input,fingerprint:snap.fingerprint,assignments,decision,expires:Date.now()+600000};const encoded=Buffer.from(JSON.stringify(payload)).toString('base64url'),signature=createHmac('sha256',autoSecret).update(encoded).digest('hex');return {token:encoded+'.'+signature,reason:decision.reason,lockedCompleted:snap.grids.length-grids.length,members:snap.members.map(m=>({...m,grids:assignments.filter(a=>a.assigneeId===m.id).length,workload:assignments.filter(a=>a.assigneeId===m.id).reduce((n,a)=>n+a.workload,0)}))};
 }
 async autoApply(actorId:number,id:number,value:unknown){const input=parse(z.object({token:z.string().max(200000)}).strict(),value);const [encoded,signature,...rest]=input.token.split('.');const expected=createHmac('sha256',autoSecret).update(encoded??'').digest('hex');if(rest.length||!signature||signature.length!==expected.length||!timingSafeEqual(Buffer.from(signature),Buffer.from(expected)))throw new BusinessError('AUTO_PREVIEW_INVALID','预览无效，请重新生成',409);let payload:any;try{payload=JSON.parse(Buffer.from(encoded,'base64url').toString());}catch{throw new BusinessError('AUTO_PREVIEW_INVALID','预览无效，请重新生成',409);}if(payload.actorId!==actorId||payload.regionId!==id||payload.expires<Date.now())throw new BusinessError('AUTO_PREVIEW_EXPIRED','预览已过期或不属于当前人员，请重新生成',409);
  return this.db.transaction(async client=>{await client.query("SELECT pg_advisory_xact_lock_shared(hashtext('teams-write'),0)");const initial=(await client.query('SELECT main_task_id FROM bd_region_task WHERE id=$1',[id])).rows[0];if(!initial)throw new BusinessError('NOT_FOUND','片区不存在',404);await client.query('SELECT id FROM bd_main_task WHERE id=$1 FOR UPDATE',[initial.main_task_id]);await client.query('SELECT id FROM bd_region_task WHERE id=$1 FOR UPDATE',[id]);await client.query('SELECT id FROM bd_grid_task WHERE region_task_id=$1 ORDER BY id FOR UPDATE',[id]);const snap=await this.autoSnapshot(client,actorId,id,payload.input);if(snap.fingerprint!==payload.fingerprint)throw new BusinessError('TASK_CHANGED','预览数据已变化，请重新生成',409);const membership=await this.membership(client,snap.actor,{team_id:snap.region.teamId});for(const a of payload.assignments){const g=(await client.query('SELECT * FROM bd_grid_task WHERE id=$1',[a.gridId])).rows[0];await client.query("UPDATE bd_grid_task SET assignee_id=$1,status=CASE WHEN progress>0 THEN 'in_progress' ELSE 'assigned' END,revision=revision+1 WHERE id=$2 AND status<>'completed'",[a.assigneeId,g.id]);await this.event(client,snap.actor,{main:snap.main,region:{...snap.region,team_id:snap.region.teamId,snapshot:snap.region.snapshot},grid:g,membership},'grid','auto_assign_grid',{assignee_id:g.assignee_id},{assignee_id:a.assigneeId,reason:payload.decision.reason});}return {ok:true,grids:payload.assignments.length};});
 }
 async events(actorId:number,mainId:number,query:unknown){return this.db.transaction(async client=>{await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');const {actor,regions}=await this.treeData(client,actorId,mainId);return readBatchEvents(client,mainId,{admin:actor.role==='admin',captainRegions:regions.filter(r=>r.leaderId===actor.id).map(r=>r.id),regions:regions.map(r=>({id:r.id,label:r.snapshot.name||String(r.id)})),grids:regions.flatMap(r=>r.grids.map((g:{id:number;snapshot:{name?:string}})=>({id:g.id,regionId:r.id,label:g.snapshot.name||String(g.id)})))},query);});}
 async map(actorId:number,gridId:number,value:unknown){
  const input=parse(z.object({after:z.coerce.number().int().min(0).max(2147483647).default(0),limit:z.coerce.number().int().min(1).max(200).default(200)}).strict(),value);
  return this.db.transaction(async client=>{
   await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');const actor=await this.actor(client,actorId),context=await this.context(client,gridId,actor);
   const boundary=input.after?null:context.grid.snapshot?.geometry??null;
   if(boundary&&Buffer.byteLength(JSON.stringify(boundary))>1024*1024)throw new BusinessError('MAP_GEOMETRY_TOO_LARGE','批次边界过大，请使用店铺列表',409);
   return {...await batchMapRows(client,gridId,input.after,input.limit),boundary,gridId,mainId:context.main.id,total:context.grid.total_stores,canExecute:context.owner&&!['completed','disabled'].includes(context.main.status)};
  });
 }
 async location(actorId:number,storeId:number,value:unknown){
  const input=parse(z.object({revision,profileRevision:revision,accuracy:z.enum(['pending','accurate','nonexistent','doubtful']),lat:z.number().finite().min(-90).max(90).optional(),lon:z.number().finite().min(-180).max(180).optional()}).strict().refine(value=>(value.lat===undefined)===(value.lon===undefined)),value);
  return this.db.transaction(async client=>{
   await client.query("SELECT pg_advisory_xact_lock_shared(hashtext('teams-write'),0)");const actor=await this.actor(client,actorId,true);
   const {rows:[initial]}=await client.query('SELECT grid_task_id FROM bd_store_task WHERE id=$1',[storeId]);if(!initial)throw new BusinessError('NOT_FOUND','店铺任务不存在',404);
   const context=await this.context(client,initial.grid_task_id,actor,true,true);
   if(context.main.status==='completed')throw new BusinessError('BATCH_COMPLETED','该批次已完成，不能修改定位',409);
   const {rows:[store]}=await client.query('SELECT * FROM bd_store_task WHERE id=$1 FOR UPDATE',[storeId]);if(!store)throw new BusinessError('NOT_FOUND','店铺任务不存在',404);
   const update=await writeLocation(client,store,input);await this.event(client,actor,context,'store','verify_store_location',update.before,update.after,store);return update.result;
  });
 }
 async stores(actorId:number,gridId:number,query:unknown){const input=parse(z.object({page:z.coerce.number().int().min(1).max(10000).default(1),q:z.string().trim().max(200).default(''),focus:z.coerce.number().int().positive().max(2147483647).optional(),state:z.enum(['','pending','completed']).default('')}).strict(),query);return this.db.transaction(async client=>{await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');const actor=await this.actor(client,actorId),context=await this.context(client,gridId,actor);const result=(await client.query(`WITH matching AS(SELECT id,store_id AS "storeId",snapshot,status,result,actor_id AS "actorId",revision FROM bd_store_task WHERE grid_task_id=$1 AND ($2='' OR store_id ILIKE $2 OR snapshot->>'name' ILIKE $2 OR snapshot->>'address' ILIKE $2) AND ($3='' OR status=$3) AND ($5::int IS NULL OR id=$5)),paged AS(SELECT * FROM matching ORDER BY id LIMIT 30 OFFSET $4)SELECT (SELECT count(*)::int FROM matching) AS total,coalesce((SELECT json_agg(row) FROM paged row),'[]') AS items`,[gridId,input.q?'%'+input.q+'%':'',input.state,(input.page-1)*30,input.focus??null])).rows[0];const editable=!['completed','disabled'].includes(context.main.status);const profiles=editable?(await client.query('SELECT store_id,photo_urls,revision FROM bd_merchant_detail WHERE store_id=ANY($1::text[])',[result.items.map((row:any)=>row.storeId)])).rows:[];const byStore=new Map(profiles.map(row=>[row.store_id,row]));const items=result.items.map((row:any)=>{const profile=byStore.get(row.storeId);return {...row,photos:profile?.photo_urls??row.result?.photo_urls??[],photoRevision:profile?.revision??0};});return {...result,items,page:input.page,pageSize:30,main:{id:context.main.id,title:context.main.title,status:context.main.status,progress:context.main.progress},region:{id:context.region.id,snapshot:context.region.snapshot,progress:context.region.progress},grid:context.grid,canExecute:context.owner&&!['completed','disabled'].includes(context.main.status)};});}
 private async event(client:PoolClient,actor:Actor,context:any,level:'store'|'grid'|'region'|'main',kind:string,before:unknown,after:unknown,store?:any){const main=context.main,region=level==='main'?null:context.region,grid=['grid','store'].includes(level)?context.grid:null;const id=level==='store'?store.id:level==='grid'?context.grid.id:level==='region'?context.region.id:main.id;
  await client.query("INSERT INTO bd_log(domain,action,ref_id,ref_label,field,old_value,new_value,actor,actor_name,note,created_at) VALUES('task',$1,$2,$3,'operations',$4,$5,$6,$7,'',now())",[kind,id,String(id),JSON.stringify(before),JSON.stringify(after),actor.username,actor.name]);
  await client.query(`INSERT INTO bd_task_event(main_task_id,main_label,region_task_id,region_label,grid_task_id,grid_label,store_task_id,store_label,store_source_id,event_type,actor_id,actor_label,actor_role,team_id,team_label,request_id,old_value,new_value,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,now())`,[main.id,main.title,region?.id??null,region?.snapshot?.name??'',grid?.id??null,grid?String(grid.grid_id):'',store?.id??null,store?.snapshot?.name??'',store?.store_id??'',kind,actor.id,actor.name||actor.username,actor.role==='admin'||!region?actor.role:context.membership?.role||actor.role,region?.team_id??null,region?context.membership?.teamName??(await client.query('SELECT name FROM bd_team WHERE id=$1',[region.team_id])).rows[0]?.name??'':'',level==='store'&&['complete_store_task','update_store_result'].includes(kind)?store.request_id:null,JSON.stringify(before),JSON.stringify(after)]);
 }
 async disable(actorId:number,mainId:number,value:unknown){const input=parse(z.object({revision}).strict(),value);return this.db.transaction(async client=>{
  await client.query("SELECT pg_advisory_xact_lock_shared(hashtext('teams-write'),0)");
  const actor=await this.actor(client,actorId,true);if(actor.role!=='admin')throw new BusinessError('FORBIDDEN','仅管理员可禁用批次',403);
  const {rows:[main]}=await client.query('SELECT * FROM bd_main_task WHERE id=$1 FOR UPDATE',[mainId]);
  if(!main)throw new BusinessError('NOT_FOUND','批次不存在',404);
  if(main.revision!==input.revision)throw new BusinessError('REVISION_CONFLICT','批次已变化，请刷新后重试',409);
  if(main.status==='disabled')return {id:main.id,revision:main.revision,reused:true};
  const before={status:main.status};
  await client.query("UPDATE bd_main_task SET status='disabled',revision=revision+1 WHERE id=$1",[mainId]);
  await this.event(client,actor,{main},'main','disable_batch',before,{status:'disabled'});
  return {id:main.id,revision:main.revision+1,reused:false};
 });}
 async creationRegions(actorId:number,value:unknown){
  const input=parse(z.object({q:z.string().trim().max(128).default(''),page:z.coerce.number().int().min(1).max(10000).default(1)}).strict(),value);
  return this.db.transaction(async client=>{
   await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');
   if((await this.actor(client,actorId)).role!=='admin')throw new BusinessError('FORBIDDEN','仅管理员可创建批次',403);
   const params=[input.q,(input.page-1)*30];
   const {rows:[row]}=await client.query(`WITH matching AS (
     SELECT r.id,r.name,r.release_id AS "releaseId" FROM bd_region r WHERE r.state<>'archived'
       AND ($1::text='' OR strpos(lower(r.name),lower($1))>0 OR r.id::text=$1)
   ), paged AS (SELECT * FROM matching ORDER BY name,id LIMIT 30 OFFSET $2)
   SELECT (SELECT count(*)::int FROM matching) AS total,
     coalesce((SELECT json_agg(p ORDER BY p.name,p.id) FROM paged p),'[]') AS items`,params);
   return {...row,page:input.page,pageSize:30};
  });
 }
 async create(actorId:number,value:unknown){
  const {title,regionIds}=parse(z.object({title:z.string().trim().min(1).max(160),regionIds:z.array(z.number().int().positive().max(2147483647)).min(1).max(2000)}).strict(),value);
  const ids=[...new Set(regionIds)].sort((a,b)=>a-b);
  return this.db.transaction(async client=>{
   const actor=await this.actor(client,actorId,true);
   if(actor.role!=='admin')throw new BusinessError('FORBIDDEN','仅管理员可创建批次',403);
   await client.query(`SELECT id FROM bd_zone_release WHERE id IN(SELECT release_id FROM bd_region WHERE id=ANY($1::int[])) ORDER BY id FOR UPDATE`,[ids]);
   const {rows:regions}=await client.query(`SELECT id,release_id FROM bd_region WHERE id=ANY($1::int[]) AND state<>'archived' ORDER BY id FOR UPDATE`,[ids]);
   if(regions.length!==ids.length)throw new BusinessError('BATCH_REGION_UNAVAILABLE','片区不存在或已归档，请刷新后重试',409);
   const {rows:grids}=await client.query(`SELECT id,zone_id,release_id FROM bd_work_grid WHERE zone_id=ANY($1::int[]) AND state<>'disabled' ORDER BY id FOR UPDATE`,[ids]);
   if(new Set(grids.map(row=>row.zone_id)).size!==ids.length)throw new BusinessError('BATCH_REGION_EMPTY','空片区不能创建巡店任务',409);
   const releases=new Map(regions.map(row=>[row.id,row.release_id]));
   if(grids.some(row=>row.release_id!==releases.get(row.zone_id)))throw new BusinessError('BATCH_SOURCE_MISMATCH','网格与片区来源不一致',409);
   const gridIds=grids.map(row=>row.id);
   // Materialize the validated source once. The merchant dataset stays in PostgreSQL.
   await client.query(`CREATE TEMP TABLE batch_creation_sources ON COMMIT DROP AS
     SELECT m.grid_id,m.source_id,coalesce(w.name,s.name) AS name,
       coalesce(w.address,s.address,'') AS address,coalesce(w.lat,s.lat)::float8 AS lat,coalesce(w.lon,s.lon)::float8 AS lon
     FROM bd_grid_store m LEFT JOIN bd_work_store w ON w.id=m.source_id
       LEFT JOIN public.kanto_merchants_v20261029 s ON s.place_id=m.source_id WHERE m.grid_id=ANY($1::int[])`,[gridIds]);
   const {rows:[counts]}=await client.query(`SELECT count(*)::int AS total,count(DISTINCT source_id)::int AS unique_count,
      count(*) FILTER(WHERE name IS NULL OR name='')::int AS missing FROM pg_temp.batch_creation_sources`);
   if(counts.total!==counts.unique_count)throw new BusinessError('BATCH_DUPLICATE_STORE','同一批次存在重复店铺归属',409);
   if(counts.missing)throw new BusinessError('BATCH_STORE_SOURCE_MISSING','存在缺失源信息的店铺，不能生成任务快照',409);
   const empty=counts.total===0;
   const {rows:[main]}=await client.query(`INSERT INTO bd_main_task(title,status,progress,created_by,created_at,started_at,completed_at,revision)
      VALUES($1,$2,$3,$4,now(),now(),CASE WHEN $5::boolean THEN now() END,0) RETURNING id,title,status,progress`,[title,empty?'completed':'active',empty?100:0,actor.id,empty]);
   await client.query(`INSERT INTO bd_region_task(main_task_id,region_id,snapshot,team_id,leader_id,status,progress,revision,created_at)
      SELECT $1,r.id,json_build_object('name',r.name,'geometry',r.geometry,'area_km2',r.area_km2,'source_id',r.source_id),
      r.team_id,r.leader_id,'pending',0,0,now() FROM bd_region r WHERE r.id=ANY($2::int[]) ORDER BY r.id`,[main.id,ids]);
   await client.query(`WITH counts AS(SELECT grid_id,count(*)::int AS n FROM pg_temp.batch_creation_sources GROUP BY grid_id)
      INSERT INTO bd_grid_task(region_task_id,grid_id,snapshot,status,progress,total_stores,completed_stores,revision,created_at)
      SELECT r.id,g.id,json_build_object('geometry',g.geometry,'area_km2',g.area_km2,'source_id',g.source_id,'name',g.name,
        'stations',coalesce(g.stations,'[]'::json),'keywords',coalesce(g.keywords,'[]'::json)),
        CASE WHEN c.n IS NULL THEN 'completed' ELSE 'pending' END,CASE WHEN c.n IS NULL THEN 100 ELSE 0 END,coalesce(c.n,0),0,0,now()
      FROM bd_work_grid g JOIN bd_region_task r ON r.region_id=g.zone_id AND r.main_task_id=$1 LEFT JOIN counts c ON c.grid_id=g.id
      WHERE g.id=ANY($2::int[]) ORDER BY g.id`,[main.id,gridIds]);
   await client.query(`UPDATE bd_region_task r SET status='completed',progress=100 WHERE main_task_id=$1
       AND NOT EXISTS(SELECT 1 FROM bd_grid_task g WHERE g.region_task_id=r.id AND g.total_stores>0)`,[main.id]);
   await client.query(`INSERT INTO bd_store_task(grid_task_id,store_id,snapshot,status,result,updated_at,revision)
     SELECT g.id,s.source_id,json_build_object('name',s.name,'address',s.address,'lat',s.lat,'lon',s.lon),'pending','{}',now(),0
     FROM pg_temp.batch_creation_sources s JOIN bd_grid_task g ON g.grid_id=s.grid_id JOIN bd_region_task r ON r.id=g.region_task_id
     WHERE r.main_task_id=$1 ORDER BY g.id,s.source_id`,[main.id]);
   await this.event(client,actor,{main},'main','create_batch',{},{title,regions:ids,grids:grids.length,stores:counts.total});
   await client.query(`WITH pending AS (
     SELECT g.id AS ref,r.id AS region_id,r.snapshot->>'name' AS region_name,g.id AS grid_id,g.grid_id::text AS grid_label,r.team_id,
       'grid_auto_completed_empty' AS event_type,'no_store_tasks' AS reason,1 AS depth
     FROM bd_grid_task g JOIN bd_region_task r ON r.id=g.region_task_id WHERE r.main_task_id=$1 AND g.total_stores=0
     UNION ALL SELECT r.id,r.id,r.snapshot->>'name',NULL,'',r.team_id,'region_auto_completed_empty','all_grids_empty',2
       FROM bd_region_task r WHERE r.main_task_id=$1 AND r.status='completed'
   ), inserted AS (
     INSERT INTO bd_task_event(main_task_id,main_label,region_task_id,region_label,grid_task_id,grid_label,event_type,actor_id,actor_label,actor_role,team_id,team_label,old_value,new_value,created_at)
     SELECT $1,$2,p.region_id,p.region_name,p.grid_id,p.grid_label,p.event_type,$3,$4,'admin',p.team_id,coalesce(t.name,''),'{}',
       json_build_object('status','completed','reason',p.reason),now() FROM pending p LEFT JOIN bd_team t ON t.id=p.team_id ORDER BY p.depth,p.ref
     RETURNING id
   ) INSERT INTO bd_log(domain,action,ref_id,ref_label,field,old_value,new_value,actor,actor_name,note,created_at)
      SELECT 'task',event_type,ref,ref::text,'operations','{}',json_build_object('status','completed','reason',reason)::text,$5,$4,'',now()
      FROM pending p ORDER BY p.depth,p.ref`,[main.id,title,actor.id,actor.name||actor.username,actor.username]);
   if(empty)await this.event(client,actor,{main},'main','batch_auto_completed_empty',{},{status:'completed',reason:'all_regions_empty'});
   return {...main,regionCount:ids.length,gridCount:grids.length,storeCount:counts.total};
  });
 }
 async assignRegion(actorId:number,id:number,value:unknown){const input=parse(z.object({revision,teamId:identity.nullable()}).strict(),value);return this.db.transaction(async client=>{
  await client.query("SELECT pg_advisory_xact_lock_shared(hashtext('teams-write'),0)");const actor=await this.actor(client,actorId,true);if(actor.role!=='admin')throw new BusinessError('FORBIDDEN','仅管理员可派发片区任务',403);const initial=(await client.query('SELECT main_task_id FROM bd_region_task WHERE id=$1',[id])).rows[0];if(!initial)throw new BusinessError('NOT_FOUND','片区任务不存在',404);const main=(await client.query('SELECT * FROM bd_main_task WHERE id=$1 FOR UPDATE',[initial.main_task_id])).rows[0],region=(await client.query('SELECT * FROM bd_region_task WHERE id=$1 FOR UPDATE',[id])).rows[0];if(['completed','disabled'].includes(main.status))throw new BusinessError('BATCH_CLOSED','批次已完成或禁用，不能派发',409);if(region.revision!==input.revision)throw new BusinessError('TASK_CHANGED','片区任务已变化',409);
  let leader:number|null=null;if(input.teamId!==null){const candidates=(await client.query("SELECT u.id,(SELECT count(*)::int FROM bd_team_member x WHERE x.team_id=t.id AND x.role='leader' AND x.end_date IS NULL) AS leaders FROM bd_team t JOIN bd_team_member m ON m.team_id=t.id JOIN users u ON u.person_code=m.person_code WHERE t.id=$1 AND t.status='active' AND m.role='leader' AND m.end_date IS NULL AND m.start_date<=(now() AT TIME ZONE 'Asia/Tokyo')::date AND u.is_active=true AND u.status='active' AND u.role IN('staff','leader')",[input.teamId])).rows;if(candidates.length!==1||candidates[0].leaders!==1)throw new BusinessError('TEAM_CAPTAIN_INVALID','团队必须只有一名在岗且已建账号的队长',409);leader=candidates[0].id;}
  const before={team_id:region.team_id,leader_id:region.leader_id};if(region.team_id!==input.teamId)await client.query("UPDATE bd_grid_task SET assignee_id=NULL,revision=revision+1,status=CASE WHEN status='completed' THEN status WHEN progress>0 THEN 'in_progress' ELSE 'pending' END WHERE region_task_id=$1",[id]);await client.query('UPDATE bd_region_task SET team_id=$1,leader_id=$2,revision=revision+1 WHERE id=$3',[input.teamId,leader,id]);region.team_id=input.teamId;region.leader_id=leader;const context={main,region,grid:null,membership:null};await this.event(client,actor,context,'region','assign_region_task',before,{team_id:input.teamId,leader_id:leader,main_task_id:main.id});return {ok:true,mainId:main.id};
 });}
 async assignGrid(actorId:number,id:number,value:unknown){const input=parse(z.object({revision,assigneeId:identity.nullable()}).strict(),value);return this.db.transaction(async client=>{
  await client.query("SELECT pg_advisory_xact_lock_shared(hashtext('teams-write'),0)");const actor=await this.actor(client,actorId,true),context=await this.context(client,id,actor,true);if(!context.captain)throw new BusinessError('FORBIDDEN','仅本批次片区的当前队长可派发网格',403);if(context.main.status==='completed')throw new BusinessError('BATCH_COMPLETED','批次已完成',409);if(context.grid.revision!==input.revision)throw new BusinessError('TASK_CHANGED','网格任务已变化',409);
  if(input.assigneeId!==null){const target=await this.actor(client,input.assigneeId,true),membership=await this.membership(client,target,context.region),valid=this.captain(target,context.region,membership)||membership?.role==='member'&&['staff','leader'].includes(target.role)&&target.id!==context.region.leader_id;if(!valid)throw new BusinessError('ASSIGNEE_UNAVAILABLE','负责人必须是本队在岗队长或成员',409);}
  const before={assignee_id:context.grid.assignee_id};await client.query("UPDATE bd_grid_task SET assignee_id=$1,revision=revision+1,status=CASE WHEN status='completed' THEN status WHEN $1::int IS NULL THEN 'pending' ELSE 'assigned' END WHERE id=$2",[input.assigneeId,id]);await this.event(client,actor,context,'grid','assign_grid_task',before,{assignee_id:input.assigneeId,main_task_id:context.main.id});return {ok:true,mainId:context.main.id};
 });}

 async proposalPreflight(actorId:number,gridId:number){return this.db.transaction(async client=>{
  const actor=await this.actor(client,actorId);return this.context(client,gridId,actor,false,true);
 });}
 async submitProposal(actorId:number,gridId:number,value:unknown,file?:Buffer){
  const input=parse(z.object({name:z.string().trim().min(1).max(255),address:z.string().trim().min(1).max(1000),lat:z.number().min(-90).max(90),lon:z.number().min(-180).max(180),requestId:z.string().uuid(),proposalId:z.number().int().positive().max(2147483647).optional()}).strict(),value);
  const preflight=await this.proposalPreflight(actorId,gridId);
  if(!gridCovers(preflight.grid.snapshot?.geometry,input.lon,input.lat))throw new BusinessError('PROPOSAL_OUTSIDE_GRID','定位不在当前网格内',409);
  if(!file?.length)throw new BusinessError('PHOTO_INVALID','请上传一张门店照片',400);
  const [photoRef]=await prepareBatchPhotos('proposal:grid:'+gridId,[file]);
  const payload={name:input.name,address:input.address,lat:input.lat,lon:input.lon,photo_ref:photoRef};
  return this.db.transaction(async client=>{
   await client.query("SELECT pg_advisory_xact_lock_shared(hashtext('teams-write'),0)");const actor=await this.actor(client,actorId,true);
   await client.query("SELECT pg_advisory_xact_lock(hashtext('proposal-request'),hashtext($1))",[input.requestId]);
   const {main,grid}=await this.context(client,gridId,actor,true,true);
   const {rows:[existing]}=await client.query('SELECT * FROM bd_store_proposal WHERE request_id=$1',[input.requestId]);
   if(existing){if(existing.submitted_by!==actorId||existing.grid_task_id!==gridId||Object.entries(payload).some(([key,val])=>existing.payload[key]!==val))throw new BusinessError('REQUEST_ID_CONFLICT','提交标识与原申请冲突',409);return {id:existing.id,state:existing.state,reused:true};}
   if(main.status==='completed')throw new BusinessError('BATCH_COMPLETED','该批次已完成',409);
   if(!gridCovers(grid.snapshot?.geometry,input.lon,input.lat))throw new BusinessError('PROPOSAL_OUTSIDE_GRID','定位不在当前网格内',409);
   let row:any;let before={};
   if(input.proposalId){const {rows:[old]}=await client.query('SELECT * FROM bd_store_proposal WHERE id=$1 FOR UPDATE',[input.proposalId]);
    if(!old||old.submitted_by!==actorId||old.grid_task_id!==gridId||old.state!=='rejected')throw new BusinessError('FORBIDDEN','仅可重新提交自己被驳回的申请',403);before={state:old.state};
    row=(await client.query(`UPDATE bd_store_proposal SET payload=$1,request_id=$2,state='pending',reason='',reviewed_by=NULL,reviewed_at=NULL,revision=revision+1 WHERE id=$3 RETURNING id,state`,[JSON.stringify(payload),input.requestId,old.id])).rows[0];
   }else row=(await client.query(`INSERT INTO bd_store_proposal(grid_task_id,submitted_by,request_id,payload,state,revision,reason,created_at) VALUES($1,$2,$3,$4,'pending',0,'',now()) RETURNING id,state`,[gridId,actorId,input.requestId,JSON.stringify(payload)])).rows[0];
   await this.proposalLog(client,actor,row.id,'submit_store_proposal',before,{grid_task_id:gridId});return {...row,reused:false};
  });
 }
 private async proposalLog(client:PoolClient,actor:Actor,id:number,action:string,before:unknown,after:unknown){
  await client.query(`INSERT INTO bd_log(domain,action,ref_id,ref_label,field,old_value,new_value,actor,actor_name,note,created_at) VALUES('task',$1,$2::int,($2::int)::text,'operations',$3,$4,$5,$6,'',now())`,[action,id,JSON.stringify(before),JSON.stringify(after),actor.username,actor.name]);
 }
 async batchProposals(actorId:number,mainId:number,value:unknown){
  const input=parse(z.object({page:z.coerce.number().int().min(1).max(10000).default(1),state:z.enum(['','pending','approved','rejected']).default('pending'),region:z.coerce.number().int().positive().optional(),grid:z.coerce.number().int().positive().optional(),submitter:z.coerce.number().int().positive().optional()}).strict(),value);
  return this.db.transaction(async client=>{await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');
   const {actor,main,regions}=await this.treeData(client,actorId,mainId);
   const grids=regions.flatMap(r=>r.grids.map((g:{id:number;snapshot:{name?:string}})=>({id:g.id,name:g.snapshot.name||'#'+g.id,regionId:r.id,canReview:r.canAssign}))),reviewIds=grids.filter(g=>g.canReview).map(g=>g.id);
   const {rows:[data]}=await client.query(`WITH visible AS (
    SELECT p.id,p.state,p.revision,p.reason,p.submitted_by AS "submittedBy",coalesce(nullif(u.display_name,''),u.username) AS "submitterName",p.grid_task_id AS "gridId",g.region_task_id AS "regionId",p.payload->>'name' AS name,p.payload->>'address' AS address,(p.payload->>'lat')::float8 AS lat,(p.payload->>'lon')::float8 AS lon,(p.grid_task_id=ANY($2::int[]) AND $10::boolean) AS "canReview"
    FROM bd_store_proposal p JOIN bd_grid_task g ON g.id=p.grid_task_id LEFT JOIN users u ON u.id=p.submitted_by
    WHERE p.grid_task_id=ANY($1::int[]) AND ($3::boolean OR p.grid_task_id=ANY($2::int[]) OR p.submitted_by=$4)
   ),matching AS(SELECT * FROM visible WHERE ($5='' OR state=$5) AND ($6::int IS NULL OR "regionId"=$6) AND ($7::int IS NULL OR "gridId"=$7) AND ($8::int IS NULL OR "submittedBy"=$8)),paged AS(SELECT * FROM matching ORDER BY id DESC LIMIT 50 OFFSET $9)
   SELECT (SELECT count(*)::int FROM matching) AS total,coalesce((SELECT json_agg(p ORDER BY p.id DESC) FROM paged p),'[]') AS items,coalesce((SELECT json_agg(s) FROM (SELECT DISTINCT "submittedBy" AS id,"submitterName" AS name FROM visible ORDER BY name,id) s),'[]') AS submitters`,[grids.map(g=>g.id),reviewIds,actor.role==='admin',actorId,input.state,input.region??null,input.grid??null,input.submitter??null,(input.page-1)*50,main.status!=='disabled']);
   return {...data,page:input.page,pageSize:50,canReview:reviewIds.length>0&&main.status!=='disabled',canApprove:reviewIds.length>0&&!['completed','disabled'].includes(main.status),regions:regions.map(r=>({id:r.id,name:r.snapshot.name||'#'+r.id})),grids};
  });
 }
 async proposals(actorId:number,gridId:number,value:unknown){
  const {page}=parse(z.object({page:z.coerce.number().int().min(1).max(10000).default(1)}).strict(),value);
  return this.db.transaction(async client=>{await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');const actor=await this.actor(client,actorId),context=await this.context(client,gridId,actor);
   const {rows:[data]}=await client.query(`WITH matching AS(SELECT id,state,revision,reason,submitted_by AS "submittedBy",store_id AS "storeId",payload->>'name' AS name,payload->>'address' AS address,(payload->>'lat')::float8 AS lat,(payload->>'lon')::float8 AS lon FROM bd_store_proposal WHERE grid_task_id=$1 AND ($2::boolean OR submitted_by=$3)), paged AS(SELECT * FROM matching ORDER BY id DESC LIMIT 30 OFFSET $4) SELECT (SELECT count(*)::int FROM matching) AS total,coalesce((SELECT json_agg(p ORDER BY p.id DESC) FROM paged p),'[]') AS items`,[gridId,actor.role==='admin'||context.captain,actor.id,(page-1)*30]);
   return {...data,page,canSubmit:context.owner&&!['completed','disabled'].includes(context.main.status),canReview:context.captain&&context.main.status!=='disabled',canApprove:context.captain&&!['completed','disabled'].includes(context.main.status)};
  });
 }
 async proposalPhoto(actorId:number,id:number){return this.db.transaction(async client=>{
  await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');const actor=await this.actor(client,actorId),{rows:[row]}=await client.query('SELECT grid_task_id,submitted_by,payload FROM bd_store_proposal WHERE id=$1',[id]);
  if(!row)throw new BusinessError('NOT_FOUND','照片不存在',404);const context=await this.context(client,row.grid_task_id,actor);if(actor.role!=='admin'&&!context.captain&&row.submitted_by!==actorId)throw new BusinessError('FORBIDDEN','无权查看此申请',403);
  const scope=photoScope('proposal:grid:'+row.grid_task_id),ref=row.payload.photo_ref,key=typeof ref==='string'?ref.split('/').at(-1):null;
  if(key&&/^[a-f0-9]{64}$/.test(key)&&ref===photoRef(scope,key))return {path:photoPath(scope,key),mime:'image/jpeg' as const};
  const {photo,photo_type:mime}=row.payload;
  if(typeof photo==='string'&&photo.length<=Math.ceil(PHOTO_BYTES/3)*4&&['image/jpeg','image/png','image/webp'].includes(mime))return {bytes:Buffer.from(photo,'base64'),mime:mime as string};
  throw new BusinessError('NOT_FOUND','照片不存在',404);
 });}
 async reviewProposals(actorId:number,mainId:number,value:unknown){
  const input=parse(z.object({action:z.enum(['approve','reject']),reason:z.string().trim().max(500).default(''),selections:z.array(z.object({id:z.number().int().positive().max(2147483647),revision}).strict()).min(1).max(200)}).strict(),value);
  const approve=input.action==='approve',selected=new Map(input.selections.map(row=>[row.id,row.revision]));
  if(selected.size!==input.selections.length||(!approve&&!input.reason))throw new BusinessError('VALIDATION','请选择不同申请，驳回须填写原因',400);
  return this.db.transaction(async client=>{
   await client.query("SELECT pg_advisory_xact_lock_shared(hashtext('teams-write'),0)");const actor=await this.actor(client,actorId,true);
   if(approve)await client.query("SELECT pg_advisory_xact_lock(hashtext('store-proposal-approvals'),0)");
   const {rows:[main]}=await client.query('SELECT id,status FROM bd_main_task WHERE id=$1 FOR UPDATE',[mainId]);
   if(!main)throw new BusinessError('NOT_FOUND','批次不存在',404);
   if(main.status==='disabled'||(approve&&main.status==='completed'))throw new BusinessError('BATCH_CLOSED','批次已关闭，不能执行此操作',409);
   const {rows:proposals}=await client.query(`SELECT p.*,r.team_id,r.leader_id,g.grid_id,g.snapshot AS grid_snapshot FROM bd_store_proposal p JOIN bd_grid_task g ON g.id=p.grid_task_id JOIN bd_region_task r ON r.id=g.region_task_id WHERE p.id=ANY($1::int[]) AND r.main_task_id=$2 ORDER BY p.id FOR UPDATE OF p`,[[...selected.keys()],mainId]);
   if(proposals.length!==selected.size)throw new BusinessError('NOT_FOUND','申请不存在或不属于此批次',404);
   const region=proposals[0],membership=await this.membership(client,actor,region);
   if(!this.captain(actor,region,membership)||proposals.some(row=>row.team_id!==region.team_id||row.leader_id!==actorId))throw new BusinessError('FORBIDDEN','仅本批次片区的当前队长可核实本队申请',403);
   if(proposals.some(row=>row.state!=='pending'||row.revision!==selected.get(row.id)))throw new BusinessError('PROPOSAL_CHANGED','申请已变更，请刷新后重试',409);
   const snapshots=proposals.map(row=>parse(z.object({name:z.string().trim().min(1).max(255),address:z.string().trim().min(1).max(1000),lat:z.number().min(-90).max(90),lon:z.number().min(-180).max(180)}),row.payload));
   if(approve){
    const normalized=(name:string)=>unicodeCasefold(name.replace(/[\s\u0085\u001c-\u001f]/gu,''));const keys=new Set(snapshots.map(row=>normalized(row.name)));const near=(a:{lat:number;lon:number},b:{lat:number;lon:number})=>Math.abs(a.lat-b.lat)<0.0003&&Math.abs(a.lon-b.lon)<0.0003;
    if(keys.size!==snapshots.length||snapshots.some((row,i)=>snapshots.slice(0,i).some(other=>near(row,other))))throw new BusinessError('PROPOSAL_DUPLICATE','所选申请可能重复，请分别核实',409);
    if(snapshots.some((row,i)=>!gridCovers(proposals[i].grid_snapshot?.geometry,row.lon,row.lat)))throw new BusinessError('PROPOSAL_OUTSIDE_GRID','定位不在当前网格内',409);
    const {rows:existing}=await client.query(`SELECT DISTINCT s.snapshot->>'name' AS name FROM bd_store_task s JOIN bd_grid_task g ON g.id=s.grid_task_id JOIN bd_region_task r ON r.id=g.region_task_id WHERE r.main_task_id=$1`,[mainId]);
    const {rows:[nearby]}=await client.query(`WITH incoming AS (SELECT lat,lon FROM jsonb_to_recordset($2::jsonb) AS v(lat float8,lon float8)), places AS (
     SELECT CASE WHEN jsonb_typeof(s.snapshot::jsonb->'lat')='number' THEN (s.snapshot->>'lat')::float8 END AS lat,
       CASE WHEN jsonb_typeof(s.snapshot::jsonb->'lon')='number' THEN (s.snapshot->>'lon')::float8 END AS lon
     FROM bd_store_task s JOIN bd_grid_task g ON g.id=s.grid_task_id JOIN bd_region_task r ON r.id=g.region_task_id WHERE r.main_task_id=$1
     UNION ALL SELECT lat,lon FROM bd_work_store
    ) SELECT EXISTS(SELECT 1 FROM incoming i JOIN places p ON abs(p.lat-i.lat)<0.0003 AND abs(p.lon-i.lon)<0.0003) AS duplicate`,[mainId,JSON.stringify(snapshots)]);
    if(nearby.duplicate||existing.some(row=>row.name&&keys.has(normalized(row.name))))throw new BusinessError('PROPOSAL_DUPLICATE','存在疑似重复商户，请先核对后重新提交',409);
   }
   const baseIds=[...new Set(proposals.map(row=>row.grid_id))];
   const base=approve?new Map((await client.query('SELECT id,release_id FROM bd_work_grid WHERE id=ANY($1::int[]) ORDER BY id FOR UPDATE',[baseIds])).rows.map(row=>[row.id,row.release_id])):new Map();
   const affected=new Set<number>();
   for(const [i,row] of proposals.entries()){
    const snapshot=snapshots[i],storeId=approve?'new-'+randomUUID():null,state=approve?'approved':'rejected';
    await client.query('UPDATE bd_store_proposal SET state=$1,reviewed_by=$2,reviewed_at=now(),reason=$3,revision=revision+1,store_id=$4 WHERE id=$5',[state,actor.id,input.reason,storeId,row.id]);
    if(approve){await client.query(`INSERT INTO bd_work_store(id,name,address,lat,lon,status,revision,updated_at) VALUES($1,$2,$3,$4,$5,'normal',0,now())`,[storeId,snapshot.name,snapshot.address,snapshot.lat,snapshot.lon]);
     await client.query('INSERT INTO bd_grid_store(release_id,grid_id,source_id) VALUES($1,$2,$3)',[base.get(row.grid_id),row.grid_id,storeId]);
     await client.query('UPDATE bd_work_grid SET store_count=store_count+1 WHERE id=$1',[row.grid_id]);
     await client.query(`INSERT INTO bd_store_task(grid_task_id,store_id,snapshot,status,result,updated_at,revision) VALUES($1,$2,$3,'pending','{}',now(),0)`,[row.grid_task_id,storeId,JSON.stringify(snapshot)]);
     await client.query('UPDATE bd_grid_task SET total_stores=total_stores+1 WHERE id=$1',[row.grid_task_id]);affected.add(row.grid_task_id);
    }
    await this.proposalLog(client,actor,row.id,'review_store_proposal',{state:row.state},{state,reason:input.reason});
   }
   if(approve){
    await client.query(`UPDATE bd_grid_task SET progress=completed_stores::bigint*100/total_stores,status=CASE WHEN completed_stores>0 THEN 'in_progress' WHEN assignee_id IS NOT NULL THEN 'assigned' ELSE 'pending' END,revision=revision+1 WHERE id=ANY($1::int[])`,[[...affected]]);
    await client.query(`WITH totals AS(SELECT r.id,sum(g.total_stores) AS total,sum(g.completed_stores) AS done FROM bd_region_task r JOIN bd_grid_task g ON g.region_task_id=r.id WHERE r.id IN(SELECT region_task_id FROM bd_grid_task WHERE id=ANY($1::int[])) GROUP BY r.id) UPDATE bd_region_task r SET progress=totals.done*100/totals.total,status=CASE WHEN totals.done=0 THEN 'pending' ELSE 'in_progress' END,revision=revision+1 FROM totals WHERE r.id=totals.id`,[[...affected]]);
    await client.query(`UPDATE bd_main_task SET status='active',completed_at=NULL,progress=(SELECT sum(g.completed_stores)*100/sum(g.total_stores) FROM bd_grid_task g JOIN bd_region_task r ON r.id=g.region_task_id WHERE r.main_task_id=$1),revision=revision+1 WHERE id=$1`,[mainId]);
   }
   return {count:proposals.length};
  });
 }
 async photoPreflight(actorId:number,storeId:number){return this.db.transaction(async client=>{
  const actor=await this.actor(client,actorId),{rows:[store]}=await client.query('SELECT id,store_id,grid_task_id FROM bd_store_task WHERE id=$1',[storeId]);if(!store)throw new BusinessError('NOT_FOUND','店铺任务不存在',404);await this.context(client,store.grid_task_id,actor,false,true);
  return store.store_id as string;
 });}
 async withPhotos(actorId:number,storeId:number,value:unknown,files:Buffer[]){
  const input=parse(storeInput.extend({photoUrls:z.array(z.string().max(2048)).max(PHOTO_COUNT),photoRevision:revision}).strict(),value);
  const {photoRevision,photoUrls,...result}=input;
  if(files.length+photoUrls.length>PHOTO_COUNT||new Set(photoUrls).size!==photoUrls.length)throw new BusinessError('PHOTO_LIMIT','每家店铺最多保存 3 张照片',400);
  const source=await this.photoPreflight(actorId,storeId);
  const uploads=await prepareBatchPhotos(source,files);
  return this.complete(actorId,storeId,{...result,photoUrls:[...new Set([...photoUrls,...uploads])]}, {source,photoRevision,keep:photoUrls});
 }
 async photoFile(actorId:number,storeId:number,scope:string,key:string){return this.db.transaction(async client=>{
  const actor=await this.actor(client,actorId),{rows:[store]}=await client.query('SELECT store_id,grid_task_id,result FROM bd_store_task WHERE id=$1',[storeId]);
  if(!store||photoScope(store.store_id)!==scope)throw new BusinessError('NOT_FOUND','照片不存在',404);
  const context=await this.context(client,store.grid_task_id,actor);
  const profile=context.main.status==='completed'||context.main.status==='disabled'?[]:(await client.query('SELECT photo_urls FROM bd_merchant_detail WHERE store_id=$1',[store.store_id])).rows[0]?.photo_urls??[];
  if(![...(store.result?.photo_urls??[]),...profile].includes(photoRef(scope,key)))throw new BusinessError('NOT_FOUND','照片不存在',404);
  return photoPath(scope,key);
 });}
 async complete(actorId:number,storeId:number,value:unknown,media?:{source:string;photoRevision:number;keep:string[]}){const input=parse(storeInput,value);return this.db.transaction(async client=>{
  await client.query("SELECT pg_advisory_xact_lock_shared(hashtext('teams-write'),0)");const actor=await this.actor(client,actorId,true);await client.query("SELECT pg_advisory_xact_lock(hashtext('batch-request'),hashtext($1))",[input.requestId]);const initial=(await client.query('SELECT grid_task_id FROM bd_store_task WHERE id=$1',[storeId])).rows[0];if(!initial)throw new BusinessError('NOT_FOUND','店铺任务不存在',404);const context=await this.context(client,initial.grid_task_id,actor,true,true),store=(await client.query('SELECT * FROM bd_store_task WHERE id=$1 FOR UPDATE',[storeId])).rows[0];
  const result={business_status:input.status,note:input.note.trim(),...input.outcome!==undefined?{outcome:input.outcome}:{},...input.photoUrls!==undefined?{photo_urls:input.photoUrls}:{}};
  if(store.request_id===input.requestId){if(store.actor_id!==actor.id||JSON.stringify(Object.entries(store.result).sort())!==JSON.stringify(Object.entries(result).sort()))throw new BusinessError('REQUEST_ID_CONFLICT','提交标识与原操作者或内容冲突',409);return {ok:true,reused:true,gridId:context.grid.id};}
  if(context.main.status==='completed')throw new BusinessError('BATCH_COMPLETED','该批次已完成，不能修改',409);
  if((await client.query('SELECT id FROM bd_task_event WHERE request_id=$1 UNION ALL SELECT id FROM bd_store_task WHERE request_id=$1 LIMIT 1',[input.requestId])).rowCount)throw new BusinessError('REQUEST_ID_CONFLICT','提交标识已用于其他记录或历史修订',409);
  if(store.revision!==input.revision)throw new BusinessError('TASK_CHANGED','店铺记录已变更，请刷新后重试',409);
  if(media){
   if(media.source!==store.store_id)throw new BusinessError('TASK_CHANGED','门店来源已变更',409);
   await client.query("SELECT pg_advisory_xact_lock(hashtext('merchant-photos'),hashtext($1))",[store.store_id]);
   const {rows:[profile]}=await client.query('SELECT revision,photo_urls FROM bd_merchant_detail WHERE store_id=$1 FOR UPDATE',[store.store_id]);
   if((profile?.revision??0)!==media.photoRevision||media.keep.some(url=>!(profile?.photo_urls??[]).includes(url)&&!(store.result?.photo_urls??[]).includes(url)))throw new BusinessError('PHOTO_CHANGED','商户照片已变更，请刷新后重试',409);
   await client.query(`INSERT INTO bd_merchant_detail(store_id,photo_urls,revision,updated_at,place_lookup_state) VALUES($1,$2,1,now(),'pending')
      ON CONFLICT(store_id) DO UPDATE SET photo_urls=excluded.photo_urls,revision=bd_merchant_detail.revision+1,updated_at=now()`,[store.store_id,JSON.stringify(input.photoUrls)]);
  }else{
  if(input.photoUrls){const known=new Set([...(store.result?.photo_urls??[]),...((await client.query('SELECT photo_urls FROM bd_merchant_detail WHERE store_id=$1',[store.store_id])).rows[0]?.photo_urls??[])]);if(input.photoUrls.some(url=>!known.has(url)))throw new BusinessError('PHOTO_NOT_OWNED','照片必须来自该门店已保存的私有照片',400);}
  }
  const first=store.status!=='completed',old={grid:context.grid.status,region:context.region.status,main:context.main.status},before={status:store.status,result:store.result};
  await client.query("UPDATE bd_store_task SET status='completed',result=$1,actor_id=$2,request_id=$3,revision=revision+1,updated_at=now() WHERE id=$4",[JSON.stringify(result),actor.id,input.requestId,store.id]);store.request_id=input.requestId;
  const completed=context.grid.completed_stores+(first?1:0);let next;try{next=batchProgress('grid',context.grid.total_stores,completed,context.grid.assignee_id!==null);}catch{throw new BusinessError('BATCH_COUNTER_INVALID','任务计数不一致，请管理员核对，提交已回滚',409);}
  await client.query('UPDATE bd_grid_task SET completed_stores=$1,progress=$2,status=$3,revision=revision+1 WHERE id=$4',[completed,next.progress,next.status,context.grid.id]);context.grid.status=next.status;
  if(first){const counts=(await client.query(`SELECT coalesce(sum(g.total_stores),0)::int AS total,coalesce(sum(g.completed_stores),0)::int AS completed,coalesce(sum(g.total_stores) FILTER(WHERE r.id=$2),0)::int AS "regionTotal",coalesce(sum(g.completed_stores) FILTER(WHERE r.id=$2),0)::int AS "regionCompleted" FROM bd_grid_task g JOIN bd_region_task r ON r.id=g.region_task_id WHERE r.main_task_id=$1`,[context.main.id,context.region.id])).rows[0];let region,main;try{region=batchProgress('region',counts.regionTotal,counts.regionCompleted);main=batchProgress('main',counts.total,counts.completed);}catch{throw new BusinessError('BATCH_COUNTER_INVALID','父级任务计数异常，提交已回滚',409);}await client.query('UPDATE bd_region_task SET progress=$1,status=$2,revision=revision+1 WHERE id=$3',[region.progress,region.status,context.region.id]);await client.query("UPDATE bd_main_task SET progress=$1,status=$2::text,revision=revision+1,completed_at=CASE WHEN $2::text='completed' THEN now() ELSE NULL END WHERE id=$3",[main.progress,main.status,context.main.id]);context.region.status=region.status;context.main.status=main.status;}
  await this.event(client,actor,context,'store',first?'complete_store_task':'update_store_result',before,{main_task_id:context.main.id,result},store);
  for(const [level,name]of [['grid','grid_status_changed'],['region','region_status_changed'],['main','batch_status_changed']] as const)if(old[level]!==context[level].status)await this.event(client,actor,context,level,name,{status:old[level]},{status:context[level].status});
  return {ok:true,reused:false,firstCompletion:first,gridId:context.grid.id};
 });}
}
@Injectable()
export class BatchPhotoUploadGuard implements CanActivate{
 private active=0;
 constructor(@Inject(AuthService)private readonly auth:AuthService,@Inject(BatchWorkService)private readonly service:BatchWorkService){}
 async canActivate(context:ExecutionContext){const req=context.switchToHttp().getRequest<Request>(),{user}=await this.auth.require(req,{write:true});const id=parse(identity,req.params.id);if(req.path.endsWith('/proposals'))await this.service.proposalPreflight(user.id,id);else await this.service.photoPreflight(user.id,id);if(!req.res||req.res.destroyed||this.active>=3)throw new BusinessError('PHOTO_BUSY','照片处理繁忙，请稍后重试',503);this.active++;req.res.once('close',()=>{this.active--;});return true;}
}
@Controller('batch-work')
export class BatchWorkController{
 constructor(@Inject(AuthService)private readonly auth:AuthService,@Inject(BatchWorkService)private readonly service:BatchWorkService){}
 @Get('creation-regions')async creationRegions(@Req()req:Request,@Query()query:unknown){const {user}=await this.auth.require(req,{admin:true});return this.service.creationRegions(user.id,query);}
 @Post('batches')async create(@Req()req:Request,@Body()body:unknown){const {user}=await this.auth.require(req,{admin:true,write:true});return this.service.create(user.id,body);}
 @Post('batches/:id/disable')async disable(@Req()req:Request,@Param('id')id:string,@Body()body:unknown){const {user}=await this.auth.require(req,{admin:true,write:true});return this.service.disable(user.id,parse(identity,id),body);}
 @Get('batches/:id/events')async events(@Req()req:Request,@Param('id')id:string,@Query()query:unknown){const {user}=await this.auth.require(req);return this.service.events(user.id,parse(identity,id),query);}
 @Get('batches/:id')async tree(@Req()req:Request,@Param('id')id:string){const {user}=await this.auth.require(req);return this.service.tree(user.id,parse(identity,id));}
 @Get('batches/:id/mobile')async mobileTree(@Req()req:Request,@Param('id')id:string){const {user}=await this.auth.require(req);return this.service.mobileTree(user.id,parse(identity,id));}
 @Get('regions/:id')async regionDetail(@Req()req:Request,@Param('id')id:string){const {user}=await this.auth.require(req);return this.service.regionDetail(user.id,parse(identity,id));}
 @Get('regions/:id/map-config')async regionMapConfig(@Req()req:Request,@Param('id')id:string){const {user}=await this.auth.require(req);return this.service.regionMapConfig(user.id,parse(identity,id));}
 @Get('regions/:id/map')async regionMap(@Req()req:Request,@Param('id')id:string){const {user}=await this.auth.require(req);return this.service.regionMap(user.id,parse(identity,id));}
 @Get('grids/:id/visits')async visits(@Req()req:Request,@Param('id')id:string,@Query()q:unknown){const {user}=await this.auth.require(req);return this.service.visits(user.id,parse(identity,id),q);}
 @Post('stores/:id/place-lookup')async placeLookup(@Req()req:Request,@Param('id')id:string){const {user}=await this.auth.require(req,{write:true});return this.service.placeLookup(user.id,parse(identity,id));}
 @Post('regions/:id/auto-preview')async autoPreview(@Req()req:Request,@Param('id')id:string,@Body()body:unknown){const {user}=await this.auth.require(req,{write:true});return this.service.autoPreview(user.id,parse(identity,id),body);}
 @Post('regions/:id/auto-apply')async autoApply(@Req()req:Request,@Param('id')id:string,@Body()body:unknown){const {user}=await this.auth.require(req,{write:true});return this.service.autoApply(user.id,parse(identity,id),body);}
 @Get('grids/:id/map-config')async mapConfig(@Req()req:Request,@Param('id')id:string){const {user}=await this.auth.require(req);await this.service.map(user.id,parse(identity,id),{limit:1});const key=process.env.GOOGLE_PUBLIC_API_KEY?.trim()??'';return {enabled:!!key,key:key||null,provider:'google'};}
 @Get('grids/:id/map')async map(@Req()req:Request,@Param('id')id:string,@Query()query:unknown){const {user}=await this.auth.require(req);return this.service.map(user.id,parse(identity,id),query);}
 @Post('stores/:id/location')async location(@Req()req:Request,@Param('id')id:string,@Body()body:unknown){const {user}=await this.auth.require(req,{write:true});return this.service.location(user.id,parse(identity,id),body);}
 @Get('grids/:id')async stores(@Req()req:Request,@Param('id')id:string,@Query()query:unknown){const {user}=await this.auth.require(req);return this.service.stores(user.id,parse(identity,id),query);}
 @Post('regions/:id/assign')async region(@Req()req:Request,@Param('id')id:string,@Body()body:unknown){const {user}=await this.auth.require(req,{admin:true,write:true});return this.service.assignRegion(user.id,parse(identity,id),body);}
 @Post('grids/:id/assign')async grid(@Req()req:Request,@Param('id')id:string,@Body()body:unknown){const {user}=await this.auth.require(req,{write:true});return this.service.assignGrid(user.id,parse(identity,id),body);}
 @Get('batches/:id/proposals')async batchProposals(@Req()req:Request,@Param('id')id:string,@Query()query:unknown){const {user}=await this.auth.require(req);return this.service.batchProposals(user.id,parse(identity,id),query);}
 @Get('grids/:id/proposals')async proposals(@Req()req:Request,@Param('id')id:string,@Query()query:unknown){const {user}=await this.auth.require(req);return this.service.proposals(user.id,parse(identity,id),query);}
 @Post('grids/:id/proposals')@UseGuards(BatchPhotoUploadGuard)@UseInterceptors(FilesInterceptor('photos',1,{storage:memoryStorage(),limits:{files:1,fileSize:PHOTO_BYTES,fields:1,fieldSize:8000}}))
 async propose(@Req()req:Request,@Param('id')id:string,@Body('result')raw:unknown,@UploadedFiles()files:Express.Multer.File[]){const {user}=await this.auth.require(req,{write:true});let value:unknown;try{if(typeof raw!=='string')throw Error();value=JSON.parse(raw);}catch{throw new BusinessError('VALIDATION','新店申请表单无效',400);}return this.service.submitProposal(user.id,parse(identity,id),value,files?.[0]?.buffer);}
 @Get('proposals/:id/photo')async proposalPhoto(@Req()req:Request,@Res()res:Response,@Param('id')id:string){const {user}=await this.auth.require(req);const photo=await this.service.proposalPhoto(user.id,parse(identity,id));res.set({'Content-Type':photo.mime,'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'});if(photo.bytes){res.send(photo.bytes);return;}try{await stat(photo.path!);}catch{throw new BusinessError('NOT_FOUND','照片不存在',404);}await pipeline(createReadStream(photo.path!),res).catch(()=>res.destroy());}
 @Post('batches/:id/proposal-review')async reviewProposals(@Req()req:Request,@Param('id')id:string,@Body()body:unknown){const {user}=await this.auth.require(req,{write:true});return this.service.reviewProposals(user.id,parse(identity,id),body);}
 @Get('photos/:scope/:key')async photo(@Req()req:Request,@Res()res:Response,@Param('scope')scope:string,@Param('key')key:string,@Query('taskId')taskId:string){
  const {user}=await this.auth.require(req);if(!/^[a-f0-9]{64}$/.test(scope)||!/^[a-f0-9]{64}$/.test(key))throw new BusinessError('NOT_FOUND','照片不存在',404);
  const file=await this.service.photoFile(user.id,parse(identity,taskId),scope,key);let size:number;try{size=(await stat(file)).size;}catch{throw new BusinessError('NOT_FOUND','照片不存在',404);}
  res.set({'Content-Type':'image/jpeg','Content-Length':String(size),'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'});await pipeline(createReadStream(file),res).catch(()=>res.destroy());
 }
 @Post('stores/:id/photos')@UseGuards(BatchPhotoUploadGuard)@UseInterceptors(FilesInterceptor('photos',PHOTO_COUNT,{storage:memoryStorage(),limits:{files:PHOTO_COUNT,fileSize:PHOTO_BYTES,fields:1,fieldSize:16000}}))
 async upload(@Req()req:Request,@Param('id')id:string,@Body('result')raw:unknown,@UploadedFiles()files:Express.Multer.File[]){const {user}=await this.auth.require(req,{write:true});let value:unknown;try{if(typeof raw!=='string')throw Error();value=JSON.parse(raw);}catch{throw new BusinessError('VALIDATION','照片表单无效',400);}return this.service.withPhotos(user.id,parse(identity,id),value,(files??[]).map(file=>file.buffer));}
 @Post('stores/:id')async complete(@Req()req:Request,@Param('id')id:string,@Body()body:unknown){const {user}=await this.auth.require(req,{write:true});return this.service.complete(user.id,parse(identity,id),body);}
}
