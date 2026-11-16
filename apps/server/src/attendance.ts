import {Body,Controller,Get,Inject,Injectable,Param,Post,Query,Req} from '@nestjs/common';
import type {Request} from 'express';
import type {PoolClient} from 'pg';
import {randomUUID} from 'node:crypto';
import {z} from 'zod';
import type {CurrentUser} from '../../../packages/contracts/src';
import {AuthService} from './auth';
import {Database} from './database';
import {BusinessError} from './errors';

/** JST 05:00 is UTC 20:00 on the preceding calendar day. */
export function attendanceDay(at=new Date()){return new Date(at.getTime()+4*3600_000).toISOString().slice(0,10);}
const kind=z.enum(['in','out']);
const instant=z.string().datetime({offset:true});
const punchSchema=z.object({kind,requestKey:z.uuid(),latitude:z.number().min(-90).max(90),longitude:z.number().min(-180).max(180),accuracy:z.number().nonnegative().finite(),locatedAt:instant}).strict();
const makeupSchema=z.object({kind,requestKey:z.uuid(),occurredAt:instant,reason:z.string().trim().min(1).max(1000)}).strict();
const date=z.string().regex(/^\d{4}-\d{2}-\d{2}$/).refine(v=>!Number.isNaN(Date.parse(v))&&new Date(v).toISOString().slice(0,10)===v);
const querySchema=z.object({day:date.optional(),scope:z.enum(['mine','team','all']).default('mine'),team:z.coerce.number().int().positive().optional(),q:z.string().trim().max(100).default(''),page:z.coerce.number().int().min(1).max(10000).default(1)}).strict();
function parse<T>(schema:z.ZodType<T>,input:unknown){const result=schema.safeParse(input);if(!result.success)throw new BusinessError('VALIDATION','请检查打卡类型、日期及定位信息',400);return result.data;}
function staff(user:CurrentUser){if(!user.personCode||!['staff','leader'].includes(user.role))throw new BusinessError('FORBIDDEN','只有员工和队长可以为本人打卡',403);return user.personCode;}
@Injectable()
export class AttendanceService {
 constructor(@Inject(Database)private readonly db:Database){}
 private async actor(client:PoolClient,user:CurrentUser,admin=false){
  const row=(await client.query("SELECT id,person_code,role FROM users WHERE id=$1 AND is_active=true AND status IN ('active','leave') AND must_change_password=false FOR SHARE",[user.id])).rows[0];
  if(!row||(admin?row.role!=='admin':!['staff','leader'].includes(row.role)||row.person_code!==user.personCode))throw new BusinessError('FORBIDDEN','账号权限已变化，请重新登录',403);
 }
 async punch(user:CurrentUser,input:unknown){const code=staff(user),data=parse(punchSchema,input);return this.db.transaction(async client=>{
  await this.actor(client,user);
  const previous=(await client.query('SELECT * FROM bd_attendance_event WHERE person_code=$1 AND request_key=$2',[code,data.requestKey])).rows[0];
  if(previous){this.samePunch(previous,data);return {id:previous.id,day:attendanceDay(new Date(previous.occurred_at))};}
  if(Math.abs(Date.now()-Date.parse(data.locatedAt))>120_000)throw new BusinessError('VALIDATION','定位已过期，请重新获取位置后打卡',400);
  const row=(await client.query(`INSERT INTO bd_attendance_event(person_code,kind,source,latitude,longitude,accuracy,located_at,request_key,actor_id)
   VALUES($1,$2,'onsite',$3,$4,$5,$6,$7,$8) ON CONFLICT(person_code,request_key) DO NOTHING RETURNING *`,[code,data.kind,data.latitude,data.longitude,data.accuracy,data.locatedAt,data.requestKey,user.id])).rows[0]
   ??(await client.query('SELECT * FROM bd_attendance_event WHERE person_code=$1 AND request_key=$2',[code,data.requestKey])).rows[0];
  this.samePunch(row,data);return {id:row.id,day:attendanceDay(new Date(row.occurred_at))};
 });}
 private samePunch(row:Record<string,any>,data:z.infer<typeof punchSchema>){if(row.source!=='onsite'||row.kind!==data.kind||row.latitude!==data.latitude||row.longitude!==data.longitude||row.accuracy!==data.accuracy||new Date(row.located_at).getTime()!==Date.parse(data.locatedAt))throw new BusinessError('CONFLICT','同一提交标识不能用于不同打卡',409);}
 async makeup(user:CurrentUser,input:unknown){const code=staff(user),data=parse(makeupSchema,input);return this.db.transaction(async client=>{
  await this.actor(client,user);
  const previous=(await client.query('SELECT * FROM bd_attendance_makeup WHERE person_code=$1 AND request_key=$2',[code,data.requestKey])).rows[0];
  if(!previous&&Date.parse(data.occurredAt)>Date.now())throw new BusinessError('VALIDATION','不能申请未来时间的补卡',400);
  const row=previous??(await client.query(`INSERT INTO bd_attendance_makeup(person_code,kind,occurred_at,reason,request_key,requested_by) VALUES($1,$2,$3,$4,$5,$6)
   ON CONFLICT(person_code,request_key) DO NOTHING RETURNING *`,[code,data.kind,data.occurredAt,data.reason,data.requestKey,user.id])).rows[0]
   ??(await client.query('SELECT * FROM bd_attendance_makeup WHERE person_code=$1 AND request_key=$2',[code,data.requestKey])).rows[0];
  if(row.kind!==data.kind||new Date(row.occurred_at).getTime()!==Date.parse(data.occurredAt)||row.reason!==data.reason)throw new BusinessError('CONFLICT','同一提交标识不能用于不同补卡',409);
  return {id:row.id,day:attendanceDay(new Date(row.occurred_at))};
 });}
 async review(user:CurrentUser,id:number,input:unknown){const data=parse(z.object({decision:z.enum(['approved','rejected']),note:z.string().trim().max(1000).default('')}).strict(),input);return this.db.transaction(async client=>{
  await this.actor(client,user,true);
  const row=(await client.query('SELECT * FROM bd_attendance_makeup WHERE id=$1 FOR UPDATE',[id])).rows[0];
  if(!row)throw new BusinessError('NOT_FOUND','补卡申请不存在',404);
  if(row.status!=='pending')throw new BusinessError('CONFLICT','补卡申请已处理，请刷新',409);
  let eventId=null;
  if(data.decision==='approved')eventId=(await client.query(`INSERT INTO bd_attendance_event(person_code,kind,occurred_at,source,request_key,actor_id) VALUES($1,$2,$3,'makeup',$4,$5) RETURNING id`,[row.person_code,row.kind,row.occurred_at,randomUUID(),user.id])).rows[0].id;
  await client.query('UPDATE bd_attendance_makeup SET status=$2,reviewed_by=$3,reviewed_at=clock_timestamp(),review_note=$4,event_id=$5 WHERE id=$1',[id,data.decision,user.id,data.note,eventId]);return {id,status:data.decision};
 });}
 async list(user:CurrentUser,input:unknown){const data=parse(querySchema,input),day=data.day??attendanceDay();
  if(data.scope==='mine')staff(user);
  if(data.scope==='all'&&user.role!=='admin'||data.scope==='team'&&user.role!=='leader')throw new BusinessError('FORBIDDEN','没有查看该范围的权限',403);
  // Current membership controls access to all history: events deliberately have no team_id.
  const teams=(await this.db.query(`SELECT t.id,t.name FROM bd_team t WHERE t.status='active' AND ($1='admin' OR EXISTS(SELECT 1 FROM bd_team_member m WHERE m.team_id=t.id AND m.end_date IS NULL AND m.role='leader' AND m.person_code=$2)) ORDER BY t.name,t.id`,[data.scope==='all'?'admin':'leader',user.personCode])).rows;
  const result=(await this.db.query(`WITH people AS (
   SELECT p.code,p.display_name AS name,coalesce((SELECT string_agg(t.name,' / ' ORDER BY t.id) FROM bd_team_member m JOIN bd_team t ON t.id=m.team_id AND t.status='active' WHERE m.person_code=p.code AND m.end_date IS NULL),'') AS team
   FROM persons p WHERE
   ($1<>'mine' OR p.code=$2) AND
   ($1<>'team' OR EXISTS(SELECT 1 FROM bd_team_member member JOIN bd_team t ON t.id=member.team_id AND t.status='active' JOIN bd_team_member leader ON leader.team_id=member.team_id AND leader.end_date IS NULL AND leader.role='leader' AND leader.person_code=$2 WHERE member.person_code=p.code AND member.end_date IS NULL)) AND
   ($3::int IS NULL OR EXISTS(SELECT 1 FROM bd_team_member m JOIN bd_team t ON t.id=m.team_id AND t.status='active' WHERE m.person_code=p.code AND m.team_id=$3 AND m.end_date IS NULL)) AND
   ($4='' OR p.code ILIKE '%'||$4||'%' OR p.display_name ILIKE '%'||$4||'%') AND
   (EXISTS(SELECT 1 FROM users u WHERE u.person_code=p.code AND u.role IN ('staff','leader') AND u.status<>'deleted') OR EXISTS(SELECT 1 FROM bd_attendance_event e WHERE e.person_code=p.code AND e.business_date=$5::date) OR EXISTS(SELECT 1 FROM bd_attendance_makeup r WHERE r.person_code=p.code AND r.business_date=$5::date))
  ), page AS (SELECT * FROM people ORDER BY name,code LIMIT 30 OFFSET $6)
  SELECT (SELECT count(*)::int FROM people) AS total,coalesce((SELECT json_agg(p ORDER BY p.name,p.code) FROM page p),'[]') AS people,
  coalesce((SELECT json_agg(json_build_object('id',e.id,'code',e.person_code,'kind',e.kind,'at',e.occurred_at,'source',e.source,'latitude',e.latitude,'longitude',e.longitude,'accuracy',e.accuracy,'locatedAt',e.located_at,'createdAt',e.created_at) ORDER BY e.occurred_at,e.id) FROM bd_attendance_event e WHERE e.business_date=$5::date AND e.person_code IN(SELECT code FROM page)),'[]') AS events,
  coalesce((SELECT json_agg(json_build_object('id',r.id,'code',r.person_code,'kind',r.kind,'at',r.occurred_at,'reason',r.reason,'status',r.status,'note',r.review_note,'createdAt',r.created_at,'reviewedAt',r.reviewed_at,'reviewer',u.display_name) ORDER BY r.created_at DESC,r.id DESC) FROM bd_attendance_makeup r LEFT JOIN users u ON u.id=r.reviewed_by WHERE r.business_date=$5::date AND r.person_code IN(SELECT code FROM page)),'[]') AS requests`,[data.scope,user.personCode,data.team??null,data.q,day,(data.page-1)*30])).rows[0];
  return {...result,day,today:attendanceDay(),scope:data.scope,page:data.page,pageSize:30,teams:data.scope==='mine'?[]:teams};
 }
}
@Controller('attendance')
export class AttendanceController {
 constructor(@Inject(AuthService)private readonly auth:AuthService,@Inject(AttendanceService)private readonly service:AttendanceService){}
 @Get()async list(@Req()req:Request,@Query()query:unknown){return this.service.list((await this.auth.require(req)).user,query);}
 @Post('punch')async punch(@Req()req:Request,@Body()body:unknown){return this.service.punch((await this.auth.require(req,{write:true})).user,body);}
 @Post('makeup')async makeup(@Req()req:Request,@Body()body:unknown){return this.service.makeup((await this.auth.require(req,{write:true})).user,body);}
 @Post('makeup/:id/review')async review(@Req()req:Request,@Param('id')id:string,@Body()body:unknown){return this.service.review((await this.auth.require(req,{admin:true,write:true})).user,parse(z.coerce.number().int().positive(),id),body);}
}
