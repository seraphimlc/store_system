import 'reflect-metadata';
import {Body,Controller,Get,Inject,Injectable,Post,Query,Req} from '@nestjs/common';
import type {Request} from 'express';
import {z} from 'zod';
import {createHash,randomUUID} from 'node:crypto';
import {readFile,writeFile,rename,mkdir} from 'node:fs/promises';
import {resolve,join} from 'node:path';
import {Database} from './database';
import {AuthService} from './auth';
import {BusinessError} from './errors';
import {loadConfig} from './config';
import {DurableQueue} from '../../worker/src/queue';
import {configuredReportModel,type ReportModel} from './report-model';
export interface DashboardMonth {month:string;employees:number;records:number;p1:number;p2:number;points:number;amount:number;p2rate:number;per_emp_points:number;per_emp_amount:number;per_emp_records:number;per_store_points:number;[key:`d_${string}`]:number|null}
export interface DashboardStaffMonth {month:string;records:number;p1:number;p2:number;points:number;amount:number;dups:number|null}
interface InsightPointer {id:string;code:string;month:string;fingerprint:string}
export function monthlyDashboard(rows:{month:string;employees:number;records:number;p1:number;p2:number;points:number;amount:number}[]):DashboardMonth[]{
 const result=rows.map(row=>({...row,p2rate:row.records?row.p2/row.records:0,per_emp_points:row.employees?row.points/row.employees:0,per_emp_amount:row.employees?row.amount/row.employees:0,per_emp_records:row.employees?row.records/row.employees:0,per_store_points:row.records?row.points/row.records:0} as DashboardMonth));
 for(let i=1;i<result.length;i++)for(const field of ['points','amount','records','employees','per_emp_points','per_emp_amount','per_emp_records','p2rate'] as const){const previous=result[i-1][field];result[i][`d_${field}`]=previous?(result[i][field]-previous)/previous:null;}
 return result;
}
function parse<T>(schema:z.ZodType<T>,value:unknown){const result=schema.safeParse(value);if(!result.success)throw new BusinessError('VALIDATION','概览月份或员工编号无效',400);return result.data;}
const selector=z.object({month:z.string().regex(/^\d{4}-(0[1-9]|1[0-2])$/).optional(),code:z.string().max(32).default('')}).strict();
function safePayload(value:string|null):unknown{if(!value)return null;try{return JSON.parse(value);}catch{return null;}}
function fingerprint(value:unknown){return createHash('sha256').update('dashboard-insight-v1\n'+JSON.stringify(value)).digest('hex');}
@Injectable()
export class DashboardService {
 readonly queue:DurableQueue;
 constructor(@Inject(Database)private readonly db:Database){this.queue=new DurableQueue(resolve(process.env.NEXT_TASK_DIRECTORY||'data/refactor/jobs'));}
 async monthly(){
  const rows=(await this.db.query<{month:string;employees:number;records:number;p1:number;p2:number;points:number;amount:string}>(`SELECT month,array_agg(person_code) AS codes,count(*)::int AS employees,coalesce(sum(records),0)::int AS records,coalesce(sum(p1),0)::int AS p1,coalesce(sum(p2),0)::int AS p2,coalesce(sum(points),0)::int AS points,coalesce(sum(salary),0)::bigint::text AS amount FROM month_perf_records GROUP BY month ORDER BY month`)).rows;
  const timeline=monthlyDashboard(rows.map(({codes,...row}:typeof rows[number]&{codes?:string[]})=>({...row,amount:Number(row.amount)})));
  return timeline.map((row,index)=>{const codes=(rows[index] as typeof rows[number]&{codes:string[]}).codes;const previous=new Set(index?(rows[index-1] as typeof rows[number]&{codes:string[]}).codes:[]);const current=new Set(codes);return {...row,newEmployees:codes.filter(code=>!previous.has(code)).length,goneEmployees:[...previous].filter(code=>!current.has(code)).length};});
 }
 async overview(requested?:string){
  const monthly=await this.monthly();const selected=monthly.find(row=>row.month===requested)??monthly.at(-1)??null;
  if(requested&&!monthly.some(row=>row.month===requested))throw new BusinessError('NOT_FOUND','该月份暂无绩效数据',404);
  const people=(await this.db.query<{code:string;name:string}>('SELECT code,display_name AS name FROM persons ORDER BY code')).rows;
  if(!selected)return {monthly,current:null,previous:null,people,top:[],newStaff:[],goneStaff:[],quality:null};
  const previous=monthly[monthly.indexOf(selected)-1]??null;
  const entries=(await this.db.query<{code:string;name:string;month:string;points:number;amount:string;records:number;p1:number;p2:number}>(`SELECT m.person_code AS code,coalesce(p.display_name,m.person_code) AS name,m.month,m.points,m.salary::text AS amount,m.records,m.p1,m.p2 FROM month_perf_records m LEFT JOIN persons p ON p.code=m.person_code WHERE m.month=ANY($1::text[]) ORDER BY m.points DESC,m.person_code`,[[selected.month,...previous?[previous.month]:[]]])).rows.map(row=>({...row,amount:Number(row.amount)}));
  const current=entries.filter(row=>row.month===selected.month),prior=entries.filter(row=>row.month===previous?.month);const currentCodes=new Set(current.map(row=>row.code)),priorCodes=new Set(prior.map(row=>row.code));
  const payloadRows=(await this.db.query<{metric:string;payload:string|null;value:number}>('SELECT metric,payload,value FROM dash_metrics WHERE month=$1 AND person IS NULL',[selected.month])).rows;
  const payloads=Object.fromEntries(payloadRows.map(row=>[row.metric,safePayload(row.payload)]));
  return {monthly,current:selected,previous,people,top:current.slice(0,8),newStaff:current.filter(row=>!priorCodes.has(row.code)),goneStaff:prior.filter(row=>!currentCodes.has(row.code)),quality:payloads.quality&&typeof payloads.quality==='object'?payloads.quality:null};
 }
 async staff(code:string){
  const person=(await this.db.query<{code:string;name:string}>('SELECT code,display_name AS name FROM persons WHERE code=$1',[code])).rows[0];if(!person)throw new BusinessError('NOT_FOUND','员工不存在',404);
  const rows=(await this.db.query<{month:string;records:number;p1:number;p2:number;points:number;amount:string}>('SELECT month,records,p1,p2,points,salary::text AS amount FROM month_perf_records WHERE person_code=$1 ORDER BY month',[code])).rows;
  const duplicateRows=(await this.db.query<{month:string;payload:string|null}>("SELECT month,payload FROM dash_metrics WHERE metric='dup_map' AND month=ANY($1::text[])",[rows.map(row=>row.month)])).rows;
  const duplicates=new Map(duplicateRows.map(row=>[row.month,safePayload(row.payload)]));
  const series:DashboardStaffMonth[]=rows.map(row=>{const values=duplicates.get(row.month);const raw=values&&typeof values==='object'?(values as Record<string,unknown>)[code]:undefined;return {...row,amount:Number(row.amount),dups:typeof raw==='number'?raw:values&&typeof values==='object'?0:null};});
  return {person,series,sampleOk:(series.at(-1)?.records??0)>=5};
 }
 async facts(code:string,month?:string){
  const overview=await this.overview();
  if(!overview.current)throw new BusinessError('DASHBOARD_NO_DATA','暂无可分析的绩效数据',409);
  const staff=code?await this.staff(code):null;
  if(staff&&(!staff.sampleOk||!staff.series.length))throw new BusinessError('INSIGHT_SAMPLE_SMALL','该员工最近月有效店少于5家，样本不足',409);
  const selectedMonth=code?staff!.series.at(-1)!.month:'ALL';
  const facts={monthly:overview.monthly,current:overview.current,previous:overview.previous,top:overview.top,newStaff:overview.newStaff,goneStaff:overview.goneStaff,quality:overview.quality,staff};
  return {code:code||'COMPANY',month:selectedMonth,facts,fingerprint:fingerprint(facts)};
 }
 private pointerPath(code:string,month:string){return join(this.queue.directory,'dashboard-'+createHash('sha256').update(code+'\n'+month).digest('hex')+'.pointer.json');}
 private async readPointer(code:string,month:string){try{return JSON.parse(await readFile(this.pointerPath(code,month),'utf8')) as InsightPointer;}catch(error){if((error as NodeJS.ErrnoException).code==='ENOENT')return null;throw error;}}
 async jobState(pointer:InsightPointer){
  for(const state of ['queued','running','done','failed'])try{const task=JSON.parse(await readFile(join(this.queue.directory,state,pointer.id+'.json'),'utf8'));return {status:state==='queued'?'pending':state,result:state==='failed'||state==='done'?task.result:undefined};}catch(error){if((error as NodeJS.ErrnoException).code!=='ENOENT')throw error;}
  return {status:'pending'};
 }
 async insight(code:string,month?:string){
  const target=await this.facts(code,month);
  const row=(await this.db.query<{content:string;fingerprint:string;updated_at:Date}>('SELECT content,fingerprint,updated_at FROM staff_analyses WHERE person_code=$1 AND month=$2',[target.code,target.month])).rows[0]??null;
  const pointer=await this.readPointer(target.code,target.month);
  return {code:target.code,month:target.month,fingerprint:target.fingerprint,content:row?.content??'',stale:!!row?.content&&row.fingerprint!==target.fingerprint,updatedAt:row?.updated_at??null,job:pointer&&pointer.fingerprint===target.fingerprint?await this.jobState(pointer):null};
 }
 async start(code:string,month?:string,force=false){
  if(process.env.VISIT_REPORT_AI==='0')throw new BusinessError('REPORT_AI_DISABLED','AI分析已关闭',409);
  const target=await this.facts(code,month);
  return this.db.transaction(async client=>{
   await client.query('SELECT pg_advisory_xact_lock(hashtext(\'dashboard-analysis\'),hashtext($1))',[target.code+'/'+target.month]);
   const cache=(await client.query('SELECT content,fingerprint FROM staff_analyses WHERE person_code=$1 AND month=$2',[target.code,target.month])).rows[0];
   if(cache?.content&&cache.fingerprint===target.fingerprint&&!force)return {reused:true,status:'done'};
   const previous=await this.readPointer(target.code,target.month);
   if(previous?.fingerprint===target.fingerprint){const state=await this.jobState(previous);if(['pending','running'].includes(state.status))return {id:previous.id,reused:true,status:state.status};}
   const pointer:InsightPointer={id:'dashboard-'+randomUUID(),code:target.code,month:target.month,fingerprint:target.fingerprint};
   await mkdir(this.queue.directory,{recursive:true,mode:0o700});const path=this.pointerPath(target.code,target.month);await writeFile(path+'.tmp',JSON.stringify(pointer),{mode:0o600});await rename(path+'.tmp',path);
   try{await this.queue.enqueue('dashboard.analysis',pointer,pointer.id);}catch{throw new BusinessError('INSIGHT_QUEUE_FAILED','分析任务入队失败，请重试',503);}
   return {id:pointer.id,reused:false,status:'pending'};
  });
 }
 async process(pointer:InsightPointer,model:ReportModel=configuredReportModel){
  const current=await this.readPointer(pointer.code,pointer.month);if(current?.id!==pointer.id)return {skipped:true};
  let target;try{target=await this.facts(pointer.code==='COMPANY'?'':pointer.code,pointer.month==='ALL'?undefined:pointer.month);}catch{return {code:'INSIGHT_NO_LONGER_AVAILABLE'};}
  if(target.fingerprint!==pointer.fingerprint)return {code:'INSIGHT_DATA_CHANGED'};
  const prompt='你是日本巡店业务的经营分析师。只依据下方程序算好的数字，用中文给管理员说明趋势、对比、可能原因与具体建议，不编造环比、人员或缺失的质量分类；缺少样本应说明。不要输出思维链或个人定性指控。返回严格JSON：{"content":"精炼中文结论，可用简单Markdown加粗，最多1200字"}。\n'+JSON.stringify(target.facts);
  let content:string;
  try{const result=await model(prompt,'zh',pointer.id);const value=(result.data as {content?:unknown})?.content;if(typeof value!=='string'||!value.trim())return {code:'AI_INVALID_CONTENT'};content=value.trim().slice(0,8000);}
  catch(error){return {code:error instanceof Error&&/^AI_[A-Z0-9_]+$/.test(error.message)?error.message:'AI_GENERATION_FAILED'};}
  const latest=await this.facts(pointer.code==='COMPANY'?'':pointer.code,pointer.month==='ALL'?undefined:pointer.month);if(latest.fingerprint!==pointer.fingerprint||(await this.readPointer(pointer.code,pointer.month))?.id!==pointer.id)return {code:'INSIGHT_DATA_CHANGED'};
  await this.db.transaction(async client=>{
   await client.query('SELECT pg_advisory_xact_lock(hashtext(\'dashboard-analysis\'),hashtext($1))',[pointer.code+'/'+pointer.month]);
   if((await this.readPointer(pointer.code,pointer.month))?.id!==pointer.id)return;
   await client.query(`INSERT INTO staff_analyses(person_code,month,content,fingerprint,created_at,updated_at) VALUES($1,$2,$3,$4,now(),now()) ON CONFLICT(person_code,month) DO UPDATE SET content=excluded.content,fingerprint=excluded.fingerprint,updated_at=now()`,[pointer.code,pointer.month,content,pointer.fingerprint]);
  });return {ok:true};
 }
 async reconcile(){
  const {readdir}=await import('node:fs/promises');await this.queue.init();
  for(const name of await readdir(this.queue.directory)){
   if(!name.startsWith('dashboard-')||!name.endsWith('.pointer.json'))continue;
   const pointer=JSON.parse(await readFile(join(this.queue.directory,name),'utf8')) as InsightPointer;
   if((await this.jobState(pointer)).status==='pending')try{await this.queue.enqueue('dashboard.analysis',pointer,pointer.id);}catch(error){if((error as NodeJS.ErrnoException).code!=='EEXIST')throw error;}
  }
 }
}
@Controller('dashboard')
export class DashboardController{
 constructor(@Inject(AuthService)private readonly auth:AuthService,@Inject(DashboardService)private readonly service:DashboardService){}
 @Get('overview')async overview(@Req()req:Request,@Query()query:unknown){await this.auth.require(req,{admin:true});const input=parse(selector,query);return this.service.overview(input.month);}
 @Get('staff')async staff(@Req()req:Request,@Query()query:unknown){await this.auth.require(req,{admin:true});const input=parse(selector,query);if(!input.code)throw new BusinessError('VALIDATION','请选择员工',400);return this.service.staff(input.code);}
 @Get('insight')async insight(@Req()req:Request,@Query()query:unknown){await this.auth.require(req,{admin:true});const input=parse(selector,query);return this.service.insight(input.code,input.month);}
 @Post('insight')async start(@Req()req:Request,@Body()body:unknown){await this.auth.require(req,{admin:true,write:true});const input=parse(selector.extend({force:z.boolean().default(false)}),body);return this.service.start(input.code,input.month,input.force);}
}
export async function runDashboardTask(payload:unknown){const input=z.object({id:z.string().regex(/^dashboard-[a-f0-9-]+$/),code:z.string().min(1).max(32),month:z.string(),fingerprint:z.string().regex(/^[a-f0-9]{64}$/)}).strict().parse(payload);const db=new Database(loadConfig());try{return await new DashboardService(db).process(input);}finally{await db.onModuleDestroy();}}
export async function reconcileDashboardTasks(){const db=new Database(loadConfig());try{await new DashboardService(db).reconcile();}finally{await db.onModuleDestroy();}}
