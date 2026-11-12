import 'reflect-metadata';
import {Body,Controller,Get,Inject,Injectable,Param,Post,Query,Req} from '@nestjs/common';
import type {Request} from 'express';
import type {PoolClient,QueryResultRow} from 'pg';
import {createHash} from 'node:crypto';
import {resolve} from 'node:path';
import {access} from 'node:fs/promises';
import {z} from 'zod';
import {Database} from './database';
import {AuthService} from './auth';
import {BusinessError} from './errors';
import {loadConfig} from './config';
import {ReportComparisonService} from './report-comparison';
import {configuredReportModel,normalizeReport,reportFingerprint,reportPrompt,type ReportModel,type ReportLanguage,type ReportComment,type ModelResponse} from './report-model';
import {validDate} from './business-dates';
import {DurableQueue} from '../../worker/src/queue';
interface Analysis extends QueryResultRow {id:number;start:string;end:string;status:string;version:string;summary:Record<string,unknown>;payload:{by_lang?:Partial<Record<ReportLanguage,ReportComment>>;failed_langs?:string[];start?:string;end?:string};ai_tokens:number;ai_model:string;ai_error:string|null;created_at:Date;finished_at:Date|null}
const columns='id,period_start::text AS start,period_end::text AS end,status,created_at::text AS version,summary,payload,ai_tokens,ai_model,ai_error,created_at,finished_at';
export const ANALYSIS_STALE_MS=15*60*1000;
export function reportCanRetry(row:{status:string;created_at:Date}){return row.status==='failed'||['pending','running'].includes(row.status)&&Date.now()-row.created_at.getTime()>ANALYSIS_STALE_MS;}
function jobId(row:Analysis){return `report-${row.id}-${createHash('sha256').update(row.version).digest('hex').slice(0,16)}`;}
function parse<T>(schema:z.ZodType<T>,input:unknown){const result=schema.safeParse(input);if(!result.success)throw new BusinessError('VALIDATION','报告区间或编号无效',400);return result.data;}
const range=z.object({start:z.string().refine(validDate),end:z.string().refine(validDate)}).strict().refine(value=>value.start<=value.end);
@Injectable()
export class ReportAnalysisService{
 readonly queue:DurableQueue;
 constructor(@Inject(Database)private readonly db:Database,@Inject(ReportComparisonService)private readonly comparison:ReportComparisonService){this.queue=new DurableQueue(resolve(process.env.NEXT_TASK_DIRECTORY||'data/refactor/jobs'));}
 async enqueue(row:Analysis){
  try{await this.queue.enqueue('report.analysis',{id:row.id,version:row.version},jobId(row));}
  catch(error){if((error as NodeJS.ErrnoException).code==='EEXIST')return;await this.db.query("UPDATE staff_report_analyses SET status='failed',ai_error='任务入队失败，请重试',finished_at=now() WHERE id=$1 AND created_at::text=$2",[row.id,row.version]);throw new BusinessError('REPORT_QUEUE_FAILED','报告任务未能入队，请重试',503);}
 }
 async start(start:string,end:string,userId:number){
  if(process.env.VISIT_REPORT_AI==='0')throw new BusinessError('REPORT_AI_DISABLED','AI报告已关闭，数字对比仍可使用',409);
  const result=await this.db.transaction(async client=>{
   await client.query('SELECT pg_advisory_xact_lock(hashtext(\'report-analysis\'),hashtext($1))',[start+'/'+end]);
   const comparison=await this.comparison.compare(start,end,'','acc',client);if(!comparison.persons.length)throw new BusinessError('REPORT_NO_DATA','该区间没有可对比的数据',400);
   const fingerprint=reportFingerprint(comparison);
   const prior=(await client.query<Analysis>(`SELECT ${columns} FROM staff_report_analyses WHERE period_start=$1 AND period_end=$2 AND summary->>'fingerprint'=$3 AND (status='done' OR (status IN ('pending','running') AND created_at>now()-interval '15 minutes')) ORDER BY id DESC LIMIT 1`,[start,end,fingerprint])).rows[0];
   if(prior)return {row:prior,reused:true};
   const row=(await client.query<Analysis>(`INSERT INTO staff_report_analyses(period_start,period_end,status,summary,payload,ai_model,ai_tokens,created_by,created_at) VALUES($1,$2,'pending',$3,'{}','',0,$4,now()) RETURNING ${columns}`,[start,end,JSON.stringify({...comparison.summary,fingerprint}),userId])).rows[0];
   await this.comparison.materialize(client,row.id,comparison);return {row,reused:false};
  });
  if(result.row.status!=='done')await this.enqueue(result.row);
  return {id:result.row.id,status:result.row.status,reused:result.reused};
 }
 async retry(id:number){
  const row=await this.db.transaction(async client=>{
   const current=(await client.query<Analysis>(`SELECT ${columns} FROM staff_report_analyses WHERE id=$1 FOR UPDATE`,[id])).rows[0];if(!current)throw new BusinessError('NOT_FOUND','报告不存在',404);
   if(!reportCanRetry(current))throw new BusinessError('REPORT_NOT_RETRYABLE','报告已完成或正在生成，请稍后再试',409);
   return (await client.query<Analysis>(`UPDATE staff_report_analyses SET status='pending',payload='{}',ai_error=NULL,ai_tokens=0,created_at=now(),finished_at=NULL WHERE id=$1 RETURNING ${columns}`,[id])).rows[0];
  });await this.enqueue(row);return {id:row.id,status:row.status};
 }
 async list(start:string,end:string){return {items:(await this.db.query<Analysis>(`SELECT ${columns} FROM staff_report_analyses WHERE period_start=$1 AND period_end=$2 ORDER BY id DESC LIMIT 20`,[start,end])).rows};}
 async get(id:number){const row=(await this.db.query<Analysis>(`SELECT ${columns} FROM staff_report_analyses WHERE id=$1`,[id])).rows[0];if(!row)throw new BusinessError('NOT_FOUND','报告不存在',404);return row;}
 async remove(id:number){return this.db.transaction(async client=>{await client.query('DELETE FROM staff_report_compare_day WHERE analysis_id=$1',[id]);await client.query('DELETE FROM staff_report_compare_person WHERE analysis_id=$1',[id]);const result=await client.query('DELETE FROM staff_report_analyses WHERE id=$1 RETURNING id',[id]);return {deleted:!!result.rowCount};});}
 async process(id:number,version:string,model:ReportModel=configuredReportModel){
  const row=await this.db.transaction(async client=>{
   const current=(await client.query<Analysis>(`SELECT ${columns} FROM staff_report_analyses WHERE id=$1 FOR UPDATE`,[id])).rows[0];
   if(!current||current.version!==version||!['pending','running'].includes(current.status))return null;
   await client.query("UPDATE staff_report_analyses SET status='running' WHERE id=$1",[id]);return current;
  });if(!row)return {skipped:true};
  const comparison=await this.comparison.compare(row.start,row.end);const fingerprint=reportFingerprint(comparison);
  const by_lang:Partial<Record<ReportLanguage,ReportComment>>=row.summary?.fingerprint===fingerprint?(row.payload?.by_lang??{}):{};const failed:string[]=[];const errors:Record<string,string>={};let tokens=row.ai_tokens??0,usedModel=row.ai_model||'';
  const languages=(process.env.VISIT_REPORT_LANGS||'zh,ja').split(',').filter((language):language is ReportLanguage=>language==='zh'||language==='ja');
  const desired=languages.length?[...new Set(languages)]:['zh','ja'] as ReportLanguage[];
  for(const language of desired){
   if(by_lang[language])continue;
   let result:ModelResponse,comment:ReportComment;
   try{
    result=await model(reportPrompt(comparison,language),language,jobId(row)+-+language);
    comment=normalizeReport(result.data,comparison);
   }catch(error){failed.push(language);errors[language]=error instanceof Error&&/^AI_[A-Z0-9_]+$/.test(error.message)?error.message:'AI_GENERATION_FAILED';continue;}
   by_lang[language]=comment;tokens+=Number.isFinite(result.tokens)?result.tokens:0;usedModel=result.model;
   const saved=await this.db.query("UPDATE staff_report_analyses SET payload=$1,ai_tokens=$2,ai_model=$3 WHERE id=$4 AND created_at::text=$5 AND status='running' RETURNING id",[JSON.stringify({by_lang,start:row.start,end:row.end}),tokens,usedModel,id,version]);
   if(!saved.rowCount)return {skipped:true};
  }
  return this.db.transaction(async client=>{
   const current=(await client.query<Analysis>(`SELECT ${columns} FROM staff_report_analyses WHERE id=$1 FOR UPDATE`,[id])).rows[0];if(!current||current.version!==version||current.status!=='running')return {skipped:true};
   const latest=await this.comparison.compare(row.start,row.end,'','acc',client);const changed=reportFingerprint(latest)!==fingerprint;
   const status=!changed&&Object.keys(by_lang).length?'done':'failed';
   const payload={by_lang:changed?{}:by_lang,failed_langs:changed?desired:failed,start:row.start,end:row.end};
   await client.query(`UPDATE staff_report_analyses SET status=$1,summary=$2,payload=$3,ai_model=$4,ai_tokens=$5,ai_error=$6,finished_at=now() WHERE id=$7`,[status,JSON.stringify({...latest.summary,fingerprint:reportFingerprint(latest)}),JSON.stringify(payload),usedModel,tokens,changed?'数据已变化，请重新生成':failed.length?Object.entries(errors).map(([lang,error])=>lang+': '+error).join('; '):null,id]);
   await this.comparison.materialize(client,id,latest);return {status};
  });
 }
 async reconcile(){
  const pending=(await this.db.query<Analysis>(`SELECT ${columns} FROM staff_report_analyses WHERE status IN ('pending','running') ORDER BY id LIMIT 500`)).rows;
  for(const row of pending){
   const failed=resolve(this.queue.directory,'failed',jobId(row)+'.json');
   try{await access(failed);await this.db.query("UPDATE staff_report_analyses SET status='failed',ai_error='任务中断次数超过上限，请重试',finished_at=now() WHERE id=$1 AND created_at::text=$2",[row.id,row.version]);continue;}catch{}
   await this.enqueue(row);
  }
 }
}
@Controller('reports/analyses')
export class ReportAnalysisController{
 constructor(@Inject(AuthService)private readonly auth:AuthService,@Inject(ReportAnalysisService)private readonly service:ReportAnalysisService){}
 @Get()async list(@Req()req:Request,@Query()query:unknown){await this.auth.require(req,{admin:true});const input=parse(range,query);const result=await this.service.list(input.start,input.end);return {items:result.items.map(({payload,...row})=>({...row,canRetry:reportCanRetry(row)}))};}
 @Get(':id')async detail(@Req()req:Request,@Param('id')id:string){await this.auth.require(req,{admin:true});return this.service.get(parse(z.coerce.number().int().positive(),id));}
 @Post()async start(@Req()req:Request,@Body()body:unknown){const {user}=await this.auth.require(req,{admin:true,write:true});const input=parse(range,body);return this.service.start(input.start,input.end,user.id);}
 @Post(':id/retry')async retry(@Req()req:Request,@Param('id')id:string,@Body()body:unknown){await this.auth.require(req,{admin:true,write:true});parse(z.object({}).strict(),body);return this.service.retry(parse(z.coerce.number().int().positive(),id));}
 @Post(':id/delete')async remove(@Req()req:Request,@Param('id')id:string,@Body()body:unknown){await this.auth.require(req,{admin:true,write:true});parse(z.object({}).strict(),body);return this.service.remove(parse(z.coerce.number().int().positive(),id));}
}
export async function runReportTask(payload:unknown){
 const input=z.object({id:z.number().int().positive(),version:z.string()}).strict().parse(payload);const db=new Database(loadConfig());
 try{return await new ReportAnalysisService(db,new ReportComparisonService(db)).process(input.id,input.version);}finally{await db.onModuleDestroy();}
}
export async function reconcileReportTasks(){const db=new Database(loadConfig());try{await new ReportAnalysisService(db,new ReportComparisonService(db)).reconcile();}finally{await db.onModuleDestroy();}}
