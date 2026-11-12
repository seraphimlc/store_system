import {Inject,Injectable} from '@nestjs/common';import type {PoolClient} from 'pg';
import {assertMonthsOpen} from './financial-control';
import {Database} from './database';import {BusinessError} from './errors';import {monthRange} from './business-dates';import {ReportComparisonService} from './report-comparison';
import {bonusConfig,halfGross,salaryFor,previousMonth} from '../../../packages/domain/src/payroll';
interface Stats {person_code:string;records:number;p1:number;p2:number;points:number;h1:number;h2:number;h1_records:number;h1_p1:number;h1_p2:number;h2_records:number;h2_p1:number;h2_p2:number}
export async function latestReconciliation(client:PoolClient,month:string){return (await client.query<{id:number}>(`SELECT id FROM recon_tasks WHERE kind='monthly_v3' AND status='done' AND params->>'month'=$1 AND (params->'replaced_by' IS NULL OR (params->'replaced_by')::text IN ('null','false','0','""')) ORDER BY id DESC LIMIT 1`,[month])).rows[0]?.id??null;}
@Injectable()
export class SettlementSyncService{
 constructor(@Inject(Database)private readonly db:Database,@Inject(ReportComparisonService)private readonly comparison:ReportComparisonService){}
 async sync(client:PoolClient,month:string,periods=true,explicitPrice?:number){
  await client.query("SELECT pg_advisory_xact_lock(hashtext('payroll-financial-write'),0)");
  await assertMonthsOpen(client,[month]);
  const {start,end}=monthRange(month);
  const missing=(await client.query<{n:number}>('SELECT count(*)::int AS n FROM formal_records WHERE japan_date BETWEEN $1 AND $2 AND (person_code IS NULL OR person_code=\'\')',[start,end])).rows[0].n;
  if(missing)throw new BusinessError('FORMAL_PERSON_MISSING','正式数据缺少人员编号，已回滚，请先核对源文件',409);
  await client.query('DELETE FROM person_daily_stats WHERE ref_date BETWEEN $1 AND $2',[start,end]);
  await client.query(`INSERT INTO person_daily_stats(person_code,ref_date,records,p1,p2,points)
   SELECT person_code,japan_date,count(*)::int,count(*) FILTER(WHERE points=1)::int,count(*) FILTER(WHERE points=2)::int,(count(*) FILTER(WHERE points=1)+2*count(*) FILTER(WHERE points=2))::int FROM formal_records WHERE japan_date BETWEEN $1 AND $2 GROUP BY person_code,japan_date`,[start,end]);
  const stats=(await client.query<Stats>(`SELECT person_code,sum(records)::int AS records,sum(p1)::int AS p1,sum(p2)::int AS p2,sum(points)::int AS points,
   coalesce(sum(points) FILTER(WHERE extract(day FROM ref_date)<=15),0)::int AS h1,coalesce(sum(points) FILTER(WHERE extract(day FROM ref_date)>15),0)::int AS h2,
   coalesce(sum(records) FILTER(WHERE extract(day FROM ref_date)<=15),0)::int AS h1_records,coalesce(sum(p1) FILTER(WHERE extract(day FROM ref_date)<=15),0)::int AS h1_p1,coalesce(sum(p2) FILTER(WHERE extract(day FROM ref_date)<=15),0)::int AS h1_p2,
   coalesce(sum(records) FILTER(WHERE extract(day FROM ref_date)>15),0)::int AS h2_records,coalesce(sum(p1) FILTER(WHERE extract(day FROM ref_date)>15),0)::int AS h2_p1,coalesce(sum(p2) FILTER(WHERE extract(day FROM ref_date)>15),0)::int AS h2_p2 FROM person_daily_stats WHERE ref_date BETWEEN $1 AND $2 GROUP BY person_code ORDER BY person_code`,[start,end])).rows;
  const config=(await client.query<{per_point:number;bonus_group:number;bonus_amount:number}>('SELECT per_point,bonus_group,bonus_amount FROM sys_configs ORDER BY id DESC LIMIT 1')).rows[0];
  const oldPrice=explicitPrice??((await client.query<{per_point:number}>('SELECT per_point FROM month_perf_records WHERE month=$1 ORDER BY id LIMIT 1',[month])).rows[0]?.per_point||250);
  const bonus=bonusConfig(month,{group:Number(process.env.BONUS_GROUP)||68,amount:Number(process.env.BONUS_AMOUNT)||3000,groupSchedule:process.env.BONUS_GROUP_SCHEDULE,amountSchedule:process.env.BONUS_AMOUNT_SCHEDULE,perPoint:oldPrice},config?{group:config.bonus_group,amount:config.bonus_amount,perPoint:oldPrice}:undefined);
  const monthly=stats.map(row=>({...row,month,salary:salaryFor(row.points,{...bonus,perPoint:oldPrice}),per_point:oldPrice,rate37:row.p1+row.p2?row.p2/(row.p1+row.p2):0}));
  await client.query(`INSERT INTO month_perf_records(month,person_code,records,p1,p2,points,salary,per_point,rate37,pass37,created_at)
   SELECT month,person_code,records,p1,p2,points,salary,per_point,rate37,rate37>=0.37,now() FROM json_populate_recordset(NULL::month_perf_records,$1::json)
   ON CONFLICT(month,person_code) DO UPDATE SET records=excluded.records,p1=excluded.p1,p2=excluded.p2,points=excluded.points,salary=excluded.salary,per_point=excluded.per_point,rate37=excluded.rate37,pass37=excluded.pass37`,[JSON.stringify(monthly)]);
  await client.query('DELETE FROM month_perf_records WHERE month=$1 AND NOT(person_code=ANY($2::text[]))',[month,stats.map(row=>row.person_code)]);
  if(periods)await this.syncPeriods(client,month,stats,{...bonus,perPoint:explicitPrice??(config?.per_point||oldPrice)});
  await this.syncDashboard(client,month);
  await this.comparison.refreshRange(client,start,end);
  return {month,people:stats.length,records:stats.reduce((sum,row)=>sum+row.records,0),points:stats.reduce((sum,row)=>sum+row.points,0)};
 }
 async syncPeriods(client:PoolClient,month:string,stats:Stats[],bonus:ReturnType<typeof bonusConfig>){
  const reconId=await latestReconciliation(client,month);
  const settled=reconId?(await client.query<{person_code:string;points:number}>('SELECT person_code,sum(points)::int AS points FROM recon_data_rows WHERE task_id=$1 GROUP BY person_code',[reconId])).rows:[];
  const existing=(await client.query<{id:number;person_code:string}>('SELECT id,person_code FROM payroll_period_rows WHERE month=$1 ORDER BY person_code FOR UPDATE',[month])).rows;
  const codes=[...new Set([...stats.map(row=>row.person_code),...settled.map(row=>row.person_code),...existing.map(row=>row.person_code)])].sort();
  for(const code of codes)await client.query("SELECT pg_advisory_xact_lock(hashtext('payroll-person'),hashtext($1))",[code]);
  const previous=(await client.query<{person_code:string;points:number}>('SELECT person_code,coalesce(diff_points,0)-coalesce(adjust_points,0) AS points FROM payroll_period_rows WHERE month=$1',[previousMonth(month)])).rows;
  const debt=(await client.query<{person_code:string;amount:string}>("SELECT person_code,sum(remaining)::text AS amount FROM payroll_adjusts WHERE source_month<$1 AND status='in_progress' GROUP BY person_code",[month])).rows;
  const paid=new Set((await client.query<{half:number}>('SELECT half FROM payroll_paid_marks WHERE month=$1',[month])).rows.map(row=>row.half));
  const daily=new Map(stats.map(row=>[row.person_code,row])),recon=new Map(settled.map(row=>[row.person_code,row.points])),prev=new Map(previous.map(row=>[row.person_code,row.points])),debts=new Map(debt.map(row=>[row.person_code,Number(row.amount)]));
  const values=codes.map(code=>{
   const row=daily.get(code);const halves=halfGross(row?.h1||0,row?.h2||0,bonus),sp=recon.get(code)||0,settleAmount=salaryFor(sp,bonus),diff=settled.length?settleAmount-halves.h1.amount-halves.h2.amount:0;
   const capacity=(paid.has(1)?0:halves.h1.amount)+(paid.has(2)?0:halves.h2.amount),left=(debts.get(code)||0)+capacity;
   return {month,person_code:code,half1_points:halves.h1.points,half2_points:halves.h2.points,half1_amount:halves.h1.amount,half2_amount:halves.h2.amount,half1_bonus:halves.h1.bonus,half2_bonus:halves.h2.bonus,half1_records:row?.h1_records||0,half1_p1:row?.h1_p1||0,half1_p2:row?.h1_p2||0,half2_records:row?.h2_records||0,half2_p1:row?.h2_p1||0,half2_p2:row?.h2_p2||0,settle_points:sp,settle_amount:settleAmount,prev_adjust_points:prev.get(code)||0,diff_points:sp-halves.h1.points-halves.h2.points+(prev.get(code)||0),prev_adjust_amount:diff+Math.min(0,left),diff_amount:diff,adjust_amount:diff};
  });
  const fields=['month','person_code','half1_points','half2_points','half1_amount','half2_amount','half1_bonus','half2_bonus','half1_records','half1_p1','half1_p2','half2_records','half2_p1','half2_p2','settle_points','settle_amount','prev_adjust_points','diff_points','prev_adjust_amount','diff_amount','adjust_amount'];
  await client.query(`INSERT INTO payroll_period_rows(${fields.join(',')},updated_at) SELECT ${fields.join(',')},now() FROM json_populate_recordset(NULL::payroll_period_rows,$1::json) ON CONFLICT(month,person_code) DO UPDATE SET ${fields.slice(2).map(field=>field+'=excluded.'+field).join(',')},updated_at=now()`,[JSON.stringify(values)]);
  await client.query(`UPDATE month_perf_records m SET settle_points=p.settle_points,settle_amount=p.settle_amount,diff_points=p.diff_points,diff_amount=p.diff_amount FROM payroll_period_rows p WHERE p.month=$1 AND m.month=p.month AND m.person_code=p.person_code`,[month]);
  const period=(await client.query<{id:number;person_code:string;diff_amount:number}>('SELECT id,person_code,diff_amount FROM payroll_period_rows WHERE month=$1 ORDER BY person_code',[month])).rows;
  const balances=(await client.query<{id:number;person_code:string;settled_amount:number;status:string}>('SELECT id,person_code,settled_amount,status FROM payroll_adjusts WHERE source_month=$1 ORDER BY id FOR UPDATE',[month])).rows;
  for(const row of period){const current=balances.find(balance=>balance.person_code===row.person_code);if(!current&&!row.diff_amount)continue;
   if(current)await client.query(`UPDATE payroll_adjusts SET adjust_amount=$1,remaining=$1-settled_amount,status=CASE WHEN $1-settled_amount=0 THEN 'settled' ELSE 'in_progress' END,settled_at=CASE WHEN $1-settled_amount=0 THEN coalesce(settled_at,now()) ELSE NULL END,source_task_id=coalesce($2,source_task_id),source_row_id=$3,updated_at=now() WHERE id=$4`,[row.diff_amount,reconId,row.id,current.id]);
   else await client.query(`INSERT INTO payroll_adjusts(source_month,person_code,source_task_id,source_row_id,adjust_amount,settled_amount,remaining,status,updated_at) VALUES($1,$2,$3,$4,$5,0,$5,'in_progress',now())`,[month,row.person_code,reconId,row.id,row.diff_amount]);
  }
 }
 async syncDashboard(client:PoolClient,month:string){
  const aggregate=(await client.query<{employees:number;records:number;p1:number;p2:number;points:number;amount:string}>('SELECT count(*)::int AS employees,coalesce(sum(records),0)::int AS records,coalesce(sum(p1),0)::int AS p1,coalesce(sum(p2),0)::int AS p2,coalesce(sum(points),0)::int AS points,coalesce(sum(salary),0)::text AS amount FROM month_perf_records WHERE month=$1',[month])).rows[0];
  await client.query('DELETE FROM dash_metrics WHERE month=$1',[month]);
  const qualityRows=(await client.query<{status:string;n:number;days:number}>(`SELECT coalesce(clean_status,'?') AS status,count(*)::int AS n,count(DISTINCT left(modified_raw,10))::int AS days FROM raw_records WHERE left(modified_raw,7)=$1 GROUP BY coalesce(clean_status,'?')`,[month])).rows;
  const q=Object.fromEntries(qualityRows.map(row=>[row.status,row.n]));const quality={total:qualityRows.reduce((sum,row)=>sum+row.n,0),valid:q.valid||0,cross_file_dup:q.cross_file_dup||0,master_late:q.master_late||0,from_sub:q.from_sub||0,visible_blank:q.visible_blank||0,valid_days:qualityRows.find(row=>row.status==='valid')?.days||0};
  const duplicates=(await client.query<{code:string;n:number}>("SELECT submitter_code AS code,count(*)::int AS n FROM raw_records WHERE clean_status='cross_file_dup' AND left(modified_raw,7)=$1 AND submitter_code IS NOT NULL AND submitter_code<>'' GROUP BY submitter_code",[month])).rows;
  const previous=(await client.query<{month:string}>('SELECT max(month) AS month FROM month_perf_records WHERE month<$1',[month])).rows[0]?.month??null;
  const ranking=(await client.query<{code:string;name:string;month:string;points:number;amount:number;records:number;p1:number;p2:number}>(`SELECT m.person_code AS code,coalesce(p.display_name,m.person_code) AS name,m.month,m.points,m.salary AS amount,m.records,m.p1,m.p2 FROM month_perf_records m LEFT JOIN persons p ON p.code=m.person_code WHERE m.month=ANY($1::text[]) ORDER BY m.points DESC,m.person_code`,[[month,...previous?[previous]:[]]])).rows;
  const current=ranking.filter(row=>row.month===month),prior=ranking.filter(row=>row.month===previous),nowCodes=new Set(current.map(row=>row.code)),priorCodes=new Set(prior.map(row=>row.code));
  const added=current.filter(row=>!priorCodes.has(row.code)),gone=prior.filter(row=>!nowCodes.has(row.code));
  const n=aggregate.employees,records=aggregate.records,points=aggregate.points,amount=Number(aggregate.amount);
  const metrics:Record<string,number>={employees:n,records,p1:aggregate.p1,p2:aggregate.p2,total_points:points,total_amount:amount,p2rate:records?aggregate.p2/records:0,per_emp_points:n?points/n:0,per_emp_amount:n?amount/n:0,per_emp_records:n?records/n:0,per_store_points:records?points/records:0,dup_total:duplicates.reduce((sum,row)=>sum+row.n,0),new_staff:added.length,gone_staff:gone.length,q_total:quality.total,q_valid:quality.valid,q_cross_file_dup:quality.cross_file_dup,q_master_late:quality.master_late,q_from_sub:quality.from_sub,q_visible_blank:quality.visible_blank,q_valid_days:quality.valid_days};
  await client.query(`INSERT INTO dash_metrics(month,metric,value,updated_at) SELECT $1,key,value::float8,now() FROM jsonb_each_text($2::jsonb)`,[month,JSON.stringify(metrics)]);
  const payloads={quality,dup_map:Object.fromEntries(duplicates.map(row=>[row.code,row.n])),top_staff:current.slice(0,8).map(({month,...row})=>row),new_staff:added.map(row=>({name:row.name,points:row.points,amount:row.amount})),gone_staff:gone.map(row=>({name:row.name,points:row.points,amount:row.amount}))};
  for(const [metric,payload]of Object.entries(payloads))await client.query('INSERT INTO dash_metrics(month,metric,value,payload,updated_at) VALUES($1,$2,0,$3,now())',[month,metric,JSON.stringify(payload)]);

 }
}
