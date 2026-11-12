import {Body,Controller,Get,Inject,Injectable,Post,Query,Req} from '@nestjs/common';
import type {Request} from 'express';import type {PoolClient,QueryResultRow} from 'pg';import {z} from 'zod';import {createHash} from 'node:crypto';
import {Database} from './database';import {AuthService} from './auth';import {BusinessError} from './errors';
import {allocateFifo,payPlan,previousMonth,validMonth} from '../../../packages/domain/src/payroll';
interface Period extends QueryResultRow {id:number;person_code:string;name:string;half1_points:number;half2_points:number;half1_amount:number;half2_amount:number;half1_bonus:number;half2_bonus:number;diff_points:number;adjust_points:number}
interface Debt extends QueryResultRow {id:number;person_code:string;source_month:string;source_task_id:number|null;source_row_id:number|null;adjust_amount:number;settled_amount:number;remaining:number;status:string}
interface Payment extends QueryResultRow {id:number;person_code:string;seq:number;amount:number;points:number;bonus:number;adjust_applied:number;adjust_leftover:number|null}
interface Snapshot {periods:Period[];previous:Period[];debts:Debt[];payments:Payment[];links:{person_code:string;amount:number}[]}
const range=z.object({month:z.string().refine(validMonth),half:z.coerce.number().int().min(1).max(2)}).strict();
function parse<T>(schema:z.ZodType<T>,value:unknown){const parsed=schema.safeParse(value);if(!parsed.success)throw new BusinessError('VALIDATION','请检查发薪月份、期次和确认信息',400);return parsed.data;}
function sum(values:number[]){const total=values.reduce((a,b)=>a+BigInt(b),0n);const result=Number(total);if(!Number.isSafeInteger(result))throw new BusinessError('MONEY_RANGE','金额超过可处理范围',409);return result;}
@Injectable()
export class PayrollPaymentsService{
 constructor(@Inject(Database)private readonly db:Database){}
 private async snapshot(client:PoolClient,month:string,lock=false):Promise<Snapshot>{
  const periods=(await client.query<Period>(`SELECT r.id,r.person_code,coalesce(p.display_name,r.person_code) AS name,r.half1_points,r.half2_points,r.half1_amount,r.half2_amount,r.half1_bonus,r.half2_bonus,r.diff_points,r.adjust_points FROM payroll_period_rows r LEFT JOIN persons p ON p.code=r.person_code WHERE r.month=$1 ORDER BY r.person_code${lock?' FOR UPDATE OF r':''}`,[month])).rows;
  if(lock)for(const row of periods)await client.query("SELECT pg_advisory_xact_lock(hashtext('payroll-person'),hashtext($1))",[row.person_code]);
  const codes=periods.map(row=>row.person_code);
  const previous=(await client.query<Period>('SELECT id,person_code,diff_points,adjust_points FROM payroll_period_rows WHERE month=$1 AND person_code=ANY($2::text[]) ORDER BY person_code',[previousMonth(month),codes])).rows;
  const debts=(await client.query<Debt>(`SELECT id,person_code,source_month,source_task_id,source_row_id,adjust_amount,settled_amount,remaining,status FROM payroll_adjusts WHERE person_code=ANY($1::text[]) AND status='in_progress' ORDER BY source_month,id${lock?' FOR UPDATE':''}`,[codes])).rows;
  const payments=(await client.query<Payment>('SELECT id,person_code,seq,amount,points,bonus,adjust_applied,adjust_leftover FROM payroll_payments WHERE month=$1 ORDER BY person_code,seq,id',[month])).rows;
  const links=(await client.query<{person_code:string;amount:number}>('SELECT person_code,amount FROM payroll_settlement_links WHERE month=$1 ORDER BY person_code,id',[month])).rows;
  return {periods,previous,debts,payments,links};
 }
 private fingerprint(month:string,half:number,state:Snapshot){
  const periods=state.periods.map(({name,...row})=>row);
  return createHash('sha256').update(JSON.stringify({month,half,...state,periods})).digest('hex');
 }
 private previewRows(month:string,half:number,state:Snapshot){
  return state.periods.map(row=>{
   const carry=sum([...state.debts.filter(debt=>debt.person_code===row.person_code&&debt.source_month<month).map(debt=>debt.remaining),...state.links.filter(link=>link.person_code===row.person_code).map(link=>link.amount)]);
   const registered=state.payments.find(payment=>payment.person_code===row.person_code&&payment.seq===1);
   const plan=payPlan(row.half1_amount||0,row.half2_amount||0,carry,registered?.adjust_applied);
   const existing=state.payments.find(payment=>payment.person_code===row.person_code&&payment.seq===half);
   const p=half===1?'half1':'half2';
   return {personCode:row.person_code,name:row.name,points:row[`${p}_points`],grossAmount:row[`${p}_amount`],bonus:row[`${p}_bonus`],adjustment:half===1?plan.h1[0]:plan.h2[0],payable:half===1?plan.h1[1]:plan.h2[1],registered:!!existing,paymentId:existing?.id??null,registeredAmount:existing?.amount??null,registeredAdjustment:existing?.adjust_applied??null};
  });
 }
 async preview(month:string,half:number){return this.db.transaction(async client=>{
  await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');const state=await this.snapshot(client,month);
  return {month,half,token:this.fingerprint(month,half,state),rows:this.previewRows(month,half,state),registrationSemantics:'legacy-gross-amount-with-separate-adjustment'};
 });}
 private async record(client:PoolClient,month:string,half:number,row:Period,state:Snapshot,actor:number){
  const existing=state.payments.find(payment=>payment.person_code===row.person_code&&payment.seq===half);if(existing)return {id:existing.id,created:false};
  const key=half===1?'half1':'half2';const gross=row[`${key}_amount`]||0;
  if(gross<0)throw new BusinessError('NEGATIVE_GROSS','应发快照金额不能为负，请先核对',409);
  const prior=state.debts.filter(debt=>debt.person_code===row.person_code&&debt.source_month<month);const carry=sum(prior.map(debt=>debt.remaining));
  const adjustment=carry<0?Math.max(carry,-gross):0;
  const sourceMonth=carry<0?(prior[0]?.source_month??previousMonth(month)):null;
  const previous=state.previous.find(entry=>entry.person_code===row.person_code);
  let task=carry<0?(prior[0]?.source_task_id??null):null;
  if(carry<0&&task===null){task=(await client.query<{id:number}>(`SELECT id FROM recon_tasks WHERE kind='monthly_v3' AND status='done' AND params->>'month'=$1 AND (params->'replaced_by' IS NULL OR (params->'replaced_by')::text IN ('null','false','0','""')) ORDER BY id DESC LIMIT 1`,[sourceMonth])).rows[0]?.id??null;}
  const inserted=(await client.query<{id:number}>(`INSERT INTO payroll_payments(month,person_code,seq,points,amount,bonus,adjust_applied,note,paid_at,paid_by,adjust_source_type,adjust_source_month,adjust_source_row_id,adjust_source_task_id,adjust_leftover)
   VALUES($1,$2,$3,$4,$5,$6,$7,NULL,now(),$8,$9,$10,$11,$12,$13) RETURNING id`,[month,row.person_code,half,row[`${key}_points`]||0,gross,row[`${key}_bonus`]||0,adjustment,actor,carry<0?'carry':null,sourceMonth,carry<0?(previous?.id??null):null,task,carry<0?carry-adjustment:null])).rows[0];
  if(adjustment){
   const allocation=allocateFifo(state.debts.filter(debt=>debt.person_code===row.person_code).map(debt=>({id:debt.id,sourceMonth:debt.source_month,remaining:debt.remaining,settled:debt.settled_amount})),adjustment);
   if(allocation.unallocated)throw new BusinessError('SETTLEMENT_INCOMPLETE','核销余额不足，登记已回滚，请核对找平账表',409);
   for(const part of allocation.allocations){
    const debt=state.debts.find(entry=>entry.id===part.adjustId)!;
    const changed=await client.query(`UPDATE payroll_adjusts SET settled_amount=$1,remaining=$2,status=$3,settled_at=CASE WHEN $2::int=0 THEN now() ELSE NULL END,updated_at=now() WHERE id=$4 AND remaining=$5 AND settled_amount=$6 RETURNING id`,[part.settled,part.remaining,part.remaining===0?'settled':'in_progress',part.adjustId,debt.remaining,debt.settled_amount]);
    if(changed.rowCount!==1)throw new BusinessError('SETTLEMENT_CONFLICT','找平余额已变化，登记已回滚',409);
    await client.query('INSERT INTO payroll_settlement_links(adjust_id,payment_id,month,person_code,amount,created_at) VALUES($1,$2,$3,$4,$5,now())',[part.adjustId,inserted.id,month,row.person_code,part.amount]);
   }
  }
  return {id:inserted.id,created:true};
 }
 async register(month:string,half:number,actor:number,expectedToken:string){
  return this.db.transaction(async client=>{
   const admin=(await client.query("SELECT id FROM users WHERE id=$1 AND role='admin' AND status IN ('active','leave') AND is_active=true FOR SHARE",[actor])).rows[0];if(!admin)throw new BusinessError('FORBIDDEN','管理员状态已变化，请重新登录',403);
   await client.query("SELECT pg_advisory_xact_lock(hashtext('payroll-financial-write'),0)");
   await client.query("SELECT pg_advisory_xact_lock(hashtext('payroll-month'),hashtext($1))",[month]);
   const state=await this.snapshot(client,month,true);
   if(!state.periods.length)throw new BusinessError('PAYROLL_NOT_GENERATED','该月尚无应发快照，请先生成',409);
   const allRegistered=state.periods.every(row=>state.payments.some(payment=>payment.person_code===row.person_code&&payment.seq===half));
   if(!allRegistered&&this.fingerprint(month,half,state)!==expectedToken)throw new BusinessError('PAYROLL_PREVIEW_CHANGED','金额或找平余额已变化，请重新预览再确认',409);
   const result=[];for(const row of state.periods)result.push(await this.record(client,month,half,row,state,actor));
   await client.query('INSERT INTO payroll_paid_marks(month,half,marked_by,marked_at) VALUES($1,$2,$3,now()) ON CONFLICT(month,half) DO NOTHING',[month,half,actor]);
   return {month,half,created:result.filter(row=>row.created).length,reused:result.filter(row=>!row.created).length,paymentIds:result.map(row=>row.id)};
  });
 }
}
@Controller('payroll/payments')
export class PayrollPaymentsController{
 constructor(@Inject(AuthService)private readonly auth:AuthService,@Inject(PayrollPaymentsService)private readonly service:PayrollPaymentsService){}
 @Get('preview')async preview(@Req()req:Request,@Query()query:unknown){await this.auth.require(req,{admin:true});const input=parse(range,query);return this.service.preview(input.month,input.half);}
 @Post('register')async register(@Req()req:Request,@Body()body:unknown){const {user}=await this.auth.require(req,{admin:true,write:true});const input=parse(range.extend({token:z.string().regex(/^[a-f0-9]{64}$/),confirm:z.literal('REGISTER_PAYROLL')}),body);return this.service.register(input.month,input.half,user.id,input.token);}
}
