import 'reflect-metadata';import {Body,Controller,Get,Inject,Injectable,Param,Post,Query,Req} from '@nestjs/common';import type {Request} from 'express';import type {PoolClient,QueryResultRow} from 'pg';import {z} from 'zod';import {createHash} from 'node:crypto';import {readFile} from 'node:fs/promises';import {join,resolve} from 'node:path';
import {Database} from './database';import {AuthService} from './auth';import {BusinessError} from './errors';import {requireAdmin} from './financial-control';import {SettlementService} from './settlement';import {SettlementSyncService} from './settlement-sync';import {ReportComparisonService} from './report-comparison';import {DurableQueue} from '../../worker/src/queue';import {loadConfig} from './config';import {candidatePairs,masterName} from '../../../packages/domain/src/store-master';
interface Entity extends QueryResultRow {id:number;store_id_raw:string;name_local:string;name_norm:string;city:string|null;master_id:number;address_local:string|null;raw_data_id:number|null}
interface Pair extends QueryResultRow {id:number;entity_a:number;entity_b:number;kind:string;status:string;sim:number|null;note:string|null}
const id=z.number().int().positive();const selection=z.discriminatedUnion('action',[z.object({action:z.literal('build')}),z.object({action:z.literal('recommended')}),
 z.object({action:z.literal('merge-pair'),pairId:id,keepId:id}),z.object({action:z.literal('skip-pair'),pairId:id}),z.object({action:z.literal('split'),entityId:id}),z.object({action:z.literal('merge-group'),nameNorm:z.string().min(1).max(2000),keepId:id}),z.object({action:z.literal('skip-group'),nameNorm:z.string().min(1).max(2000)}),
]);export type StoreSelection=z.infer<typeof selection>;
const aiDecision=z.object({decision:z.enum(['same','diff','unknown']),confidence:z.enum(['high','medium','low']),reason:z.string().max(500)}).strict();
const task=z.object({note:z.string().max(4000).optional(),origin:z.literal('ai').optional(),ai:aiDecision.optional(),requestId:z.string().uuid(),actor:id,selection,token:z.string().regex(/^[a-f0-9]{64}$/)}).strict();
const fields='id,store_id_raw,name_local,name_norm,city,master_id,address_local,raw_data_id';
function parse<T>(schema:z.ZodType<T>,value:unknown){const parsed=schema.safeParse(value);if(!parsed.success)throw new BusinessError('VALIDATION','店铺主档参数无效',400);return parsed.data;}
@Injectable()
export class StoreMasterService{
 readonly queue:DurableQueue;
 constructor(@Inject(Database)private readonly db:Database,@Inject(SettlementService)private readonly settlement:SettlementService){this.queue=new DurableQueue(resolve(process.env.NEXT_TASK_DIRECTORY||'data/refactor/jobs'));}
 async catalog(search='',page=1){
  const result=(await this.db.query(`WITH RECURSIVE membership AS(
   SELECT e.id,e.id AS root,ARRAY[e.id] AS visited FROM store_entities e WHERE e.master_id=e.id OR e.master_id IS NULL OR NOT EXISTS(SELECT 1 FROM store_entities p WHERE p.id=e.master_id)
   UNION ALL SELECT c.id,m.root,m.visited||c.id FROM store_entities c JOIN membership m ON c.master_id=m.id WHERE c.id<>m.id AND NOT(c.id=ANY(m.visited))
  ),matched AS(SELECT DISTINCT m.root FROM membership m JOIN store_entities e ON e.id=m.id WHERE $1='' OR e.store_id_raw ILIKE $1 OR e.name_local ILIKE $1 OR e.name_norm ILIKE $1 OR coalesce(e.city,'') ILIKE $1 OR coalesce(e.address_local,'') ILIKE $1),paged AS(SELECT root FROM matched ORDER BY root LIMIT 30 OFFSET $2),entries AS(
   SELECT e.id,e.store_id_raw AS "storeId",e.name_local AS name,e.name_norm AS "nameNorm",e.city,e.address_local AS address,e.master_id AS "masterId",m.root FROM paged p JOIN membership m ON m.root=p.root JOIN store_entities e ON e.id=m.id ORDER BY m.root,e.id)
   SELECT (SELECT count(*)::int FROM matched) AS total,coalesce((SELECT json_agg(entries) FROM entries),'[]'::json) AS items`,[search?'%'+search+'%':'',(page-1)*30])).rows[0];
  return {...result,page,pageSize:30};
 }
 async pairs(kind:'exact'|'fuzzy',page=1){const result=(await this.db.query(`WITH matched AS(SELECT p.id,p.kind,p.sim,p.status,p.note,p.ai_decision AS "aiDecision",p.ai_reason AS "aiReason",p.entity_a AS "aId",a.store_id_raw AS "aStoreId",a.name_local AS "aName",a.name_norm AS "nameNorm",a.city AS "aCity",p.entity_b AS "bId",b.store_id_raw AS "bStoreId",b.name_local AS "bName",b.city AS "bCity"
  FROM store_pairs p JOIN store_entities a ON a.id=p.entity_a JOIN store_entities b ON b.id=p.entity_b WHERE p.kind=$1 AND p.status='pending' AND a.master_id=a.id AND b.master_id=b.id AND a.id<>b.id),paged AS(SELECT * FROM matched ORDER BY id LIMIT 30 OFFSET $2)
  SELECT (SELECT count(*)::int FROM matched) AS total,coalesce((SELECT json_agg(paged) FROM paged),'[]'::json) AS items`,[kind,(page-1)*30])).rows[0];return {...result,page,pageSize:30};}
 private async state(client:PoolClient,input:StoreSelection){
  let pair:Pair|undefined,entities:Entity[]=[];
  if('pairId'in input){pair=(await client.query<Pair>('SELECT id,entity_a,entity_b,kind,status,sim,note FROM store_pairs WHERE id=$1',[input.pairId])).rows[0];if(!pair)throw new BusinessError('NOT_FOUND','候选对不存在',404);entities=(await client.query<Entity>(`SELECT ${fields} FROM store_entities WHERE id=ANY($1::int[]) ORDER BY id`,[[pair.entity_a,pair.entity_b]])).rows;if(entities.length!==2)throw new BusinessError('NOT_FOUND','候选店铺不存在',404);if(pair.status!=='pending'||entities.some(e=>e.master_id!==e.id))throw new BusinessError('STORE_PAIR_CHANGED','候选已处理或已并入其他主档，请刷新',409);if(input.action==='merge-pair'&&!entities.some(e=>e.id===input.keepId))throw new BusinessError('KEEP_NOT_IN_PAIR','保留主档必须属于该候选对',400);}
  else if(input.action==='split'){const entity=(await client.query<Entity>(`SELECT ${fields} FROM store_entities WHERE id=$1`,[input.entityId])).rows[0];if(!entity)throw new BusinessError('NOT_FOUND','店铺实体不存在',404);entities=[entity];if(entity.master_id===entity.id)throw new BusinessError('ALREADY_MASTER','该店铺已是主档',409);}
  else if('nameNorm'in input){entities=(await client.query<Entity>(`SELECT ${fields} FROM store_entities WHERE name_norm=$1 AND master_id=id ORDER BY id`,[input.nameNorm])).rows;if(entities.length<2)throw new BusinessError('STORE_GROUP_CHANGED','该名称组已不足两个独立主档，请刷新',409);if(input.action==='merge-group'&&!entities.some(e=>e.id===input.keepId))throw new BusinessError('KEEP_NOT_IN_GROUP','保留主档不属于该组',400);}
  else entities=(await client.query<Entity>(`SELECT ${fields} FROM store_entities WHERE master_id=id ORDER BY id`)).rows;
  const pairs=(await client.query<Pair>("SELECT id,entity_a,entity_b,kind,status,sim,note FROM store_pairs WHERE entity_a=ANY($1::int[]) AND entity_b=ANY($1::int[]) ORDER BY id",[entities.map(e=>e.id)])).rows;
  const token=createHash('sha256').update(JSON.stringify({selection:input,entities,pair:pair??null,pairs})).digest('hex');
  return {entities,pair,pairs,token,crossCity:new Set(entities.map(e=>e.city).filter(Boolean)).size>1};
 }
 async preview(input:StoreSelection){return this.db.transaction(async client=>{await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');return this.previewInTransaction(client,input);});}
 async enqueue(input:StoreSelection,token:string,actor:number,requestId:string){const jobId='store-master-'+requestId,payload={requestId,actor,selection:input,token};try{const old=JSON.parse(await readFile(join(this.queue.directory,jobId+'.identity'),'utf8'));if(JSON.stringify(old.payload)!==JSON.stringify(payload))throw new BusinessError('REQUEST_ID_CONFLICT','提交标识已被其他主档操作使用',409);return {jobId,reused:true};}catch(error){if((error as NodeJS.ErrnoException).code!=='ENOENT')throw error;}
  const current=await this.preview(input);if(current.token!==token)throw new BusinessError('STORE_MASTER_CHANGED','主档或候选已变化，请重新预览',409);
  try{await this.queue.enqueue('store.master',payload,jobId);}catch(error){if((error as NodeJS.ErrnoException).code!=='EEXIST')throw error;const old=JSON.parse(await readFile(join(this.queue.directory,jobId+'.identity'),'utf8'));if(JSON.stringify(old.payload)!==JSON.stringify(payload))throw new BusinessError('REQUEST_ID_CONFLICT','提交标识已被其他操作使用',409);}return {jobId,reused:false};
 }
 private async lock(client:PoolClient,actor:number){await requireAdmin(client,actor);await client.query("SELECT pg_advisory_xact_lock(hashtext('settlement-write'),0)");await client.query("SELECT pg_advisory_xact_lock(hashtext('payroll-financial-write'),0)");}
 private async affected(client:PoolClient,ids:number[]){return (await client.query<{month:string}>(`WITH RECURSIVE family AS(SELECT id,store_id_raw FROM store_entities WHERE id=ANY($1::int[]) UNION SELECT e.id,e.store_id_raw FROM store_entities e JOIN family f ON e.master_id=f.id WHERE e.id<>f.id) SELECT DISTINCT left(r.modified_raw,7) AS month FROM raw_records r JOIN family f ON btrim(r.store_id_raw)=f.store_id_raw WHERE left(r.modified_raw,7)~'^\\d{4}-(0[1-9]|1[0-2])$' ORDER BY month`,[ids])).rows.map(row=>row.month);}
 private async rebuild(client:PoolClient,months:string[],actor:number){const sealed=new Set((await client.query<{month:string}>('SELECT month FROM sealed_months WHERE month=ANY($1::text[])',[months])).rows.map(row=>row.month));const done=[];for(const month of months)if(!sealed.has(month))done.push(await this.settlement.rebuildTransaction(client,month,actor));return {recomputed:done.map(row=>row.month),skippedSealed:[...sealed].sort()};}
 private async link(client:PoolClient,entity:Entity,keeper:Entity,basis:string,note:string,actor:number){
  if(entity.id===keeper.id)return;await client.query('UPDATE store_entities SET master_id=$1,master_store_id=$2,master_raw_data_id=$3,updated_at=now() WHERE id=$4',[keeper.id,keeper.store_id_raw,keeper.raw_data_id,entity.id]);await client.query('INSERT INTO store_merge_logs(entity_id,from_master,to_master,basis,note,decided_by,created_at) VALUES($1,$2,$3,$4,$5,$6,now())',[entity.id,entity.master_id,keeper.id,basis,note,actor]);
 }

 async previewInTransaction(client:PoolClient,input:StoreSelection){const state=await this.state(client,input);return {selection:input,token:state.token,entityCount:state.entities.length,entities:state.entities.slice(0,100).map(e=>({id:e.id,storeId:e.store_id_raw,name:e.name_local,city:e.city,masterId:e.master_id})),crossCity:state.crossCity};
 }

 async processInTransaction(client:PoolClient,input:z.infer<typeof task>){const checked=task.safeParse(input);if(!checked.success)throw new BusinessError('STORE_TASK_INVALID','主档任务参数无效',400);input=checked.data;await this.lock(client,input.actor);const marker='[request:'+input.requestId+']';const replay=(await client.query("SELECT id FROM store_merge_logs WHERE decided_by=$1 AND strpos(coalesce(note,''),$2)>0 UNION ALL SELECT id FROM store_pairs WHERE decided_by=$1 AND strpos(coalesce(note,''),$2)>0 LIMIT 1",[input.actor,marker])).rows[0];if(replay)return {ok:true,reused:true};const state=await this.state(client,input.selection);if(state.token!==input.token)throw new BusinessError('STORE_MASTER_CHANGED','主档或候选已变化，任务未执行，请重新预览',409);
   if(input.origin==='ai'){
    if(!state.pair||!input.ai)throw new BusinessError('AI_DECISION_INVALID','模型采纳参数无效',400);
    if(input.selection.action==='merge-pair'&&state.crossCity)throw new BusinessError('AI_CITY_CONFLICT','模型判同但城市不同，保留人工核对',409);
    await client.query('UPDATE store_pairs SET ai_decision=$1,ai_confidence=$2,ai_reason=$3 WHERE id=$4',[input.ai.decision,input.ai.confidence,input.ai.reason,state.pair.id]);
   }
   if(input.selection.action==='build')return {ok:true,action:'build',...await this.build(client,state.entities.map(e=>e.id)),recomputed:[] as string[],skippedSealed:[] as string[]};
   if(input.selection.action==='recommended'){
    const pendingIds=new Set(state.pairs.filter(p=>p.kind==='exact'&&p.status==='pending').flatMap(p=>[p.entity_a,p.entity_b]));const groups=new Map<string,Entity[]>();for(const entity of state.entities)if(pendingIds.has(entity.id)){const group=groups.get(entity.name_norm)??[];group.push(entity);groups.set(entity.name_norm,group);}
    const affected:number[]=[];let merged=0,left=0,groupsApplied=0;
    for(const [norm,members]of groups){if(members.length<2)continue;if(new Set(members.map(e=>e.city).filter(Boolean)).size>1){left++;continue;}const group=state.entities.filter(e=>e.name_norm===norm);const keeper=group[0];for(const entity of group)if(entity.id!==keeper.id){await this.link(client,entity,keeper,'program_exact',(input.note||'整批推荐(最早主档)')+' '+marker,input.actor);merged++;}groupsApplied++;affected.push(...group.map(e=>e.id));await client.query("UPDATE store_pairs SET status='merged',decided_by=$1 WHERE kind='exact' AND status='pending' AND entity_a=ANY($2::int[]) AND entity_b=ANY($2::int[])",[input.actor,group.map(e=>e.id)]);}
    return {ok:true,action:'recommended',groupsApplied,mergedEntities:merged,leftGroups:left,...await this.rebuild(client,await this.affected(client,affected),input.actor)};
   }
   const ids=state.entities.map(e=>e.id);await client.query('SELECT id FROM store_entities WHERE id=ANY($1::int[]) ORDER BY id FOR UPDATE',[ids]);const beforeMonths=await this.affected(client,ids);
   if(input.selection.action==='skip-pair')await client.query("UPDATE store_pairs SET status='skip',decided_by=$1,note=$3 WHERE id=$2",[input.actor,input.selection.pairId,(input.origin==='ai'?'AI(high): '+input.ai!.reason:(input.note||'人工跳过'))+' '+marker]);
   else if(input.selection.action==='skip-group')await client.query("UPDATE store_pairs SET status='skip',decided_by=$1,note=$3 WHERE kind='exact' AND status='pending' AND entity_a=ANY($2::int[]) AND entity_b=ANY($2::int[])",[input.actor,ids,'人工整组跳过 '+marker]);
   else if(input.selection.action==='split'){const entity=state.entities[0];await client.query('UPDATE store_entities SET master_id=id,master_store_id=store_id_raw,master_raw_data_id=NULL,updated_at=now() WHERE id=$1',[entity.id]);await client.query("INSERT INTO store_merge_logs(entity_id,from_master,to_master,basis,note,decided_by,created_at) VALUES($1,$2,$1,'manual',$4,$3,now())",[entity.id,entity.master_id,input.actor,(input.note||'拆分回主档')+' '+marker]);}
   else{const chosen=input.selection;const keeper=state.entities.find(e=>e.id===chosen.keepId)!;for(const entity of state.entities)if(entity.id!==keeper.id)await this.link(client,entity,keeper,input.origin==='ai'?'ai':chosen.action==='merge-group'?'program_exact':'manual',(input.origin==='ai'?'AI(high): '+input.ai!.reason:input.note||(chosen.action==='merge-group'?'人工同名归并':'人工候选合并'))+' '+marker,input.actor);
    if(chosen.action==='merge-pair')await client.query("UPDATE store_pairs SET status='merged',decided_by=$1 WHERE id=$2",[input.actor,chosen.pairId]);else await client.query("UPDATE store_pairs SET status='merged',decided_by=$1 WHERE kind='exact' AND status='pending' AND entity_a=ANY($2::int[]) AND entity_b=ANY($2::int[])",[input.actor,ids]);
   }
   const change=!input.selection.action.startsWith('skip');const result=change?await this.rebuild(client,[...new Set([...beforeMonths,...await this.affected(client,ids)])].sort(),input.actor):{recomputed:[] as string[],skippedSealed:[] as string[]};return {ok:true,action:input.selection.action,...result};

 }
async process(value:unknown){const payload=task.safeParse(value);if(!payload.success)return {ok:false,code:'STORE_TASK_INVALID',message:'主档任务参数无效'};const input=payload.data;
  try{return await this.db.transaction(client=>this.processInTransaction(client,input));}catch(error){if(error instanceof BusinessError)return {ok:false,code:error.code,message:error.message};throw error;}
 }
 async build(client:PoolClient,targets:number[]){
  const entities=(await client.query<Entity>(`SELECT ${fields} FROM store_entities WHERE master_id=id ORDER BY id`)).rows;
  const existing=(await client.query<{entity_a:number;entity_b:number}>('SELECT entity_a,entity_b FROM store_pairs')).rows;const pairs=candidatePairs(entities.map(e=>({id:e.id,nameNorm:e.name_norm,city:e.city,masterId:e.master_id})),new Set(targets),new Set(existing.map(p=>`${Math.min(p.entity_a,p.entity_b)}:${Math.max(p.entity_a,p.entity_b)}`)));
  for(let i=0;i<pairs.length;i+=1000)await client.query(`INSERT INTO store_pairs(entity_a,entity_b,kind,sim,status,created_at) SELECT a,b,kind,sim,'pending',now() FROM json_to_recordset($1::json) AS p(a int,b int,kind text,sim int)`,[JSON.stringify(pairs.slice(i,i+1000))]);
  await client.query(`UPDATE store_pairs p SET status='skip',note='跨城市 → 判定不同店' FROM store_entities a,store_entities b WHERE p.status='pending' AND p.entity_a=a.id AND p.entity_b=b.id AND a.city IS NOT NULL AND a.city<>'' AND b.city IS NOT NULL AND b.city<>'' AND a.city<>b.city AND (a.id=ANY($1::int[]) OR b.id=ANY($1::int[]))`,[targets]);
  await client.query(`WITH conflicting AS(SELECT name_norm FROM store_entities WHERE master_id=id AND name_norm IN(SELECT name_norm FROM store_entities WHERE id=ANY($1::int[])) GROUP BY name_norm HAVING count(DISTINCT nullif(city,''))>=2) UPDATE store_pairs p SET status='skip',note='跨城市/城市不一致 → 判定不同店' FROM store_entities a,store_entities b WHERE p.kind='exact' AND p.status='pending' AND p.entity_a=a.id AND p.entity_b=b.id AND a.name_norm=b.name_norm AND a.name_norm IN(SELECT name_norm FROM conflicting)`,[targets]);
  return {added:pairs.length};
 }
 async automatic(importId:number,actor:number){return this.db.transaction(async client=>{
  await this.lock(client,actor);const targets=(await client.query<Entity>(`SELECT ${fields} FROM store_entities WHERE store_id_raw IN(SELECT DISTINCT btrim(store_id_raw) FROM raw_records WHERE import_id=$1) ORDER BY id`,[importId])).rows;
  for(const entity of targets){const norm=masterName(entity.name_local);if(norm!==entity.name_norm)await client.query('UPDATE store_entities SET name_norm=$1 WHERE id=$2',[norm,entity.id]);}
  const built=await this.build(client,targets.map(e=>e.id));
  if(process.env.STORE_AUTO_MERGE_EXACT==='0')return {...built,mergedGroups:0,recomputed:[] as string[],skippedSealed:[] as string[],automaticDisabled:true};
  const groupRows=(await client.query<Entity>(`SELECT ${fields} FROM store_entities e WHERE e.master_id=e.id AND e.name_norm IN(SELECT DISTINCT a.name_norm FROM store_pairs p JOIN store_entities a ON a.id=p.entity_a WHERE p.kind='exact' AND p.status='pending' AND (p.entity_a=ANY($1::int[]) OR p.entity_b=ANY($1::int[]))) ORDER BY e.id`,[targets.map(e=>e.id)])).rows;
  const groups=new Map<string,Entity[]>();for(const entity of groupRows){const members=groups.get(entity.name_norm)??[];members.push(entity);groups.set(entity.name_norm,members);}
  const changed:number[]=[];let count=0;
  for(const members of groups.values()){if(members.length<2||new Set(members.map(e=>e.city).filter(Boolean)).size>1)continue;const keep=members.at(-1)!;for(const entity of members)if(entity.id!==keep.id){await this.link(client,entity,keep,'program_exact','自动合并：归一化全等(程序必同)',actor);changed.push(entity.id,keep.id);}await client.query("UPDATE store_pairs SET status='merged',decided_by=$1 WHERE kind='exact' AND status='pending' AND entity_a=ANY($2::int[]) AND entity_b=ANY($2::int[])",[actor,members.map(e=>e.id)]);count++;}
  const result=changed.length?await this.rebuild(client,await this.affected(client,changed),actor):{recomputed:[] as string[],skippedSealed:[] as string[]};return {...built,mergedGroups:count,...result};
 });}
 async status(jobId:string){if(!/^store-master-[a-f0-9-]{36}$/.test(jobId))throw new BusinessError('VALIDATION','主档任务编号无效',400);for(const state of ['queued','running','done','failed'])try{const row=JSON.parse(await readFile(join(this.queue.directory,state,jobId+'.json'),'utf8'));return {jobId,status:state==='queued'?'pending':state,result:state==='done'||state==='failed'?row.result:undefined};}catch(error){if((error as NodeJS.ErrnoException).code!=='ENOENT')throw error;}throw new BusinessError('NOT_FOUND','任务不存在',404);}
 async logs(entityId:number){return {items:(await this.db.query('SELECT id,entity_id AS "entityId",from_master AS "fromMaster",to_master AS "toMaster",basis,note,decided_by AS "decidedBy",created_at AS "createdAt" FROM store_merge_logs WHERE entity_id=$1 OR from_master=$1 OR to_master=$1 ORDER BY id DESC LIMIT 100',[entityId])).rows};}
}
@Controller('stores')
export class StoreMasterController{
 constructor(@Inject(AuthService)private readonly auth:AuthService,@Inject(StoreMasterService)private readonly service:StoreMasterService){}
 @Get()async catalog(@Req()req:Request,@Query()query:unknown){await this.auth.require(req,{admin:true});const value=parse(z.object({search:z.string().max(100).default(''),page:z.coerce.number().int().min(1).max(10000).default(1)}).strict(),query);return this.service.catalog(value.search,value.page);}
 @Get('pairs')async pairs(@Req()req:Request,@Query()query:unknown){await this.auth.require(req,{admin:true});const value=parse(z.object({kind:z.enum(['exact','fuzzy']).default('exact'),page:z.coerce.number().int().min(1).max(10000).default(1)}).strict(),query);return this.service.pairs(value.kind,value.page);}
 @Post('preview')async preview(@Req()req:Request,@Body()body:unknown){await this.auth.require(req,{admin:true,write:true});return this.service.preview(parse(selection,body));}
 @Post('change')async change(@Req()req:Request,@Body()body:unknown){const {user}=await this.auth.require(req,{admin:true,write:true});const value=parse(z.object({selection,token:z.string().regex(/^[a-f0-9]{64}$/),requestId:z.string().uuid(),confirm:z.literal('CHANGE_STORE_MASTER')}).strict(),body);return this.service.enqueue(value.selection,value.token,user.id,value.requestId);}
 @Get('jobs/:id')async status(@Req()req:Request,@Param('id')id:string){await this.auth.require(req,{admin:true});return this.service.status(id);}
 @Get(':id/logs')async logs(@Req()req:Request,@Param('id')value:string){await this.auth.require(req,{admin:true});return this.service.logs(parse(z.coerce.number().int().positive(),value));}
}
export async function runStoreMasterTask(value:unknown){const db=new Database(loadConfig());try{return await new StoreMasterService(db,new SettlementService(db,new SettlementSyncService(db,new ReportComparisonService(db)))).process(value);}finally{await db.onModuleDestroy();}}
