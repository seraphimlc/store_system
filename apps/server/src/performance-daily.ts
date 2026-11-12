import {Controller,Get,Inject,Injectable,Query,Req} from '@nestjs/common';import type {Request} from 'express';import {z} from 'zod';
import {AuthService} from './auth';import {Database} from './database';import {PayrollService} from './payroll';import {BusinessError} from './errors';import {monthRange,validDate} from './business-dates';import {validMonth} from '../../../packages/domain/src/payroll';
const base=z.object({month:z.string().refine(validMonth),date:z.union([z.literal('all'),z.string().refine(validDate)]).optional(),page:z.coerce.number().int().min(1).max(10000).default(1)}).strict();
function parse<T>(schema:z.ZodType<T>,input:unknown){const value=schema.safeParse(input);if(!value.success)throw new BusinessError('VALIDATION','绩效日期、月份或分页参数无效',400);return value.data;}
@Injectable()
export class PerformanceDailyService{
 constructor(@Inject(Database)private readonly db:Database){}
 async read(month:string,code:string|null,date:string|undefined,page:number,personal:boolean){
  const {start,end}=monthRange(month);if(date&&date!=='all'&&(date<start||date>end))throw new BusinessError('VALIDATION','日期必须在所选结算月内',400);
  return this.db.transaction(async client=>{
   await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');
   // Preserve different legacy sources: admin treats every formal non-2 point row as a 1-point visit;
   // staff uses the materialized p1/p2/points, which only recognizes explicit 1/2-point rows.
   const source=personal?`SELECT person_code AS code,ref_date AS date,records,p1,p2,points FROM person_daily_stats WHERE ref_date BETWEEN $1::date AND $2::date AND person_code=$3`
    :`SELECT person_code AS code,japan_date AS date,count(*)::int AS records,(count(*)-count(*) FILTER(WHERE points=2))::int AS p1,count(*) FILTER(WHERE points=2)::int AS p2,(count(*)+count(*) FILTER(WHERE points=2))::int AS points FROM formal_records WHERE japan_date BETWEEN $1::date AND $2::date AND ($3::text IS NULL OR person_code=$3) GROUP BY person_code,japan_date`;
   const sql=`WITH daily AS(${source}),dates AS(SELECT DISTINCT date FROM daily),selected AS(SELECT CASE WHEN $4::text='all' THEN NULL WHEN $4::text IS NOT NULL THEN $4::date WHEN $6::boolean THEN NULL ELSE (SELECT max(date) FROM dates) END AS day),matched AS(
    SELECT d.code,coalesce(p.display_name,d.code) AS name,d.date::text AS date,d.records,d.p1,d.p2,d.points FROM daily d LEFT JOIN persons p ON p.code=d.code WHERE (SELECT day FROM selected) IS NULL OR d.date=(SELECT day FROM selected)
   ),paged AS(SELECT * FROM matched ORDER BY date,code NULLS LAST LIMIT 30 OFFSET $5),month_totals AS(SELECT coalesce(sum(records),0)::int AS records,coalesce(sum(p1),0)::int AS p1,coalesce(sum(p2),0)::int AS p2,coalesce(sum(points),0)::int AS points FROM daily),totals AS(SELECT coalesce(sum(records),0)::int AS records,coalesce(sum(p1),0)::int AS p1,coalesce(sum(p2),0)::int AS p2,coalesce(sum(points),0)::int AS points FROM matched)
   SELECT (SELECT count(*)::int FROM matched) AS total,coalesce((SELECT json_agg(paged) FROM paged),'[]'::json) AS items,
    (SELECT row_to_json(totals) FROM totals) AS summary,(SELECT row_to_json(month_totals) FROM month_totals) AS "monthSummary",
    coalesce((SELECT json_agg(date::text ORDER BY date DESC) FROM dates),'[]'::json) AS dates,(SELECT day::text FROM selected) AS "selectedDate"`;
   const result=(await client.query(sql,[start,end,code,date??null,(page-1)*30,personal])).rows[0];
   const people=personal?[]:(await client.query<{code:string;name:string}>(`SELECT DISTINCT d.code,coalesce(p.display_name,d.code) AS name FROM (${source}) d LEFT JOIN persons p ON p.code=d.code WHERE d.code IS NOT NULL ORDER BY name,code`,[start,end,null])).rows;
   return {...result,month,page,pageSize:30,people,source:personal?'person_daily_stats':'formal_records'};
  });
 }
}
@Controller('payroll')
export class PerformanceDailyController{
 constructor(@Inject(AuthService)private readonly auth:AuthService,@Inject(PerformanceDailyService)private readonly service:PerformanceDailyService,@Inject(PayrollService)private readonly payroll:PayrollService){}
 @Get('daily')async admin(@Req()req:Request,@Query()query:unknown){await this.auth.require(req,{admin:true});const input=parse(base.extend({personCode:z.string().max(32).default('')}),query);return this.service.read(input.month,input.personCode||null,input.date,input.page,false);}
 @Get('mine/daily')async mine(@Req()req:Request,@Query()query:unknown){const {user}=await this.auth.require(req);const input=parse(base,query);if(!user.personCode)throw new BusinessError('PERSON_NOT_FOUND','账号未绑定员工编号',400);const configuration=await this.payroll.configuration(input.month);if(input.month<configuration.staffVisibleFrom)throw new BusinessError('MONTH_NOT_VISIBLE','该月份尚未开放员工查看',403);return this.service.read(input.month,user.personCode,input.date??'all',input.page,true);}
}
