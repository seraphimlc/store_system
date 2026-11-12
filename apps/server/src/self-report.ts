/** Sole orchestration boundary for staff report counts + station progress. Never writes settlement tables. */
import {Body,Controller,Get,Inject,Injectable,Post,Query,Req} from '@nestjs/common';import type {Request} from 'express';import type {PoolClient} from 'pg';import {z} from 'zod';import {createHash} from 'node:crypto';import {Database} from './database';import {AuthService} from './auth';import {BusinessError} from './errors';import {DailyReportsService} from './daily-reports';import {StationTasksService} from './station-tasks';import {jstToday,validDate} from './business-dates';
const stamp=(value:unknown)=>createHash('sha256').update(JSON.stringify(value)).digest('hex'),date=z.string().refine(value=>validDate(value)&&!value.startsWith('0000'));
const fields=z.object({area:z.string().max(4096).default(''),p1:z.number().int().min(0).max(999),p2:z.number().int().min(0).max(999),items:z.array(z.object({taskId:z.number().int().positive().max(2147483647),pct:z.number().int().min(0).max(100).nullable().optional(),note:z.string().trim().max(4000).default(''),storeCount:z.number().int().min(0).max(2147483647).optional(),token:z.string().regex(/^[a-f0-9]{64}$/)}).strict()).max(1000).default([]),token:z.string().regex(/^[a-f0-9]{64}$/)}).strict().refine(value=>new Set(value.items.map(item=>item.taskId)).size===value.items.length);
function parse<T>(schema:z.ZodType<T>,input:unknown){const result=schema.safeParse(input);if(!result.success)throw new BusinessError('VALIDATION','请检查日报店数、任务进度和日期',400);return result.data;}
@Injectable()
export class SelfReportService{
 constructor(@Inject(Database)private readonly db:Database,@Inject(DailyReportsService)private readonly reports:DailyReportsService,@Inject(StationTasksService)private readonly tasks:StationTasksService){}
 private async actor(client:PoolClient,id:number,write=false){const row=(await client.query(`SELECT id,person_code AS code,role,username FROM users WHERE id=$1 AND is_active=true AND status IN('active','leave') ${write?'FOR SHARE':''}`,[id])).rows[0];if(!row)throw new BusinessError('FORBIDDEN','账号状态已变化',403);return row;}
 private async report(client:PoolClient,code:string,day:string){const report=(await client.query('SELECT id,area,p1_cnt AS p1,p2_cnt AS p2,source,submitted_at::text AS submitted FROM staff_daily_reports WHERE person_code=$1 AND report_date=$2',[code,day])).rows[0]??null;return {report,token:stamp({code,day,report})};}
 private async rows(client:PoolClient,code:string,day:string,admin:boolean){
  const result=await client.query(`SELECT t.id AS "taskId",coalesce(place.name,s.name,'#'||t.id::text) AS label,t.pct AS "currentPct",p.pct AS "dayPct",p.reported_pct AS "reportedPct",p.review_status AS review,p.note,p.reported_by AS "reportedBy",p.submitted_by AS "submittedBy",t.store_count AS "storeCount",a.dispatch_date::text AS "dispatchDate",
   md5(jsonb_build_object('task',to_jsonb(t),'progress',to_jsonb(p),'assigned',(SELECT coalesce(jsonb_agg(to_jsonb(ta) ORDER BY ta.person_code),'[]') FROM bd_task_assign ta WHERE ta.task_id=t.id))::text) AS token
   FROM bd_task t LEFT JOIN bd_station_place place ON place.id=t.place_id LEFT JOIN bd_station s ON s.id=t.station_id LEFT JOIN bd_task_progress p ON p.task_id=t.id AND p.progress_date=$2 LEFT JOIN bd_task_assign a ON a.task_id=t.id AND a.person_code=$1
   WHERE CASE WHEN $3 THEN p.id IS NOT NULL AND (a.id IS NOT NULL OR EXISTS(SELECT 1 FROM users u WHERE u.person_code=$1 AND (u.username=p.reported_by OR u.username=p.submitted_by)))
   ELSE a.id IS NOT NULL AND t.state<>'done' AND EXISTS(SELECT 1 FROM bd_team_member m JOIN bd_team team ON team.id=m.team_id WHERE m.team_id=t.team_id AND m.person_code=$1 AND m.end_date IS NULL AND m.start_date<=$2 AND team.status='active') END ORDER BY t.id`,[code,day,admin]);
  return result.rows.map(row=>({...row,token:stamp(row.token),pct:admin?row.dayPct:row.currentPct,editable:admin?['confirmed','adjusted','rejected'].includes(row.review):row.review!=='confirmed'}));
 }
 async view(actorId:number,personCode?:string,day=jstToday()){
  return this.db.transaction(async client=>{await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');const actor=await this.actor(client,actorId),admin=personCode!==undefined;if(admin&&actor.role!=='admin')throw new BusinessError('FORBIDDEN','仅管理员可编辑他人日报',403);const code=personCode??actor.code;if(!code)throw new BusinessError('PERSON_NOT_FOUND','账号未绑定员工编号',400);const report=await this.report(client,code,day),coverage=await this.reports.coverage(client);return {...report,date:day,personCode:code,backfill:report.report===null,locked:!!coverage.end&&day<=coverage.end,items:admin&&!report.report?[]:await this.rows(client,code,day,admin)};});
 }
 async submit(actorId:number,value:unknown,personCode?:string,day=jstToday()){
  const input=parse(fields,value);return this.db.transaction(async client=>{
   await client.query("SELECT pg_advisory_xact_lock(hashtext('teams-write'),0)");const actor=await this.actor(client,actorId,true),admin=personCode!==undefined;if(admin&&actor.role!=='admin')throw new BusinessError('FORBIDDEN','仅管理员可编辑他人日报',403);const code=personCode??actor.code;if(!code)throw new BusinessError('PERSON_NOT_FOUND','账号未绑定员工编号',400);
   await client.query('SELECT pg_advisory_xact_lock(hashtext($1),hashtext($2))',[code,day]);const before=await this.report(client,code,day);if(before.token!==input.token)throw new BusinessError('SELF_REPORT_CHANGED','日报已变化，请重新读取后保存',409);if(admin&&!before.report&&input.items.length)throw new BusinessError('BACKFILL_POINTS_ONLY','这天没有日报，只能补点数，不能补任务进展',409);
   const candidates=await this.rows(client,code,day,admin),byId=new Map(candidates.map(row=>[row.taskId,row]));
   await this.reports.saveTransaction(client,code,day,actorId,input,admin?'admin':before.report?'update':'create',false);
   let changed=0,skipped=0;
   for(const item of [...input.items].sort((a,b)=>a.taskId-b.taskId)){
    if(item.pct===null||item.pct===undefined){skipped++;continue;}
    const row=byId.get(item.taskId);if(!row)throw new BusinessError('TASK_NOT_IN_REPORT','任务不属于本次人员日期的可填报范围，保存已回滚',403);
    if(row.token!==item.token)throw new BusinessError('TASK_CHANGED','任务已变化，日报与进度未保存，请刷新后重试',409);
    // Preserve legacy unified-form no-op behavior; store-count-only values do not reopen a task.
    if(row.pct===item.pct&&!item.note){skipped++;continue;}
    if(!row.editable)throw new BusinessError('TASK_REVIEW_LOCKED','进度尚不符合修改条件或已经确认，日报与进度均未保存',409);
    if(!admin&&item.pct===100&&item.storeCount===undefined)throw new BusinessError('STORE_COUNT_REQUIRED','完成任务须填店铺数，可以填0',400);
    // Obtain the same core token within this transaction, never opening a second transaction.
    await this.tasks.reportForSelfReport(client,item.taskId,actorId,{pct:item.pct,note:item.note,storeCount:item.storeCount},day,admin);changed++;
   }
   await this.reports.synchronize(client,code,day,true);return {ok:true,date:day,tasks:changed,skipped,created:before.report===null};
  });
 }
}
@Controller('self-report')
export class SelfReportController{
 constructor(@Inject(AuthService)private readonly auth:AuthService,@Inject(SelfReportService)private readonly service:SelfReportService){}
 @Get('mine')async view(@Req()req:Request){const {user}=await this.auth.require(req);return this.service.view(user.id);}
 @Post('mine')async submit(@Req()req:Request,@Body()body:unknown){const {user}=await this.auth.require(req,{write:true});return this.service.submit(user.id,body);}
 @Get('day')async day(@Req()req:Request,@Query()query:unknown){const {user}=await this.auth.require(req,{admin:true});const input=parse(z.object({personCode:z.string().trim().min(1).max(32),date}).strict(),query);return this.service.view(user.id,input.personCode,input.date);}
 @Post('day')async saveDay(@Req()req:Request,@Body()body:unknown){const {user}=await this.auth.require(req,{admin:true,write:true});const target=parse(z.object({personCode:z.string().trim().min(1).max(32),date,data:z.unknown()}).strict(),body);return this.service.submit(user.id,target.data,target.personCode,target.date);}
}
