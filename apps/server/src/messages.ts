import { Body, Controller, Get, Inject, Injectable, Param, Post, Query, Req } from '@nestjs/common';
import type { Request } from 'express';
import type { CurrentUser } from '../../../packages/contracts/src';
import { z } from 'zod';
import { AuthService } from './auth';
import { Database } from './database';
import { BusinessError } from './errors';
const MAX_RECIPIENTS=1000;
function parse<T>(schema:z.ZodType<T>,input:unknown){const result=schema.safeParse(input);if(!result.success)throw new BusinessError('VALIDATION','请检查消息标题、收件人或分页参数',400);return result.data;}
const pageSchema=z.object({page:z.coerce.number().int().min(1).max(10000).default(1),unread:z.enum(['true','false']).default('false')}).strict();
const sendSchema=z.object({title:z.string().trim().min(1).max(200),body:z.string().max(20000).default(''),mode:z.enum(['all','pick']),codes:z.array(z.string().min(1).max(32)).max(MAX_RECIPIENTS).default([])}).strict();
@Injectable()
export class MessagesService {
 constructor(@Inject(Database)private readonly db:Database){}
 private canSend(user:CurrentUser){if(!['admin','leader'].includes(user.role))throw new BusinessError('FORBIDDEN','员工不能发送消息',403);}
 async candidates(user:CurrentUser){
  this.canSend(user);
  return (await this.db.query<{code:string;name:string;status:string}>(`SELECT p.code,p.display_name AS name,CASE WHEN bool_or(u.status='active') THEN 'active' ELSE 'leave' END AS status
   FROM persons p JOIN users u ON u.person_code=p.code WHERE u.status IN ('active','leave') AND ($1::boolean OR EXISTS (
    SELECT 1 FROM bd_team_member leader JOIN bd_team t ON t.id=leader.team_id JOIN bd_team_member member ON member.team_id=t.id
    WHERE leader.person_code=$2 AND leader.role='leader' AND leader.end_date IS NULL AND member.person_code=p.code AND member.end_date IS NULL AND t.status='active'
   )) GROUP BY p.code,p.display_name ORDER BY p.display_name,p.code`,[user.role==='admin',user.personCode])).rows;
 }
 async send(user:CurrentUser,input:z.infer<typeof sendSchema>){
  this.canSend(user);
  return this.db.transaction(async client=>{
   // Recheck sender and recipients inside this transaction; no cached client-supplied team permission.
   const sender=(await client.query(`SELECT id,username,display_name,role,person_code,status,is_active FROM users WHERE id=$1 FOR SHARE`,[user.id])).rows[0];
   if(!sender||!sender.is_active||!['active','leave'].includes(sender.status)||!['admin','leader'].includes(sender.role))throw new BusinessError('FORBIDDEN','发送权限已变化，请刷新重试',403);
   const available=(await client.query<{code:string}>(`SELECT DISTINCT u.person_code AS code FROM users u JOIN persons p ON p.code=u.person_code WHERE u.status IN ('active','leave') AND ($1::boolean OR EXISTS (
    SELECT 1 FROM bd_team_member leader JOIN bd_team t ON t.id=leader.team_id JOIN bd_team_member member ON member.team_id=t.id WHERE leader.person_code=$2 AND leader.role='leader' AND leader.end_date IS NULL AND member.person_code=u.person_code AND member.end_date IS NULL AND t.status='active'
   )) ORDER BY u.person_code`,[sender.role==='admin',sender.person_code])).rows.map(row=>row.code);
   const allowed=new Set(available);const wanted=[...new Set(input.codes)];
   if(input.mode==='pick'&&wanted.some(code=>!allowed.has(code)))throw new BusinessError('RECIPIENT_FORBIDDEN','收件人不在当前可发送范围，或已离职、停用',403);
   const codes=(input.mode==='all'?available:wanted).slice(0,MAX_RECIPIENTS);
   if(!codes.length)throw new BusinessError('NO_RECIPIENTS','没有可发送的收件人',400);
   const message=(await client.query(`INSERT INTO bd_message(sender_kind,sender,sender_name,title,body,url,scope,ref_type,created_at) VALUES($1,$2,$3,$4,$5,'','manual','',now()) RETURNING id`,[sender.role,sender.username,sender.display_name||sender.username,input.title,input.body.trim()])).rows[0];
   await client.query(`INSERT INTO bd_message_recipient(message_id,person_code,created_at) SELECT $1,code,now() FROM unnest($2::text[]) AS code`,[message.id,codes]);
   await client.query(`INSERT INTO bd_log(domain,ref_id,ref_label,action,field,new_value,actor,actor_name,note,created_at) VALUES('message',$1,$2,'create','收件人',$3,$4,$5,$6,now())`,[message.id,input.title.slice(0,128),codes.length+' 人',sender.username,sender.display_name||sender.username,input.body.slice(0,200)]);
   return {id:message.id,recipients:codes.length};
  });
 }
 async inbox(code:string,page:number,unread:boolean){
  const result=(await this.db.query(`WITH matching AS (
   SELECT m.id,m.title,m.body,m.sender_name AS "senderName",m.sender_kind AS "senderKind",m.scope,m.url,m.created_at AS "createdAt",r.read_at AS "readAt"
   FROM bd_message_recipient r JOIN bd_message m ON m.id=r.message_id WHERE r.person_code=$1 AND (NOT $2::boolean OR r.read_at IS NULL)
   ),paged AS (SELECT * FROM matching ORDER BY "createdAt" DESC,id DESC LIMIT 30 OFFSET $3)
   SELECT (SELECT count(*)::int FROM matching) AS total,coalesce((SELECT json_agg(paged) FROM paged),'[]'::json) AS items,
   (SELECT count(*)::int FROM bd_message_recipient WHERE person_code=$1 AND read_at IS NULL) AS unread`,[code,unread,(page-1)*30])).rows[0];
  return {...result,page,pageSize:30};
 }
 async sent(user:CurrentUser,page:number){this.canSend(user);const result=(await this.db.query(`WITH matching AS (
   SELECT id,title,body,sender_name AS "senderName",created_at AS "createdAt" FROM bd_message WHERE sender=$1
   ),paged AS (SELECT * FROM matching ORDER BY "createdAt" DESC,id DESC LIMIT 30 OFFSET $2),enriched AS (
   SELECT p.*,(SELECT count(*)::int FROM bd_message_recipient r WHERE r.message_id=p.id) AS recipients,
   (SELECT count(*)::int FROM bd_message_recipient r WHERE r.message_id=p.id AND r.read_at IS NOT NULL) AS "readCount" FROM paged p)
   SELECT (SELECT count(*)::int FROM matching) AS total,coalesce((SELECT json_agg(enriched) FROM enriched),'[]'::json) AS items`,[user.username,(page-1)*30])).rows[0];return {...result,page,pageSize:30};}
 async detail(user:CurrentUser,id:number){const {rows:[message]}=await this.db.query(`SELECT m.id,m.title,m.body,m.sender_name AS "senderName",m.created_at AS "createdAt",m.url,(SELECT r.read_at FROM bd_message_recipient r WHERE r.message_id=m.id AND r.person_code=$2 LIMIT 1) AS "readAt" FROM bd_message m WHERE m.id=$1 AND (m.sender=$3 OR EXISTS(SELECT 1 FROM bd_message_recipient r WHERE r.message_id=m.id AND r.person_code=$2))`,[id,user.personCode??'',user.username]);if(!message)throw new BusinessError('NOT_FOUND','消息不存在或没有访问权限',404);return message;}
 async read(code:string,id:number|null){const changed=await this.db.query('UPDATE bd_message_recipient SET read_at=now() WHERE person_code=$1 AND ($2::int IS NULL OR message_id=$2) AND read_at IS NULL RETURNING id',[code,id]);return {count:changed.rowCount};}
}
@Controller('messages')
export class MessagesController {
 constructor(@Inject(AuthService)private readonly auth:AuthService,@Inject(MessagesService)private readonly service:MessagesService){}
 @Get()async inbox(@Req()req:Request,@Query()query:unknown){const {user}=await this.auth.require(req);const input=parse(pageSchema,query);return {...await this.service.inbox(user.personCode??'',input.page,input.unread==='true'),canSend:['admin','leader'].includes(user.role)};}
 @Get('candidates')async candidates(@Req()req:Request){const {user}=await this.auth.require(req);return {items:await this.service.candidates(user)};}
 @Get('sent')async sent(@Req()req:Request,@Query()query:unknown){const {user}=await this.auth.require(req);const input=parse(pageSchema,query);return this.service.sent(user,input.page);}
 @Get(':id')async detail(@Req()req:Request,@Param('id')value:string){const {user}=await this.auth.require(req);return this.service.detail(user,parse(z.coerce.number().int().positive(),value));}
 @Post('send')async send(@Req()req:Request,@Body()body:unknown){const {user}=await this.auth.require(req,{write:true});return this.service.send(user,parse(sendSchema,body));}
 @Post('read-all')async readAll(@Req()req:Request,@Body()body:unknown){const {user}=await this.auth.require(req,{write:true});parse(z.object({}).strict(),body);return this.service.read(user.personCode??'',null);}
 @Post(':id/read')async read(@Req()req:Request,@Param('id')value:string,@Body()body:unknown){const {user}=await this.auth.require(req,{write:true});parse(z.object({}).strict(),body);return this.service.read(user.personCode??'',parse(z.coerce.number().int().positive(),value));}
}
