import { Body, Controller, Get, Inject, Injectable, Param, Post, Query, Req, Res } from '@nestjs/common';
import type { Request, Response } from 'express';
import type { PoolClient } from 'pg';
import { z } from 'zod';
import { Database } from './database';
import { AuthService } from './auth';
import { BusinessError } from './errors';
import { jstToday, monthRange, validDate } from './business-dates';
import { ReportComparisonService, reportPoints, scoreShare } from './report-comparison';
const date=z.string().refine(validDate,'日期无效');
const counts=z.object({area:z.string().max(4096).default(''),p1:z.number().int().min(0).max(999),p2:z.number().int().min(0).max(999)});
const range=z.object({start:date,end:date,personCode:z.string().max(32).default(''),page:z.coerce.number().int().min(1).max(10000).default(1)}).strict().refine(value=>value.start<=value.end);
function parse<T>(schema:z.ZodType<T>,value:unknown){const result=schema.safeParse(value);if(!result.success)throw new BusinessError('VALIDATION','请检查日期和店铺数（0至999的整数）',400);return result.data;}
export interface ReportInput {area:string;p1:number;p2:number}
interface ReportRow {id:number;personCode:string;date:string;area:string;p1:number;p2:number;total:number;source:string;submittedAt:string}
const fields=`r.id,r.person_code AS "personCode",r.report_date::text AS date,r.area,r.p1_cnt AS p1,r.p2_cnt AS p2,r.total_cnt AS total,r.source,r.submitted_at AS "submittedAt"`;
function enrich(row:ReportRow){return {...row,points:reportPoints(row.p1,row.p2),scoreShare:scoreShare(row.p1,row.p2),storeShare:row.total?row.p2/row.total:null};}
@Injectable()
export class DailyReportsService {
 constructor(@Inject(Database)private readonly db:Database,@Inject(ReportComparisonService)private readonly comparison:ReportComparisonService){}
 async people(){return this.db.query<{code:string;name:string}>('SELECT code,display_name AS name FROM persons ORDER BY display_name,code');}
 async teamPeople(code:string){return (await this.db.query<{code:string;name:string}>(`SELECT DISTINCT p.code,p.display_name AS name FROM bd_team_member leader JOIN bd_team team ON team.id=leader.team_id JOIN bd_team_member member ON member.team_id=team.id JOIN persons p ON p.code=member.person_code WHERE leader.person_code=$1 AND leader.role='leader' AND leader.end_date IS NULL AND member.end_date IS NULL AND team.status='active' ORDER BY p.display_name,p.code`,[code])).rows;}
 async coverage(client?:PoolClient){const sql='SELECT min(japan_date)::text AS start,max(japan_date)::text AS end FROM formal_records';return (client?await client.query(sql):await this.db.query(sql)).rows[0] as {start:string|null;end:string|null};}
 async assertWritable(client:PoolClient,day:string){const cov=await this.coverage(client);if(cov.end&&day<=cov.end)throw new BusinessError('REPORT_LOCKED','该日期已对账，无法修改或删除自报',409);}
 async synchronize(client:PoolClient,code:string,day:string,reported:boolean){
  if(reported)await client.query(`INSERT INTO staff_date_plans(person_code,plan_date,available,reported,source,created_at,updated_at) VALUES($1,$2,true,true,'report',now(),now())
   ON CONFLICT(person_code,plan_date) DO UPDATE SET reported=true,updated_at=now()`,[code,day]);
  else await client.query('UPDATE staff_date_plans SET reported=false,updated_at=now() WHERE person_code=$1 AND plan_date=$2',[code,day]);
  await this.comparison.refresh(client,day);
 }
 async save(code:string,day:string,userId:number,input:ReportInput,mode:'create'|'update'|'admin'){
  try{return await this.db.transaction(client=>this.saveTransaction(client,code,day,userId,input,mode));}catch(error){if((error as {code?:string}).code==='23505')throw new BusinessError('ALREADY_SUBMITTED','当天已填报，请刷新重试',409);throw error;}
 }
 async saveTransaction(client:PoolClient,code:string,day:string,userId:number,input:ReportInput,mode:'create'|'update'|'admin',synchronize=true){
   if(!(await client.query('SELECT code FROM persons WHERE code=$1',[code])).rowCount)throw new BusinessError('PERSON_NOT_FOUND','账号未绑定有效员工编号',400);
   await this.assertWritable(client,day);
   // Serialize this person's report date without adding a revision column or changing schema.
   await client.query('SELECT pg_advisory_xact_lock(hashtext($1),hashtext($2))',[code,day]);
   const existing=(await client.query('SELECT id FROM staff_daily_reports WHERE person_code=$1 AND report_date=$2 FOR UPDATE',[code,day])).rows[0];
   if(mode==='create'&&existing)throw new BusinessError('ALREADY_SUBMITTED','今天已填报，请使用修改入口',409);
   if(mode==='update'&&!existing)throw new BusinessError('REPORT_NOT_FOUND','今天尚未填报',404);
   const area=[...input.area.trim()].slice(0,64).join('');
   if(existing)await client.query(`UPDATE staff_daily_reports SET area=$1,p1_cnt=$2,p2_cnt=$3,total_cnt=$2::int+$3::int,source=CASE WHEN $4='admin' THEN 'admin' ELSE source END WHERE id=$5`,[area,input.p1,input.p2,mode,existing.id]);
   else await client.query(`INSERT INTO staff_daily_reports(person_code,report_date,user_id,area,p1_cnt,p2_cnt,total_cnt,source,submitted_at,created_at,client_ts) VALUES($1,$2,$3,$4,$5,$6,$5::int+$6::int,$7,now(),now(),'')`,[code,day,userId,area,input.p1,input.p2,mode==='admin'?'admin':'web']);
   if(synchronize)await this.synchronize(client,code,day,true);
   return {ok:true,date:day,points:reportPoints(input.p1,input.p2)};
 }
 async remove(code:string,day:string){return this.db.transaction(async client=>{
  await this.assertWritable(client,day);await client.query('SELECT pg_advisory_xact_lock(hashtext($1),hashtext($2))',[code,day]);
  const deleted=await client.query('DELETE FROM staff_daily_reports WHERE person_code=$1 AND report_date=$2 RETURNING id',[code,day]);
  if(deleted.rowCount)await this.synchronize(client,code,day,false);
  return {deleted:!!deleted.rowCount};
 });}
 async list(start:string,end:string,code:string,page:number,limit=30){
  const result=await this.db.query(`WITH matching AS (SELECT ${fields},coalesce(p.display_name,r.person_code) AS name FROM staff_daily_reports r LEFT JOIN persons p ON p.code=r.person_code WHERE report_date BETWEEN $1::date AND $2::date AND ($3='' OR r.person_code=$3)),paged AS (SELECT * FROM matching ORDER BY date DESC,"personCode",id LIMIT $4 OFFSET $5)
   SELECT (SELECT count(*)::int FROM matching) AS total,coalesce((SELECT json_agg(paged) FROM paged),'[]'::json) AS items,
   (SELECT json_build_object('p1',coalesce(sum(p1),0),'p2',coalesce(sum(p2),0),'total',coalesce(sum(total),0),'points',coalesce(sum(p1+2*p2),0)) FROM matching) AS summary`,[start,end,code,limit,(page-1)*limit]);
  const value=result.rows[0];return {...value,items:(value.items as ReportRow[]).map(enrich),page,pageSize:limit,coverage:await this.coverage()};
 }
 async feedback(code:string,id?:number){
  const cov=await this.coverage();if(!cov.start||!cov.end)return {periods:[],person:null,days:[]};
  const periods=(await this.db.query(`WITH periods AS (SELECT DISTINCT ON(a.period_start,a.period_end) a.id,a.period_start::text AS start,a.period_end::text AS end
    FROM staff_report_analyses a JOIN staff_report_compare_person p ON p.analysis_id=a.id WHERE p.person_code=$1 AND a.status IN ('done','failed') AND a.period_start>=$2 AND a.period_end<=$3
    ORDER BY a.period_start DESC,a.period_end DESC,a.id DESC) SELECT * FROM periods ORDER BY id DESC LIMIT 12`,[code,cov.start,cov.end])).rows;
  const selected=id?periods.find(p=>p.id===id):periods[0];if(!selected)return {periods,person:null,days:[]};
  const person=(await this.db.query('SELECT person_code,name,sys_p1,sys_p2,sys_total,rep_p1,rep_p2,rep_total,d1,d2,d_total,acc,days_filled,days_system,days_both,gaps,abs_dt FROM staff_report_compare_person WHERE analysis_id=$1 AND person_code=$2',[selected.id,code])).rows[0]??null;
  const days=(await this.db.query('SELECT ref_date::text AS date,kind,sys_p1,sys_p2,sys_total,rep_p1,rep_p2,rep_total,dt,acc FROM staff_report_compare_day WHERE analysis_id=$1 AND person_code=$2 ORDER BY ref_date',[selected.id,code])).rows;
  return {periods,selected,person,days};
 }
}
@Controller('reports')
export class DailyReportsController {
 constructor(@Inject(AuthService)private readonly auth:AuthService,@Inject(DailyReportsService)private readonly service:DailyReportsService,@Inject(ReportComparisonService)private readonly comparison:ReportComparisonService){}
 @Get('mine')async mine(@Req()req:Request,@Query()query:unknown){
  const {user}=await this.auth.require(req);if(!user.personCode)throw new BusinessError('PERSON_NOT_FOUND','账号未绑定员工编号',400);
  const input=parse(z.object({month:z.string().regex(/^\d{4}-\d{2}$/).default(jstToday().slice(0,7))}).strict(),query);
  let period;try{period=monthRange(input.month);}catch{throw new BusinessError('VALIDATION','月份无效',400);}
  return {...await this.service.list(period.start,period.end,user.personCode,1,31),today:jstToday(),canViewTeam:(await this.service.teamPeople(user.personCode)).length>0};
 }
 @Get('team')async team(@Req()req:Request,@Query()query:unknown){
  const {user}=await this.auth.require(req);if(!user.personCode)throw new BusinessError('PERSON_NOT_FOUND','账号未绑定员工编号',400);
  const input=parse(z.object({month:z.string().regex(/^\d{4}-\d{2}$/).default(jstToday().slice(0,7)),personCode:z.string().max(32).default('')}).strict(),query);
  const people=await this.service.teamPeople(user.personCode);if(!people.length)throw new BusinessError('FORBIDDEN','仅当前团队队长可查看本队日报',403);
  const selected=input.personCode||people[0].code;if(!people.some(person=>person.code===selected))throw new BusinessError('FORBIDDEN','不能查看其他团队的日报',403);
  let period;try{period=monthRange(input.month);}catch{throw new BusinessError('VALIDATION','月份无效',400);}
  return {...await this.service.list(period.start,period.end,selected,1,31),people,selected};
 }
 @Post('mine')async submit(@Req()req:Request,@Body()body:unknown){
  const {user}=await this.auth.require(req,{write:true});if(!user.personCode)throw new BusinessError('PERSON_NOT_FOUND','账号未绑定员工编号',400);
  const input=parse(counts.extend({mode:z.enum(['create','update'])}).strict(),body);return this.service.save(user.personCode,jstToday(),user.id,input,input.mode);
 }
 @Get('feedback')async feedback(@Req()req:Request,@Query()query:unknown){
  const {user}=await this.auth.require(req);if(!user.personCode)throw new BusinessError('PERSON_NOT_FOUND','账号未绑定员工编号',400);
  const input=parse(z.object({analysisId:z.coerce.number().int().positive().optional()}).strict(),query);return this.service.feedback(user.personCode,input.analysisId);
 }
 @Get('people')async people(@Req()req:Request){await this.auth.require(req,{admin:true});return {items:(await this.service.people()).rows};}
 @Get()async list(@Req()req:Request,@Query()query:unknown){await this.auth.require(req,{admin:true});const input=parse(range,query);return this.service.list(input.start,input.end,input.personCode,input.page);}
 @Post('save')async save(@Req()req:Request,@Body()body:unknown){const {user}=await this.auth.require(req,{admin:true,write:true});const input=parse(counts.extend({personCode:z.string().min(1).max(32),date}).strict(),body);return this.service.save(input.personCode,input.date,user.id,input,'admin');}
 @Post('delete')async remove(@Req()req:Request,@Body()body:unknown){await this.auth.require(req,{admin:true,write:true});const input=parse(z.object({personCode:z.string().min(1).max(32),date}).strict(),body);return this.service.remove(input.personCode,input.date);}
 @Get('compare')async compare(@Req()req:Request,@Query()query:unknown){await this.auth.require(req,{admin:true});const input=parse(range,query);return this.comparison.compare(input.start,input.end,input.personCode);}
}
