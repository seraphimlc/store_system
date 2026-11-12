import { Body, Controller, Get, Inject, Injectable, Post, Query, Req, Res } from '@nestjs/common';
import type { Request, Response } from 'express';
import type { PoolClient } from 'pg';
import { z } from 'zod';
import ExcelJS from 'exceljs';
import { AuthService } from './auth';
import type { Session } from '../../../packages/contracts/src';
import { Database } from './database';
import { BusinessError } from './errors';
import { jstToday, validDate } from './business-dates';
import { addDays, periodBounds, periodDays, periodOf, planView, shiftPeriod, windowState, landingPath, type PlanRow } from './plan-rules';
const keySchema=z.string().max(16).refine(value=>{try{periodBounds(value);return true;}catch{return false;}});
const dateSchema=z.string().refine(validDate);
function parse<T>(schema:z.ZodType<T>,input:unknown){const result=schema.safeParse(input);if(!result.success)throw new BusinessError('VALIDATION','请检查半月和日期格式',400);return result.data;}
interface Leave {id:number;start_date:string;end_date:string|null;reason:string;status:string}
@Injectable()
export class PlansService {
 constructor(@Inject(Database)private readonly db:Database){}
 async sessionLanding(session:Session,afterPassword=false){return {...session,landingPath:landingPath(session.user.role,false,afterPassword)};}
 async roster(code:string,client?:PoolClient){
  const sql=`SELECT min(day)::text AS day FROM (
    SELECT min((created_at+interval '9 hours')::date) AS day FROM users WHERE role IN ('staff','leader') AND person_code=$1
    UNION ALL SELECT (created_at+interval '9 hours')::date FROM persons WHERE code=$1
    UNION ALL SELECT min(plan_date) FROM staff_date_plans WHERE person_code=$1) firsts`;
  return (client?await client.query(sql,[code]):await this.db.query(sql,[code])).rows[0].day as string|null;
 }
 async rows(code:string,key:string,client?:PoolClient){const bounds=periodBounds(key);const sql='SELECT plan_date::text AS date,available,reported,source,leave_id,updated_at::text AS updated_at FROM staff_date_plans WHERE person_code=$1 AND plan_date BETWEEN $2::date AND $3::date ORDER BY plan_date';return (client?await client.query<PlanRow>(sql,[code,bounds.start,bounds.end]):await this.db.query<PlanRow>(sql,[code,bounds.start,bounds.end])).rows;}
 async currentLeave(code:string,client?:PoolClient){const sql="SELECT id,start_date::text AS start_date,end_date::text AS end_date,reason,status FROM bd_staff_leave WHERE person_code=$1 AND status='active' ORDER BY id DESC LIMIT 1";return (client?await client.query<Leave>(sql,[code]):await this.db.query<Leave>(sql,[code])).rows[0]??null;}
 async mine(code:string,requested?:string){
  const today=jstToday(),roster=await this.roster(code),current=periodOf(today),next=shiftPeriod(current);
  const key=requested?periodBounds(requested).key:windowState(current,today,roster)==='open'?current:windowState(next,today,roster)==='open'?next:current;
  const rows=await this.rows(code,key);let pending:string|null=null;
  for(const candidate of [current,next])if(windowState(candidate,today,roster)==='open'){
   const candidateRows=candidate===key?rows:await this.rows(code,candidate);if(!candidateRows.some(row=>row.source!=='report')){pending=candidate;break;}
  }
  return {...planView(key,rows,roster,today),previous:shiftPeriod(key,-1),next:shiftPeriod(key),pending,leave:await this.currentLeave(code)};
 }
 async save(code:string,key:string,off:string[]){return this.db.transaction(async client=>{
  const today=jstToday(),period=periodBounds(key);const roster=await this.roster(code,client);
  if(windowState(key,today,roster)!=='open')throw new BusinessError('PLAN_WINDOW_CLOSED','该半月登记窗口未开放或已关闭',409);
  const days=periodDays(key);if(off.some(date=>!days.includes(date)))throw new BusinessError('VALIDATION','不出勤日期必须属于所选半月',400);
  await client.query('SELECT pg_advisory_xact_lock(hashtext($1),1)',[code]);
  const rows=await this.rows(code,key,client);const view=planView(key,rows,roster,today);
  const editable=view.days.filter(day=>day.editable);if(!editable.length)throw new BusinessError('PLAN_ALL_LOCKED','该半月日期已锁定，无法修改',409);
  const values=editable.map(day=>({plan_date:day.date,available:!off.includes(day.date)}));
  const changed=await client.query(`INSERT INTO staff_date_plans(person_code,plan_date,available,reported,source,created_at,updated_at)
    SELECT $1,plan_date,available,false,'web',now(),now() FROM json_to_recordset($2::json) AS dates(plan_date date,available boolean)
    ON CONFLICT(person_code,plan_date) DO UPDATE SET available=excluded.available,source='web',updated_at=now() WHERE NOT staff_date_plans.reported`,[code,JSON.stringify(values)]);
  if(!changed.rowCount)throw new BusinessError('PLAN_ALL_LOCKED','该半月日期已锁定，无法修改',409);
  return {key:period.key,written:changed.rowCount,skipped:days.length-changed.rowCount,off_cnt:editable.filter(day=>off.includes(day.date)).length};
 });}
 async matrix(key:string){
  const today=jstToday(),bounds=periodBounds(key);
  const people=(await this.db.query<{code:string;name:string;status:string}>(`WITH accounts AS (
   SELECT DISTINCT ON(u.person_code) u.person_code AS code,coalesce(p.display_name,nullif(u.display_name,''),u.person_code) AS name,u.status
   FROM users u LEFT JOIN persons p ON p.code=u.person_code WHERE u.role IN ('staff','leader') AND u.person_code IS NOT NULL AND u.person_code<>'' ORDER BY u.person_code,CASE u.status WHEN 'active' THEN 0 WHEN 'leave' THEN 1 ELSE 2 END,u.id
   ),planned AS (SELECT DISTINCT person_code AS code FROM staff_date_plans WHERE plan_date BETWEEN $1::date AND $2::date)
   SELECT coalesce(a.code,planned.code) AS code,coalesce(a.name,p.display_name,planned.code) AS name,coalesce(a.status,'') AS status
   FROM accounts a FULL JOIN planned ON planned.code=a.code LEFT JOIN persons p ON p.code=planned.code
   WHERE a.status IN ('active','leave') OR planned.code IS NOT NULL ORDER BY name,code`,[bounds.start,bounds.end])).rows;
  const codes=people.map(person=>person.code);
  const plans=(await this.db.query<PlanRow&{person_code:string}>('SELECT person_code,plan_date::text AS date,available,reported,source,leave_id,updated_at::text AS updated_at FROM staff_date_plans WHERE person_code=ANY($1::text[]) AND plan_date BETWEEN $2::date AND $3::date',[codes,bounds.start,bounds.end])).rows;
  const starts=(await this.db.query<{code:string;day:string}>(`SELECT code,min(day)::text AS day FROM (
   SELECT person_code AS code,min((created_at+interval '9 hours')::date) AS day FROM users WHERE person_code=ANY($1::text[]) AND role IN ('staff','leader') GROUP BY person_code
   UNION ALL SELECT code,(created_at+interval '9 hours')::date FROM persons WHERE code=ANY($1::text[])
   UNION ALL SELECT person_code,min(plan_date) FROM staff_date_plans WHERE person_code=ANY($1::text[]) GROUP BY person_code) rows GROUP BY code`,[codes])).rows;
  const roster=new Map(starts.map(row=>[row.code,row.day]));const grouped=new Map<string,PlanRow[]>();for(const row of plans){const rows=grouped.get(row.person_code)??[];rows.push(row);grouped.set(row.person_code,rows);}
  const rows=people.map(person=>{
   const view=planView(key,grouped.get(person.code)??[],roster.get(person.code)??null,today,!!person.status&&!['active','leave'].includes(person.status),false);
   return {...person,...view,on_cnt:view.days.filter(day=>day.state==='on').length,off_cnt:view.days.filter(day=>['off','miss'].includes(day.state)).length};
  }).filter(row=>!row.days.every(day=>day.state==='na'));
  const summary=periodDays(key).map(date=>{
   let free=0,unregistered=0,defaultCount=0,planned=0,actual=0;
   for(const row of rows){const day=row.days.find(value=>value.date===date)!;if(day.state==='na')continue;
    const source=(grouped.get(row.code)??[]).find(value=>value.date===date);const inactive=!!row.status&&!['active','leave'].includes(row.status);
    if(['on','done'].includes(day.state))free++;
    if(!source&&!inactive&&date>=today)unregistered++;
    if(day.assumed&&!inactive)defaultCount++;
    if(day.reported)actual++;
    if((source?.available||day.assumed)&&!(inactive&&date>=today))planned++;
   }
   return {date,free:date<today?0:free,unregistered,defaultCount:date<today?0:defaultCount,planned,actual:date<=today?actual:null};
  });
  return {...bounds,today,rows,summary,total:rows.length,unsubmitted:rows.filter(row=>!row.submitted).map(row=>row.code)};
 }
 async clearLeave(client:PoolClient,id:number){await client.query('UPDATE staff_date_plans SET leave_id=NULL,updated_at=now() WHERE leave_id=$1 AND reported=true',[id]);await client.query('DELETE FROM staff_date_plans WHERE leave_id=$1 AND reported=false',[id]);}
 async leave(code:string,userId:number,username:string,input:{start:string;end:string|null;reason:string}|null){return this.db.transaction(async client=>{
  await client.query('SELECT pg_advisory_xact_lock(hashtext($1),1)',[code]);
  const previous=await this.currentLeave(code,client),today=jstToday();
  if(previous){const end=input?addDays(input.start,-1):[previous.start_date,addDays(today,-1)].sort().at(-1)!;await client.query("UPDATE bd_staff_leave SET status='ended',end_date=CASE WHEN $3::boolean OR end_date IS NULL OR end_date>$1::date THEN $1::date ELSE end_date END,updated_at=now() WHERE id=$2",[end,previous.id,!input]);await this.clearLeave(client,previous.id);}
  if(!input){if(previous)await client.query("INSERT INTO bd_log(domain,ref_id,ref_label,action,field,old_value,new_value,actor,actor_name,note,created_at) SELECT 'member',$1,$2,'status','假期模式','休假中','已结束',username,display_name,'员工自己/管理员结束',now() FROM users WHERE id=$3",[previous.id,code,userId]);return {ended:!!previous};}
  if(input.end&&input.end<input.start)throw new BusinessError('VALIDATION','结束日不能早于开始日',400);
  const row=(await client.query(`INSERT INTO bd_staff_leave(person_code,start_date,end_date,reason,status,created_by,created_at,updated_at) VALUES($1,$2,$3,$4,'active',$5,now(),now()) RETURNING id`,[code,input.start,input.end,input.reason.trim(),username])).rows[0];
  const start=input.start>today?input.start:today,end=input.end&&input.end<addDays(start,400)?input.end:addDays(start,400);
  await client.query(`INSERT INTO staff_date_plans(person_code,plan_date,available,reported,source,leave_id,created_at,updated_at)
   SELECT $1,day::date,false,false,'leave',$4,now(),now() FROM generate_series($2::date,$3::date,interval '1 day') AS day
   ON CONFLICT(person_code,plan_date) DO UPDATE SET available=false,source='leave',leave_id=$4,updated_at=now()
   WHERE NOT staff_date_plans.reported AND (staff_date_plans.available OR staff_date_plans.leave_id=$4)`,[code,start,end,row.id]);
  await client.query("INSERT INTO bd_log(domain,ref_id,ref_label,action,field,old_value,new_value,actor,actor_name,note,created_at) SELECT 'member',$1,$2,'status','假期模式',$3,$4,username,display_name,$5,now() FROM users WHERE id=$6",[row.id,code,previous?'休假中':'正常','休假 '+input.start+' ~ '+(input.end??'未定'),input.reason,userId]);
  return {id:row.id};
 });}
}
@Controller('plans')
export class PlansController {
 constructor(@Inject(AuthService)private readonly auth:AuthService,@Inject(PlansService)private readonly service:PlansService){}
 @Get('mine')async mine(@Req()req:Request,@Query()query:unknown){const {user}=await this.auth.require(req);if(!user.personCode)throw new BusinessError('PERSON_NOT_FOUND','账号未绑定员工编号',400);const input=parse(z.object({key:keySchema.optional()}).strict(),query);return this.service.mine(user.personCode,input.key);}
 @Post('mine')async save(@Req()req:Request,@Body()body:unknown){const {user}=await this.auth.require(req,{write:true});if(!user.personCode)throw new BusinessError('PERSON_NOT_FOUND','账号未绑定员工编号',400);const input=parse(z.object({key:keySchema,unavailable:z.array(dateSchema).max(16)}).strict(),body);return this.service.save(user.personCode,input.key,input.unavailable);}
 @Post('leave/start')async startLeave(@Req()req:Request,@Body()body:unknown){const {user}=await this.auth.require(req,{write:true});if(!user.personCode)throw new BusinessError('PERSON_NOT_FOUND','账号未绑定员工编号',400);const input=parse(z.object({start:dateSchema,end:dateSchema.nullable(),reason:z.string().max(2000).default('')}).strict(),body);return this.service.leave(user.personCode,user.id,user.username,input);}
 @Post('leave/end')async endLeave(@Req()req:Request,@Body()body:unknown){const {user}=await this.auth.require(req,{write:true});if(!user.personCode)throw new BusinessError('PERSON_NOT_FOUND','账号未绑定员工编号',400);parse(z.object({}).strict(),body);return this.service.leave(user.personCode,user.id,user.username,null);}
 @Get()async matrix(@Req()req:Request,@Query()query:unknown){await this.auth.require(req,{admin:true});const input=parse(z.object({key:keySchema.default(periodOf(jstToday()))}).strict(),query);return this.service.matrix(input.key);}
 @Get('export')async export(@Req()req:Request,@Query()query:unknown,@Res()response:Response){await this.auth.require(req,{admin:true});const input=parse(z.object({key:keySchema.default(periodOf(jstToday()))}).strict(),query);const matrix=await this.service.matrix(input.key);const workbook=new ExcelJS.Workbook();const sheet=workbook.addWorksheet('出勤计划');sheet.addRow(['编号','姓名',...periodDays(input.key)]);sheet.addRow(['计划出勤','',...matrix.summary.map(day=>day.planned)]);sheet.addRow(['实际出勤','',...matrix.summary.map(day=>day.actual)]);sheet.addRow(['未登记','',...matrix.summary.map(day=>day.unregistered)]);for(const row of matrix.rows)sheet.addRow([row.code,row.name,...row.days.map(day=>day.mark)]);const notes=workbook.addWorksheet('图例');notes.addRows([['符号','说明'],['□','已自报出勤'],['○','计划出勤'],['×','不出勤'],['△','计划出勤但未自报'],['–','未登记'],['','名册起点之前']]);response.setHeader('Content-Type','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');response.setHeader('Content-Disposition','attachment; filename="attendance-plan.xlsx"');response.send(Buffer.from(await workbook.xlsx.writeBuffer()));}
}
