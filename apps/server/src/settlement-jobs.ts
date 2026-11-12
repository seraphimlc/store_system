import 'reflect-metadata';
import {Controller,Get,Inject,Injectable,Param,Req} from '@nestjs/common';import type {Request} from 'express';import {z} from 'zod';import {readFile} from 'node:fs/promises';import {resolve,join} from 'node:path';
import {Database} from './database';import {AuthService} from './auth';import {BusinessError} from './errors';import {loadConfig} from './config';
import {SettlementService} from './settlement';import {SettlementSyncService} from './settlement-sync';import {ReportComparisonService} from './report-comparison';import {DurableQueue} from '../../worker/src/queue';import {monthRange} from './business-dates';
export type SettlementAction='judge'|'finalize'|'rebuild';
export interface SettlementTask {action:SettlementAction;target:string;actor:number;requestId:string}
const taskSchema=z.object({action:z.enum(['judge','finalize','rebuild']),target:z.string().min(1).max(32),actor:z.number().int().positive(),requestId:z.string().uuid()}).strict();
@Injectable()
export class SettlementJobsService{
 readonly queue:DurableQueue;
 constructor(@Inject(Database)private readonly db:Database,@Inject(SettlementService)private readonly settlement:SettlementService){this.queue=new DurableQueue(resolve(process.env.NEXT_TASK_DIRECTORY||'data/refactor/jobs'));}
 private async preflight(action:SettlementAction,target:string){
  if(action==='rebuild'){
   const {start,end}=monthRange(target);const row=(await this.db.query<{pending:number;sources:number;formal:number}>(`SELECT (SELECT count(*)::int FROM appeals a JOIN raw_records r ON r.id=a.raw_record_id WHERE a.status='pending' AND left(r.modified_raw,7)=$1) AS pending,(SELECT count(*)::int FROM raw_records WHERE left(modified_raw,7)=$1) AS sources,(SELECT count(*)::int FROM formal_records WHERE japan_date BETWEEN $2 AND $3) AS formal`,[target,start,end])).rows[0];
   if(row.pending)throw new BusinessError('APPEALS_PENDING','该月仍有未处理申诉，请处理后重算',409);if(!row.sources&&row.formal)throw new BusinessError('REBUILD_SOURCE_MISSING','该月没有源记录但存在正式数据，拒绝重算以免清空',409);return;
  }
  const row=(await this.db.query<{id:number;pending:number;unjudged:number}>(`SELECT i.id,(SELECT count(*)::int FROM appeals a WHERE a.import_id=i.id AND a.status='pending') AS pending,(SELECT count(*)::int FROM raw_records r WHERE r.import_id=i.id AND r.clean_status IS NULL) AS unjudged FROM imports i WHERE i.id=$1`,[Number(target)])).rows[0];
  if(!row)throw new BusinessError('NOT_FOUND','文件不存在',404);
  if(action==='finalize'&&row.pending)throw new BusinessError('APPEALS_PENDING','该文件仍有未处理申诉，请处理后入表',409);
  if(action==='finalize'&&row.unjudged)throw new BusinessError('JUDGEMENT_REQUIRED','文件仍有未判定记录，请先执行判定',409);
 }
 async enqueue(action:SettlementAction,target:string,actor:number,requestId:string){
  const payload=taskSchema.parse({action,target,actor,requestId});const jobId=`settlement-${action}-${requestId}`;
  // Retried HTTP requests return the exact same task even if the first attempt already changed state.
  try{const previous=JSON.parse(await readFile(join(this.queue.directory,jobId+'.identity'),'utf8'));if(JSON.stringify(previous.payload)!==JSON.stringify(payload))throw new BusinessError('REQUEST_ID_CONFLICT','提交标识已被用于其他操作',409);return {jobId,reused:true};}catch(error){if((error as NodeJS.ErrnoException).code!=='ENOENT')throw error;}
  await this.preflight(action,target);
  try{await this.queue.enqueue(`settlement.${action}`,payload,jobId);}catch(error){if((error as NodeJS.ErrnoException).code!=='EEXIST')throw new BusinessError('SETTLEMENT_QUEUE_FAILED','任务未能入队，请重试',503);const previous=JSON.parse(await readFile(join(this.queue.directory,jobId+'.identity'),'utf8'));if(JSON.stringify(previous.payload)!==JSON.stringify(payload))throw new BusinessError('REQUEST_ID_CONFLICT','提交标识已被用于其他操作',409);}
  return {jobId,reused:false};
 }
 async status(jobId:string){
  if(!/^settlement-(judge|finalize|rebuild)-[a-f0-9-]{36}$/.test(jobId))throw new BusinessError('VALIDATION','任务编号无效',400);
  for(const state of ['queued','running','done','failed'])try{const task=JSON.parse(await readFile(join(this.queue.directory,state,jobId+'.json'),'utf8'));return {jobId,status:state==='queued'?'pending':state,attempts:task.attempts,result:state==='done'||state==='failed'?task.result:undefined};}catch(error){if((error as NodeJS.ErrnoException).code!=='ENOENT')throw error;}
  throw new BusinessError('NOT_FOUND','任务不存在',404);
 }
 async process(input:unknown){
  const parsed=taskSchema.safeParse(input);if(!parsed.success)return {ok:false,code:'INVALID_TASK',message:'任务参数无效'};const task=parsed.data;
  try{if(task.action==='rebuild')return await this.settlement.rebuild(task.target,task.actor);if(task.action==='finalize')return await this.settlement.finalize(Number(task.target),task.actor);return {ok:true,...await this.settlement.judgeImport(Number(task.target),task.actor)};}
  catch(error){if(error instanceof BusinessError)return {ok:false,code:error.code,message:error.message};throw error;}
 }
}
@Controller('settlement/jobs')
export class SettlementJobsController{
 constructor(@Inject(AuthService)private readonly auth:AuthService,@Inject(SettlementJobsService)private readonly jobs:SettlementJobsService){}
 @Get(':id')async status(@Req()req:Request,@Param('id')id:string){await this.auth.require(req,{admin:true});return this.jobs.status(id);}
}
export async function runSettlementTask(payload:unknown){const db=new Database(loadConfig());try{const sync=new SettlementSyncService(db,new ReportComparisonService(db));return await new SettlementJobsService(db,new SettlementService(db,sync)).process(payload);}finally{await db.onModuleDestroy();}}
