import type { PoolClient } from 'pg';
import {Body,Controller,Get,Inject,Injectable,Param,Post,Req} from '@nestjs/common';import type {Request} from 'express';import {z} from 'zod';import {Database} from './database';import {AuthService} from './auth';import {BusinessError} from './errors';import {PayrollService} from './payroll';import {SettlementSyncService} from './settlement-sync';import {assertMonthsOpen,requireAdmin} from './financial-control';import {validMonth} from '../../../packages/domain/src/payroll';import {monthRange,jstToday} from './business-dates';
const month=z.string().refine(validMonth);const schema=z.object({perPoint:z.number().int().min(1).max(10000),bonusGroup:z.number().int().min(1).max(1000),bonusAmount:z.number().int().min(0).max(1000000),staffVisibleFrom:z.union([z.literal(''),month]),expected:z.object({id:z.number().int().positive().nullable(),version:z.string().nullable()}).strict()}).strict();
function parse<T>(schema:z.ZodType<T>,input:unknown){const value=schema.safeParse(input);if(!value.success)throw new BusinessError('VALIDATION','配置值或月份格式无效',400);return value.data;}
@Injectable()
export class ConfigurationService{
 constructor(@Inject(Database)private readonly db:Database,@Inject(PayrollService)private readonly payroll:PayrollService,@Inject(SettlementSyncService)private readonly sync:SettlementSyncService){}
 async read(){const row=(await this.db.query('SELECT id,per_point AS "perPoint",bonus_group AS "bonusGroup",bonus_amount AS "bonusAmount",staff_visible_from AS "staffVisibleFrom",updated_at::text AS version,updated_by AS "updatedBy" FROM sys_configs ORDER BY id DESC LIMIT 1')).rows[0]??null;const config=await this.payroll.configuration(jstToday().slice(0,7));return {row,effective:{perPoint:config.bonus.perPoint,bonusGroup:config.bonus.group,bonusAmount:config.bonus.amount,staffVisibleFrom:config.staffVisibleFrom},sealed:(await this.db.query('SELECT month,note,created_by AS "createdBy",created_at AS "createdAt" FROM sealed_months ORDER BY month DESC')).rows};}
 async save(input:z.infer<typeof schema>,actor:number){return this.db.transaction(client=>this.saveInTransaction(client,input,actor));}
 async saveInTransaction(client:PoolClient,input:z.infer<typeof schema>,actor:number){
  input=parse(schema,input);
  await requireAdmin(client,actor);await client.query("SELECT pg_advisory_xact_lock(hashtext('settlement-write'),0)");await client.query("SELECT pg_advisory_xact_lock(hashtext('payroll-financial-write'),0)");
  const current=(await client.query<{id:number;version:string}>('SELECT id,updated_at::text AS version FROM sys_configs ORDER BY id DESC LIMIT 1 FOR UPDATE')).rows[0];
  if((current?.id??null)!==input.expected.id||(current?.version??null)!==input.expected.version)throw new BusinessError('CONFIG_CHANGED','配置已被更新，请刷新后再保存',409);
  if(current)await client.query('UPDATE sys_configs SET per_point=$1,bonus_group=$2,bonus_amount=$3,staff_visible_from=$4,updated_by=$5,updated_at=now() WHERE id=$6',[input.perPoint,input.bonusGroup,input.bonusAmount,input.staffVisibleFrom,actor,current.id]);
  else await client.query('INSERT INTO sys_configs(config_month,per_point,bonus_group,bonus_amount,staff_visible_from,updated_by,updated_at) VALUES(\'\',$1,$2,$3,$4,$5,now())',[input.perPoint,input.bonusGroup,input.bonusAmount,input.staffVisibleFrom,actor]);
  return {ok:true,recalculated:false};
 }
 async seal(month:string,note:string,actor:number,closed:boolean){return this.db.transaction(async client=>{
  await requireAdmin(client,actor);await client.query("SELECT pg_advisory_xact_lock(hashtext('settlement-write'),0)");await client.query("SELECT pg_advisory_xact_lock(hashtext('payroll-financial-write'),0)");
  if(closed){const pending=(await client.query(`SELECT a.id FROM appeals a JOIN raw_records r ON r.id=a.raw_record_id WHERE a.status='pending' AND left(r.modified_raw,7)=$1 LIMIT 1`,[month])).rows[0];if(pending)throw new BusinessError('APPEALS_PENDING','该月仍有未决申诉，不能封账',409);await client.query('INSERT INTO sealed_months(month,note,created_by,created_at) VALUES($1,$2,$3,now()) ON CONFLICT(month) DO NOTHING',[month,note.trim(),actor]);}
  else await client.query('DELETE FROM sealed_months WHERE month=$1',[month]);return {month,sealed:closed};
 });}
 async perPoint(month:string,price:number,actor:number){return this.db.transaction(async client=>{
  await requireAdmin(client,actor);await client.query("SELECT pg_advisory_xact_lock(hashtext('settlement-write'),0)");await client.query("SELECT pg_advisory_xact_lock(hashtext('payroll-financial-write'),0)");await assertMonthsOpen(client,[month]);
  const bounds=monthRange(month);if(!(await client.query('SELECT id FROM formal_records WHERE japan_date BETWEEN $1 AND $2 LIMIT 1',[bounds.start,bounds.end])).rowCount)throw new BusinessError('MONTH_SOURCE_MISSING','该月没有正式明细，不能重新计价以免清空历史快照',409);
  await client.query('UPDATE month_perf_records SET per_point=$1 WHERE month=$2',[price,month]);const result=await this.sync.sync(client,month,true,price);return {...result,perPoint:price};
 });}
}
@Controller('configuration')
export class ConfigurationController{
 constructor(@Inject(AuthService)private readonly auth:AuthService,@Inject(ConfigurationService)private readonly service:ConfigurationService){}
 @Get()async read(@Req()req:Request){await this.auth.require(req,{admin:true});return this.service.read();}
 @Post()async save(@Req()req:Request,@Body()body:unknown){const {user}=await this.auth.require(req,{admin:true,write:true});return this.service.save(parse(schema,body),user.id);}
 @Post('months/:month/seal')async seal(@Req()req:Request,@Param('month')value:string,@Body()body:unknown){const {user}=await this.auth.require(req,{admin:true,write:true});const input=parse(z.object({note:z.string().max(500),confirm:z.literal('SEAL_MONTH')}).strict(),body);return this.service.seal(parse(month,value),input.note,user.id,true);}
 @Post('months/:month/unseal')async unseal(@Req()req:Request,@Param('month')value:string,@Body()body:unknown){const {user}=await this.auth.require(req,{admin:true,write:true});parse(z.object({confirm:z.literal('UNSEAL_MONTH')}).strict(),body);return this.service.seal(parse(month,value),'',user.id,false);}
 @Post('months/:month/per-point')async price(@Req()req:Request,@Param('month')value:string,@Body()body:unknown){const {user}=await this.auth.require(req,{admin:true,write:true});const input=parse(z.object({perPoint:z.number().int().min(1).max(10000),confirm:z.literal('RECALCULATE_MONTH_PRICE')}).strict(),body);return this.service.perPoint(parse(month,value),input.perPoint,user.id);}
}
