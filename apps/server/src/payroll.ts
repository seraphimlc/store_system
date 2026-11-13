import {Param,Post,Body} from '@nestjs/common';
import {assertMonthsOpen,requireAdmin} from './financial-control';
import {SettlementSyncService} from './settlement-sync';
import {Controller,Get,Inject,Injectable,Query,Req} from '@nestjs/common';
import type {Request} from 'express';import {z} from 'zod';
import {Database} from './database';import {AuthService} from './auth';import {BusinessError} from './errors';
import {monthRange,jstToday} from './business-dates';
import {bonusConfig,payPlan,previousMonth,validMonth} from '../../../packages/domain/src/payroll';
interface PeriodRow {person_code:string;name:string;half1_points:number;half2_points:number;half1_amount:number;half2_amount:number;half1_bonus:number;half2_bonus:number;settle_points:number;settle_amount:number;diff_points:number;diff_amount:number;adjust_points:number;adjust_amount:number;half1_records:number;half1_p1:number;half1_p2:number;half2_records:number;half2_p1:number;half2_p2:number;prev_adjust_points:number;prev_adjust_amount:number;[key:string]:unknown}
interface CarryRow {code:string;points:number;amount:number;remaining:number;recovered:number}
function parse<T>(schema:z.ZodType<T>,input:unknown){const result=schema.safeParse(input);if(!result.success)throw new BusinessError('VALIDATION','结算月份格式无效',400);return result.data;}
const querySchema=z.object({month:z.string().refine(validMonth)}).strict();
@Injectable()
export class PayrollService{
 constructor(@Inject(Database)private readonly db:Database){}
 async months(onlyCode?:string){return (await this.db.query<{month:string}>('SELECT month FROM month_perf_records WHERE ($1::text IS NULL OR person_code=$1) UNION SELECT month FROM payroll_period_rows WHERE ($1::text IS NULL OR person_code=$1) ORDER BY month',[onlyCode??null])).rows.map(row=>row.month);}
 async configuration(month:string,client:Pick<Database,'query'>=this.db){
  const row=(await client.query<{per_point:number;bonus_group:number;bonus_amount:number;staff_visible_from:string}>('SELECT per_point,bonus_group,bonus_amount,staff_visible_from FROM sys_configs ORDER BY id DESC LIMIT 1')).rows[0];
  const recorded=(await client.query<{per_point:number}>('SELECT per_point FROM month_perf_records WHERE month=$1 ORDER BY id LIMIT 1',[month])).rows[0];
  const fallback={group:Number(process.env.BONUS_GROUP)||68,amount:Number(process.env.BONUS_AMOUNT)||3000,perPoint:recorded?.per_point||250,groupSchedule:process.env.BONUS_GROUP_SCHEDULE,amountSchedule:process.env.BONUS_AMOUNT_SCHEDULE};
  return {bonus:bonusConfig(month,fallback,row?{group:row.bonus_group,amount:row.bonus_amount,perPoint:row.per_point}:undefined),staffVisibleFrom:row?.staff_visible_from||process.env.STAFF_VISIBLE_FROM||'2026-10'};
 }
 async carry(month:string,client:Pick<Database,'query'>=this.db){
  if(!validMonth(month))throw new BusinessError('VALIDATION','结算月份格式无效',400);
  const rows=(await client.query<{code:string;points:number;amount:string;remaining:string;recovered:string}>(`WITH remaining AS (
    SELECT person_code,sum(coalesce(remaining,0)) AS amount FROM payroll_adjusts WHERE source_month<$1 AND status='in_progress' GROUP BY person_code
   ),recovered AS (
    SELECT person_code,sum(coalesce(amount,0)) AS amount FROM payroll_settlement_links WHERE month=$1 GROUP BY person_code
   ),points AS (SELECT person_code,coalesce(diff_points,0)-coalesce(adjust_points,0) AS points FROM payroll_period_rows WHERE month=$2),codes AS (
    SELECT person_code FROM remaining UNION SELECT person_code FROM recovered
   ) SELECT c.person_code AS code,coalesce(p.points,0)::int AS points,(coalesce(r.amount,0)+coalesce(s.amount,0))::text AS amount,coalesce(r.amount,0)::text AS remaining,coalesce(s.amount,0)::text AS recovered
   FROM codes c LEFT JOIN remaining r USING(person_code) LEFT JOIN recovered s USING(person_code) LEFT JOIN points p USING(person_code) WHERE coalesce(r.amount,0)+coalesce(s.amount,0)<>0 ORDER BY c.person_code`,[month,previousMonth(month)])).rows;
  return rows.map(row=>({...row,amount:Number(row.amount),remaining:Number(row.remaining),recovered:Number(row.recovered)})) as CarryRow[];
 }
 async read(month:string,onlyCode?:string,client:Pick<Database,'query'>=this.db){
  const configuration=await this.configuration(month,client);const carry=await this.carry(month,client);const carryMap=new Map(carry.map(row=>[row.code,row]));
  const period=(await client.query<PeriodRow>(`SELECT r.*,r.updated_at::text AS version,coalesce(p.display_name,r.person_code) AS name FROM payroll_period_rows r LEFT JOIN persons p ON p.code=r.person_code WHERE r.month=$1 AND ($2::text IS NULL OR r.person_code=$2) ORDER BY r.person_code`,[month,onlyCode??null])).rows;
  const payments=(await client.query<{id:number;person_code:string;seq:number;amount:number;points:number;bonus:number;adjust_applied:number;paid_at:Date;adjust_leftover:number|null}>(`SELECT id,person_code,seq,amount,points,bonus,adjust_applied,paid_at,adjust_leftover FROM payroll_payments WHERE month=$1 AND ($2::text IS NULL OR person_code=$2) ORDER BY person_code,seq,id`,[month,onlyCode??null])).rows;
  const registered=new Map<string,number>();const byPerson=new Map<string,typeof payments>();
  for(const payment of payments){if(payment.seq===1)registered.set(payment.person_code,payment.adjust_applied||0);const list=byPerson.get(payment.person_code)??[];list.push(payment);byPerson.set(payment.person_code,list);}
  const rows=period.map(row=>{
   const adjustment=carryMap.get(row.person_code)??{points:0,amount:0,remaining:0,recovered:0};
   const plan=payPlan(row.half1_amount||0,row.half2_amount||0,adjustment.amount,registered.get(row.person_code));
   return {code:row.person_code,name:row.name,version:row.version as string|null,adjustedAmount:Number(row.adjust_amount||0),half1:{points:row.half1_points,amount:row.half1_amount,bonus:row.half1_bonus,records:row.half1_records,p1:row.half1_p1,p2:row.half1_p2,adjustment:plan.h1[0],payable:plan.h1[1]},half2:{points:row.half2_points,amount:row.half2_amount,bonus:row.half2_bonus,records:row.half2_records,p1:row.half2_p1,p2:row.half2_p2,adjustment:plan.h2[0],payable:plan.h2[1]},settlePoints:row.settle_points,settleAmount:row.settle_amount,diffPoints:row.diff_points,diffAmount:row.diff_amount,carryPoints:adjustment.points,carryAmount:adjustment.amount,unrecovered:adjustment.remaining,recoveredThisMonth:adjustment.recovered,payments:byPerson.get(row.person_code)??[]};
  });
  const performance=(await client.query(`SELECT m.person_code AS code,coalesce(p.display_name,m.person_code) AS name,m.records,m.p1,m.p2,m.points,m.salary,m.per_point AS "perPoint",m.rate37,m.pass37 FROM month_perf_records m LEFT JOIN persons p ON p.code=m.person_code WHERE m.month=$1 AND ($2::text IS NULL OR m.person_code=$2) ORDER BY m.person_code`,[month,onlyCode??null])).rows;
  const cached=(await client.query<{payload:string|null}>("SELECT payload FROM dash_metrics WHERE month=$1 AND metric='dup_map' AND person IS NULL ORDER BY id DESC LIMIT 1",[month])).rows[0]?.payload;
  let duplicates:Record<string,number>|null=null;try{if(cached){const value=typeof cached==='string'?JSON.parse(cached):cached;if(value&&typeof value==='object'&&!Array.isArray(value))duplicates=value;}}catch{}
  return {month,configuration,rows,performance,duplicates,carry:onlyCode?carry.filter(row=>row.code===onlyCode):carry,summary:{people:rows.length,half1Payable:rows.reduce((sum,row)=>sum+row.half1.payable,0),half2Payable:rows.reduce((sum,row)=>sum+row.half2.payable,0),registeredAmount:payments.reduce((sum,row)=>sum+row.amount,0),carryAmount:(onlyCode?carry.filter(row=>row.code===onlyCode):carry).reduce((sum,row)=>sum+row.amount,0)}};
 }
 async adjust(month:string,code:string,input:{half1Amount:number;half2Amount:number;adjustDelta:number;version:string|null},actor:number){return this.db.transaction(async client=>{
  await requireAdmin(client,actor);await client.query("SELECT pg_advisory_xact_lock(hashtext('payroll-financial-write'),0)");await assertMonthsOpen(client,[month]);
  const row=(await client.query<{version:string|null}>("SELECT updated_at::text AS version FROM payroll_period_rows WHERE month=$1 AND person_code=$2 FOR UPDATE",[month,code])).rows[0];
  if(!row)throw new BusinessError('NOT_FOUND','该月此员工无对账行',404);
  if(row.version!==input.version)throw new BusinessError('PAYROLL_CHANGED','薪资找平数据已变化，请重新读取后保存',409);
  await client.query('UPDATE payroll_period_rows SET half1_amount=$1,half2_amount=$2,adjust_amount=coalesce(adjust_amount,0)+$3,updated_by=$4,updated_at=now() WHERE month=$5 AND person_code=$6',[input.half1Amount,input.half2Amount,input.adjustDelta,actor,month,code]);return {ok:true};
 });}
 async generate(month:string,actor:number,sync:SettlementSyncService){return this.db.transaction(async client=>{
  await requireAdmin(client,actor);await client.query("SELECT pg_advisory_xact_lock(hashtext('settlement-write'),0)");await client.query("SELECT pg_advisory_xact_lock(hashtext('payroll-financial-write'),0)");await assertMonthsOpen(client,[month]);
  const {start,end}=monthRange(month);const stats=(await client.query<{person_code:string;records:number;p1:number;p2:number;points:number;h1:number;h2:number;h1_records:number;h1_p1:number;h1_p2:number;h2_records:number;h2_p1:number;h2_p2:number}>(`SELECT person_code,sum(records)::int AS records,sum(p1)::int AS p1,sum(p2)::int AS p2,sum(points)::int AS points,
   coalesce(sum(points) FILTER(WHERE extract(day FROM ref_date)<=15),0)::int AS h1,coalesce(sum(points) FILTER(WHERE extract(day FROM ref_date)>15),0)::int AS h2,
   coalesce(sum(records) FILTER(WHERE extract(day FROM ref_date)<=15),0)::int AS h1_records,coalesce(sum(p1) FILTER(WHERE extract(day FROM ref_date)<=15),0)::int AS h1_p1,coalesce(sum(p2) FILTER(WHERE extract(day FROM ref_date)<=15),0)::int AS h1_p2,
   coalesce(sum(records) FILTER(WHERE extract(day FROM ref_date)>15),0)::int AS h2_records,coalesce(sum(p1) FILTER(WHERE extract(day FROM ref_date)>15),0)::int AS h2_p1,coalesce(sum(p2) FILTER(WHERE extract(day FROM ref_date)>15),0)::int AS h2_p2 FROM person_daily_stats WHERE ref_date BETWEEN $1 AND $2 GROUP BY person_code ORDER BY person_code`,[start,end])).rows;
  if(!stats.length)throw new BusinessError('MONTH_SOURCE_MISSING','该月没有已入表的每日绩效，不能生成以免清空历史快照',409);
  const config=await this.configuration(month);await sync.syncPeriods(client,month,stats,config.bonus);await sync.syncDashboard(client,month);return {ok:true,rows:stats.length};
 });}

}
@Controller('payroll')
export class PayrollController{
 constructor(@Inject(AuthService)private readonly auth:AuthService,@Inject(PayrollService)private readonly service:PayrollService,@Inject(SettlementSyncService)private readonly sync:SettlementSyncService){}
 @Get('months')async months(@Req()req:Request){await this.auth.require(req,{admin:true});return {items:await this.service.months()};}
 @Get()async read(@Req()req:Request,@Query()query:unknown){await this.auth.require(req,{admin:true});const input=parse(querySchema,query);return this.service.read(input.month);}
 @Post('settlement/:month/generate')async generate(@Req()req:Request,@Param('month')value:string,@Body()body:unknown){const {user}=await this.auth.require(req,{admin:true,write:true});const input=parse(z.object({confirm:z.literal('GENERATE_PAYROLL_PERIODS')}).strict(),body);return this.service.generate(parse(querySchema,{month:value}).month,user.id,this.sync);}
 @Post('settlement/:month/:code')async adjust(@Req()req:Request,@Param('month')value:string,@Param('code')code:string,@Body()body:unknown){const {user}=await this.auth.require(req,{admin:true,write:true});const input=parse(z.object({half1Amount:z.number().int().min(0).max(1000000000),half2Amount:z.number().int().min(0).max(1000000000),adjustDelta:z.number().int().min(-1000000000).max(1000000000),version:z.string().nullable()}).strict(),body);return this.service.adjust(parse(querySchema,{month:value}).month,parse(z.string().min(1).max(64),code),input,user.id);}
 @Get('mine/months')async myMonths(@Req()req:Request){const {user}=await this.auth.require(req);if(!user.personCode)throw new BusinessError('PERSON_NOT_FOUND','账号未绑定员工编号',400);const config=await this.service.configuration(jstToday().slice(0,7));return {items:(await this.service.months(user.personCode)).filter(month=>month>=config.staffVisibleFrom)};}
 @Get('mine')async mine(@Req()req:Request,@Query()query:unknown){const {user}=await this.auth.require(req);const input=parse(querySchema,query);if(!user.personCode)throw new BusinessError('PERSON_NOT_FOUND','账号未绑定员工编号',400);const configuration=await this.service.configuration(input.month);if(input.month<configuration.staffVisibleFrom)throw new BusinessError('MONTH_NOT_VISIBLE','该月份尚未开放员工查看',403);return this.service.read(input.month,user.personCode);}
}
