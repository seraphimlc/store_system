import {Body,Controller,Get,Inject,Injectable,Post,Req} from '@nestjs/common';import type {Request} from 'express';import type {PoolClient} from 'pg';import {createHash} from 'node:crypto';import {z} from 'zod';import {Database} from './database';import {AuthService} from './auth';import {BusinessError} from './errors';import {planStationSequence,type SequenceLine,type SequenceStation,type SequenceSources} from '../../../packages/domain/src/station-sequence';import {sequenceReference} from '../../../packages/domain/src/sequence-reference';
const hash=(value:unknown)=>createHash('sha256').update(JSON.stringify(value)).digest('hex');
@Injectable()
export class StationSequenceService{
 constructor(@Inject(Database)private readonly db:Database){}
 private async admin(client:PoolClient,id:number,write=false){const actor=(await client.query(`SELECT username,display_name AS name FROM users WHERE id=$1 AND role='admin' AND is_active=true AND status IN('active','leave') ${write?'FOR SHARE':''}`,[id])).rows[0];if(!actor)throw new BusinessError('FORBIDDEN','仅管理员可更新沿线顺序',403);return actor;}
 private async state(client:PoolClient,sources:SequenceSources){const lines=(await client.query<SequenceLine>('SELECT id,name,operator,operator_short FROM bd_line ORDER BY id')).rows,stations=(await client.query<SequenceStation>('SELECT id,name,line_id,lat,lon,seq,along_km,seq_src FROM bd_station ORDER BY id')).rows,plan=planStationSequence(lines,stations,sources);return {plan,token:hash({sources,lines,stations})};}
 async preview(actorId:number,sources=sequenceReference){return this.db.transaction(async client=>{await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');await this.admin(client,actorId);const {plan,token}=await this.state(client,sources);return {token,summary:plan.summary,lines:plan.reports,licenses:plan.licenses};});}
 async apply(actorId:number,body:unknown,sources=sequenceReference){const input=z.object({token:z.string().regex(/^[a-f0-9]{64}$/),confirm:z.literal('UPDATE_STATION_SEQUENCE')}).strict().safeParse(body);if(!input.success)throw new BusinessError('VALIDATION','请先预览并确认沿线顺序更新',400);return this.db.transaction(async client=>{
  await client.query("SELECT pg_advisory_xact_lock(hashtext('station-assets'),0)");const actor=await this.admin(client,actorId,true),{plan,token}=await this.state(client,sources);if(token!==input.data.token)throw new BusinessError('SEQUENCE_CHANGED','线路或站点数据已变化，请重新预览',409);
  await client.query(`WITH desired AS(SELECT * FROM json_to_recordset($1::json) AS r("stationId" int,seq int,km float8,source text)) UPDATE bd_station s SET seq=r.seq,along_km=r.km,seq_src=r.source FROM desired r WHERE s.id=r."stationId" AND (s.seq IS DISTINCT FROM r.seq OR s.along_km IS DISTINCT FROM r.km OR s.seq_src IS DISTINCT FROM r.source)`,[JSON.stringify(plan.entries)]);
  await client.query("UPDATE bd_station SET seq=NULL,along_km=NULL,seq_src='' WHERE NOT(id=ANY($1::int[])) AND (seq IS NOT NULL OR along_km IS NOT NULL OR seq_src<>'')",[plan.entries.map(row=>row.stationId)]);
  if(plan.summary.changed||plan.summary.cleared)await client.query("INSERT INTO bd_log(domain,action,ref_id,ref_label,field,old_value,new_value,actor,actor_name,note,created_at) VALUES('station','update',NULL,'沿线顺序更新','seq/along_km/seq_src','',$1,$2,$3,'离线资料；每条线只使用一个顺序源；不改任务',now())",[JSON.stringify(plan.summary),actor.username,actor.name]);
  return {ok:true,summary:plan.summary,licenses:plan.licenses};
 });}
}
@Controller('station-sequence')
export class StationSequenceController{
 constructor(@Inject(AuthService)private readonly auth:AuthService,@Inject(StationSequenceService)private readonly service:StationSequenceService){}
 @Get('preview')async preview(@Req()req:Request){const {user}=await this.auth.require(req,{admin:true});return this.service.preview(user.id);}
 @Post()async apply(@Req()req:Request,@Body()body:unknown){const {user}=await this.auth.require(req,{admin:true,write:true});return this.service.apply(user.id,body);}
}
