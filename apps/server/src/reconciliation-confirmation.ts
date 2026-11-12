import {Body,Controller,Inject,Injectable,Param,Post,Req} from '@nestjs/common';import type {Request} from 'express';import {z} from 'zod';import {Database} from './database';import {AuthService} from './auth';import {BusinessError} from './errors';import {assertMonthsOpen,requireAdmin} from './financial-control';
@Injectable()
export class ReconciliationConfirmationService{
 constructor(@Inject(Database)private readonly db:Database){}
 async change(taskId:number,code:string,actor:number,action:'confirm'|'cancel'){return this.db.transaction(async client=>{
  await requireAdmin(client,actor);await client.query("SELECT pg_advisory_xact_lock(hashtext('settlement-write'),0)");await client.query("SELECT pg_advisory_xact_lock(hashtext('payroll-financial-write'),0)");
  const task=(await client.query<{status:string;params:{month:string;replaced_by?:number}}>('SELECT status,params FROM recon_tasks WHERE id=$1 AND kind=\'monthly_v3\' FOR UPDATE',[taskId])).rows[0];if(!task)throw new BusinessError('NOT_FOUND','对账任务不存在',404);
  await assertMonthsOpen(client,[task.params.month]);
  const previous=(await client.query<{id:number;amount:number;per_point:number;amount_adj:number;month:string}>('SELECT id,amount,per_point,amount_adj,month FROM adjust_records WHERE source_task_id=$1 AND person_code=$2 ORDER BY id LIMIT 1 FOR UPDATE',[taskId,code])).rows[0];
  if(action==='cancel'){
   if(!previous)return {ok:true,cancelled:false};
   if(previous.amount_adj)await client.query('UPDATE payroll_period_rows SET adjust_amount=adjust_amount-$1,updated_at=now(),updated_by=$2 WHERE month=$3 AND person_code=$4',[previous.amount_adj,actor,previous.month,code]);
   await client.query('DELETE FROM adjust_records WHERE source_task_id=$1 AND person_code=$2',[taskId,code]);return {ok:true,cancelled:true};
  }
  if(previous)return {ok:true,reused:true,id:previous.id,points:previous.amount,perPoint:previous.per_point};
  if(task.status!=='done')throw new BusinessError('RECON_NOT_DONE','对账任务尚未成功，不能确认',409);
  if(task.params.replaced_by)throw new BusinessError('RECON_REPLACED','该对账版本已被替代，请查看当前有效版本',409);
  const difference=(await client.query<{system_value:number;report_value:number;diff:number}>('SELECT system_value,report_value,diff FROM recon_results WHERE task_id=$1 AND submitter_code=$2 ORDER BY id LIMIT 1 FOR UPDATE',[taskId,code])).rows[0];if(!difference)throw new BusinessError('NOT_FOUND','该员工没有需要确认的差异行',404);
  const config=(await client.query<{per_point:number}>('SELECT per_point FROM sys_configs ORDER BY id DESC LIMIT 1')).rows[0];const monthly=(await client.query<{per_point:number}>('SELECT per_point FROM month_perf_records WHERE month=$1 ORDER BY id LIMIT 1',[task.params.month])).rows[0];const price=config?.per_point||monthly?.per_point||250;
  const points=(difference.report_value||0)-(difference.system_value||0);const [year,month]=task.params.month.split('-').map(Number),next=month===12?`${year+1}-01`:`${year}-${String(month+1).padStart(2,'0')}`;
  const period=(await client.query<{diff_amount:number;adjust_amount:number}>('SELECT diff_amount,adjust_amount FROM payroll_period_rows WHERE month=$1 AND person_code=$2 FOR UPDATE',[task.params.month,code])).rows[0];const delta=period?(period.diff_amount||0)-(period.adjust_amount||0):0;
  const result=(await client.query<{id:number}>(`INSERT INTO adjust_records(month,applied_to_month,person_code,amount,reason,source_task_id,source_points_diff,created_by,created_at,per_point,amount_adj) VALUES($1,$2,$3,$4,$5,$6,$7,$8,now(),$9,$10) RETURNING id`,[task.params.month,next,code,points,`对账#${taskId} ${task.params.month} 差异 ${difference.diff} 点（系统 ${difference.system_value} / 对账 ${difference.report_value}）· 当时单价 ${price}円/点`,taskId,difference.diff,actor,price,delta])).rows[0];
  if(period)await client.query('UPDATE payroll_period_rows SET adjust_amount=adjust_amount+$1,updated_at=now(),updated_by=$2 WHERE month=$3 AND person_code=$4',[delta,actor,task.params.month,code]);
  return {ok:true,reused:false,id:result.id,points,perPoint:price};
 });}
}
@Controller('reconciliation')
export class ReconciliationConfirmationController{
 constructor(@Inject(AuthService)private readonly auth:AuthService,@Inject(ReconciliationConfirmationService)private readonly service:ReconciliationConfirmationService){}
 @Post(':id/confirmation')async change(@Req()req:Request,@Param('id')id:string,@Body()body:unknown){const {user}=await this.auth.require(req,{admin:true,write:true});const task=z.coerce.number().int().positive().safeParse(id),input=z.object({personCode:z.string().min(1).max(32),action:z.enum(['confirm','cancel'])}).strict().safeParse(body);if(!task.success||!input.success)throw new BusinessError('VALIDATION','确认参数无效',400);return this.service.change(task.data,input.data.personCode,user.id,input.data.action);}
}
