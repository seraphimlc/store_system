import {Body,Controller,Get,Inject,Injectable,Param,Post,Query,Req} from '@nestjs/common';import type {Request} from 'express';import type {PoolClient,QueryResultRow} from 'pg';import {z} from 'zod';
import {randomUUID} from 'node:crypto';
import {assertMonthsOpen} from './financial-control';
import {Database} from './database';import {AuthService} from './auth';import {BusinessError} from './errors';import {SettlementSyncService} from './settlement-sync';import {monthRange} from './business-dates';
import {judgeSettlement,settlementPoints,type SettlementRecord,type ImportRules} from '../../../packages/domain/src/settlement';import {validMonth} from '../../../packages/domain/src/payroll';
interface Source extends SettlementRecord,QueryResultRow {code:string|null;status:string|null;excelRow:number}
interface ImportRow extends QueryResultRow {id:number;layout:{value_map?:{visible?:Record<string,string>};point_rules?:unknown[]}|null}
const sourceFields=`id,import_id AS "importId",store_id_raw AS "storeId",store_name_local_raw AS name,modified_raw AS modified,visible_raw AS visible,deploy_raw AS deploy,filtered_by_raw_id AS "filteredBy",submitter_code AS code,clean_status AS status,excel_row AS "excelRow"`;
function parse<T>(schema:z.ZodType<T>,value:unknown){const result=schema.safeParse(value);if(!result.success)throw new BusinessError('VALIDATION','文件、月份或申诉参数无效',400);return result.data;}
const id=z.coerce.number().int().positive();const month=z.string().refine(value=>validMonth(value)&&Number(value.slice(0,4))>=1&&Number(value.slice(0,4))<=9998);
@Injectable()
export class SettlementService{
 constructor(@Inject(Database)private readonly db:Database,@Inject(SettlementSyncService)private readonly sync: SettlementSyncService){}
 private async lock(client:PoolClient,actor?:number){if(actor!==undefined){const user=(await client.query("SELECT id FROM users WHERE id=$1 AND role='admin' AND status IN ('active','leave') AND is_active=true FOR SHARE",[actor])).rows[0];if(!user)throw new BusinessError('FORBIDDEN','管理员权限已变化，任务未执行',403);}await client.query("SELECT pg_advisory_xact_lock(hashtext('settlement-write'),0)");}
 private async lockMonths(client:PoolClient,months:string[]){await client.query("SELECT pg_advisory_xact_lock(hashtext('payroll-financial-write'),0)");for(const value of [...new Set(months)].filter(validMonth).sort())await client.query("SELECT pg_advisory_xact_lock(hashtext('payroll-month'),hashtext($1))",[value]);await assertMonthsOpen(client,months);}
 private async requireImport(client:PoolClient,identity:number){const row=(await client.query<ImportRow>('SELECT id,layout FROM imports WHERE id=$1 FOR UPDATE',[identity])).rows[0];if(!row)throw new BusinessError('NOT_FOUND','文件不存在',404);return row;}
 private async rules(client:PoolClient,identities:number[]){const rows=(await client.query<ImportRow>('SELECT id,layout FROM imports WHERE id=ANY($1::int[])',[identities])).rows;return new Map<number,ImportRules>(rows.map(row=>[row.id,{visible:row.layout?.value_map?.visible,pointRules:row.layout?.point_rules}]));}
 private async judge(client:PoolClient,identity:number,onlyMonth?:string){
  const targets=(await client.query<Source>(`SELECT ${sourceFields} FROM raw_records WHERE import_id=$1 AND ($2::text IS NULL OR left(modified_raw,7)=$2) ORDER BY excel_row,id`,[identity,onlyMonth??null])).rows;
  const months=[...new Set(targets.map(row=>(row.modified??'').slice(0,7)))];await assertMonthsOpen(client,months);
  const all=(await client.query<Source>(`SELECT ${sourceFields} FROM raw_records WHERE left(modified_raw,7)=ANY($1::text[]) OR (import_id=$2 AND $3::text IS NULL) ORDER BY id`,[months,identity,onlyMonth??null])).rows;
  const rules=await this.rules(client,[...new Set(all.map(row=>row.importId))]);const desired=new Set(targets.map(row=>row.id));
  const decisions=judgeSettlement(all,rules,identity).filter(row=>desired.has(row.id));
  await client.query(`UPDATE raw_records r SET clean_status=d.status,filter_reason=d.reason,confirm_state=d.confirmation,filtered_by_raw_id=d."filteredBy" FROM json_to_recordset($1::json) AS d(id int,status text,reason text,confirmation text,"filteredBy" int) WHERE r.id=d.id`,[JSON.stringify(decisions)]);
  const counts:Record<string,number>={};for(const row of decisions)counts[row.status]=(counts[row.status]??0)+1;return {rows:decisions.length,counts};
 }
 async judgeImport(identity:number,actor?:number){return this.db.transaction(async client=>{await this.lock(client,actor);await this.requireImport(client,identity);const result=await this.judge(client,identity);await client.query("UPDATE raw_records r SET clean_status='valid',filter_reason=NULL,filtered_by_raw_id=NULL,confirm_state='approved' FROM appeals a WHERE a.raw_record_id=r.id AND a.status='approved' AND r.import_id=$1",[identity]);return result;});}
 private async insertFormal(client:PoolClient,records:Source[],rules:Map<number,ImportRules>){
  const rows=records.map(row=>({import_id:row.importId,raw_record_id:row.id,person_code:row.code,store_id_raw:row.storeId??'',japan_date:(row.modified??'').slice(0,10),points:settlementPoints((row.modified??'').slice(0,10),row.deploy,row.visible??'',rules.get(row.importId)?.pointRules)})).filter(row=>row.points>0);
  if(rows.some(row=>!row.person_code))throw new BusinessError('FORMAL_PERSON_MISSING','有效记录缺少人员编号，已回滚，请核对源文件',409);
  for(let i=0;i<rows.length;i+=1000)await client.query(`INSERT INTO formal_records(import_id,raw_record_id,person_code,store_id_raw,japan_date,points,created_at) SELECT import_id,raw_record_id,person_code,store_id_raw,japan_date,points,now() FROM json_populate_recordset(NULL::formal_records,$1::json)`,[JSON.stringify(rows.slice(i,i+1000))]);
  return rows.length;
 }
 async finalize(identity:number,actor?:number){return this.db.transaction(async client=>{
  await this.lock(client,actor);const imp=await this.requireImport(client,identity);
  const pending=(await client.query('SELECT id FROM appeals WHERE import_id=$1 AND status=\'pending\' LIMIT 1',[identity])).rows[0];if(pending)throw new BusinessError('APPEALS_PENDING','该文件仍有未处理申诉，请处理后入表',409);
  if((await client.query('SELECT id FROM raw_records WHERE import_id=$1 AND clean_status IS NULL LIMIT 1',[identity])).rowCount)throw new BusinessError('JUDGEMENT_REQUIRED','文件仍有未判定记录，请先执行判定',409);
  const records=(await client.query<Source>(`SELECT ${sourceFields} FROM raw_records WHERE import_id=$1 AND clean_status='valid' ORDER BY id`,[identity])).rows;
  const previous=(await client.query<{month:string}>('SELECT DISTINCT to_char(japan_date,\'YYYY-MM\') AS month FROM formal_records WHERE import_id=$1 AND japan_date IS NOT NULL',[identity])).rows;
  const months=[...new Set([...previous.map(row=>row.month),...records.map(row=>(row.modified??'').slice(0,7))])].filter(validMonth).sort();await this.lockMonths(client,months);
  await client.query('DELETE FROM formal_records WHERE import_id=$1',[identity]);
  const added=await this.insertFormal(client,records,new Map([[identity,{pointRules:imp.layout?.point_rules}]]));
  const synchronized=[];for(const month of months)synchronized.push(await this.sync.sync(client,month));
  return {ok:true,added,months,synchronized};
 });}
 async rebuild(value:string,actor?:number){return this.db.transaction(client=>this.rebuildTransaction(client,value,actor));}
 async rebuildTransaction(client:PoolClient,value:string,actor?:number){
  await this.lock(client,actor);await this.lockMonths(client,[value]);const {start,end}=monthRange(value);
  const sources=(await client.query<{import_id:number}>('SELECT DISTINCT import_id FROM raw_records WHERE left(modified_raw,7)=$1',[value])).rows;
  const before=(await client.query<{count:number;points:string}>('SELECT count(*)::int AS count,coalesce(sum(points),0)::text AS points FROM formal_records WHERE japan_date BETWEEN $1 AND $2',[start,end])).rows[0];
  if(!sources.length&&before.count)throw new BusinessError('REBUILD_SOURCE_MISSING','该月没有源记录但存在正式数据，拒绝重算以免清空',409);
  const pending=(await client.query(`SELECT a.id FROM appeals a JOIN raw_records r ON r.id=a.raw_record_id WHERE a.status='pending' AND left(r.modified_raw,7)=$1 LIMIT 1`,[value])).rows[0];if(pending)throw new BusinessError('APPEALS_PENDING','该月仍有未处理申诉，请处理后重算',409);
  for(const source of sources){await this.requireImport(client,source.import_id);await this.judge(client,source.import_id,value);}
  await client.query(`UPDATE raw_records r SET clean_status='valid',filter_reason=NULL,filtered_by_raw_id=NULL,confirm_state='approved' FROM appeals a WHERE a.raw_record_id=r.id AND a.status='approved' AND left(r.modified_raw,7)=$1`,[value]);
  const byPoints=(await client.query<{points:number;n:number}>('SELECT points,count(*)::int AS n FROM formal_records WHERE japan_date BETWEEN $1 AND $2 GROUP BY points',[start,end])).rows;
  await client.query('DELETE FROM rebuild_snapshots WHERE month=$1',[value]);await client.query('INSERT INTO rebuild_snapshots(month,payload_json,row_count,created_at) VALUES($1,$2,$3,now())',[value,JSON.stringify({kind:'digest',month:value,count:before.count,points_sum:Number(before.points),by_points:Object.fromEntries(byPoints.map(row=>[row.points,row.n]))}),before.count]);
  const configured=(process.env.DEDUP_SUB_STORES??'0101047092026031200555097|0202047092026032480084411|0101047092026081903348200|0101047092026060970019734').split('|').filter(Boolean);
  const merged=(await client.query<{id:string}>('SELECT DISTINCT s.store_id_raw AS id FROM store_entities s JOIN store_merge_logs m ON m.entity_id=s.id WHERE s.master_id<>s.id')).rows.map(row=>row.id);const subs=[...new Set([...configured,...merged])];
  await client.query(`UPDATE raw_records SET clean_status='from_sub',filter_reason='from_sub',confirm_state='auto_ok' WHERE left(modified_raw,7)=$1 AND clean_status='valid' AND store_id_raw=ANY($2::text[])`,[value,subs]);
  const records=(await client.query<Source>(`SELECT ${sourceFields} FROM raw_records WHERE left(modified_raw,7)=$1 AND clean_status='valid' ORDER BY id`,[value])).rows;
  await client.query('DELETE FROM formal_records WHERE japan_date BETWEEN $1 AND $2',[start,end]);
  const added=await this.insertFormal(client,records,await this.rules(client,sources.map(row=>row.import_id)));const synced=await this.sync.sync(client,value);
  const after=(await client.query<{count:number;points:string}>('SELECT count(*)::int AS count,coalesce(sum(points),0)::text AS points FROM formal_records WHERE japan_date BETWEEN $1 AND $2',[start,end])).rows[0];
  return {ok:true,month:value,added,files:sources.map(row=>row.import_id),formalBefore:before.count,formalAfter:after.count,pointsBefore:Number(before.points),pointsAfter:Number(after.points),synchronized:synced};

 }
 async appeal(rawId:number,code:string,reason:string){return this.db.transaction(async client=>{
  await this.lock(client);const record=(await client.query<{import_id:number;submitter_code:string;clean_status:string;month:string}>('SELECT import_id,submitter_code,clean_status,left(modified_raw,7) AS month FROM raw_records WHERE id=$1 FOR UPDATE',[rawId])).rows[0];
  if(!record||record.submitter_code!==code)throw new BusinessError('NOT_FOUND','记录不存在或不属于你',404);
  await assertMonthsOpen(client,[record.month]);
  if(!['master_late','from_sub'].includes(record.clean_status))throw new BusinessError('APPEAL_NOT_ALLOWED','该条不可申诉，仅疑似重复或从档归并可申诉',409);
  if((await client.query('SELECT id FROM appeals WHERE raw_record_id=$1',[rawId])).rowCount)throw new BusinessError('APPEAL_EXISTS','该条已申诉过',409);
  const row=(await client.query<{id:number}>('INSERT INTO appeals(raw_record_id,import_id,submitter_code,reason,status,created_at) VALUES($1,$2,$3,$4,\'pending\',now()) RETURNING id',[rawId,record.import_id,code,reason])).rows[0];await client.query('UPDATE raw_records SET confirm_state=\'disputed\' WHERE id=$1',[rawId]);return {id:row.id};
 });}
 async resolve(identity:number,decision:'accept'|'reject',actor:number){return this.db.transaction(async client=>{
  await this.lock(client);const appeal=(await client.query<{raw_record_id:number;status:string}>('SELECT raw_record_id,status FROM appeals WHERE id=$1 FOR UPDATE',[identity])).rows[0];if(!appeal)throw new BusinessError('NOT_FOUND','申诉不存在',404);if(appeal.status!=='pending')throw new BusinessError('APPEAL_ALREADY_HANDLED','申诉已处理，不能重复裁定',409);
  const source=(await client.query<{month:string}>('SELECT left(modified_raw,7) AS month FROM raw_records WHERE id=$1',[appeal.raw_record_id])).rows[0];await assertMonthsOpen(client,[source?.month??'']);
  if(decision==='accept')await client.query("UPDATE raw_records SET clean_status='valid',filter_reason=NULL,confirm_state='approved' WHERE id=$1",[appeal.raw_record_id]);else await client.query("UPDATE raw_records SET confirm_state='auto_ok' WHERE id=$1",[appeal.raw_record_id]);
  await client.query('UPDATE appeals SET status=$1,handled_by=$2,handled_at=now() WHERE id=$3',[decision==='accept'?'approved':'rejected',actor,identity]);return {ok:true};
 });}
 async imports(){return {items:(await this.db.query(`SELECT i.id,i.file_name AS "fileName",i.status,i.uploaded_at AS "uploadedAt",i.parsed_rows AS "parsedRows",(SELECT count(*)::int FROM raw_records r WHERE r.import_id=i.id AND r.clean_status IS NULL) AS "unjudgedRows",(SELECT count(*)::int FROM formal_records f WHERE f.import_id=i.id) AS "formalRows",(SELECT count(*)::int FROM appeals a WHERE a.import_id=i.id AND a.status='pending') AS "pendingAppeals" FROM imports i ORDER BY i.id DESC LIMIT 100`)).rows};}
 async records(identity:number,code:string|null,page:number){
  const result=(await this.db.query(`WITH matching AS (SELECT r.id,r.import_id AS "importId",r.store_name_local_raw AS name,r.store_id_raw AS "storeId",r.modified_raw AS modified,r.submitter_code AS code,r.visible_raw AS visible,r.deploy_raw AS deploy,r.clean_status AS status,r.confirm_state AS confirmation,a.id AS "appealId",a.status AS "appealStatus",a.reason AS "appealReason" FROM raw_records r LEFT JOIN LATERAL(SELECT id,status,reason FROM appeals WHERE raw_record_id=r.id ORDER BY id DESC LIMIT 1) a ON true WHERE ($1::int IS NULL OR r.import_id=$1) AND ($2::text IS NULL OR r.submitter_code=$2)),paged AS (SELECT * FROM matching ORDER BY id LIMIT 30 OFFSET $3) SELECT (SELECT count(*)::int FROM matching) AS total,coalesce((SELECT json_agg(paged) FROM paged),'[]'::json) AS items`,[identity||null,code,(page-1)*30])).rows[0];return {...result,page,pageSize:30};
 }
}
@Controller('settlement')
export class SettlementController{
 constructor(@Inject(AuthService)private readonly auth:AuthService,@Inject(SettlementService)private readonly service:SettlementService,@Inject('SETTLEMENT_JOBS')private readonly jobs:{enqueue:(action:'judge'|'finalize'|'rebuild',target:string,actor:number,requestId:string)=>Promise<unknown>}){}
 @Get('imports')async imports(@Req()req:Request){await this.auth.require(req,{admin:true});return this.service.imports();}
 @Get('records')async records(@Req()req:Request,@Query()query:unknown){const {user}=await this.auth.require(req);const input=parse(z.object({importId:id.optional(),page:z.coerce.number().int().min(1).max(10000).default(1)}).strict(),query);if(user.role!=='admin'&&!user.personCode)throw new BusinessError('PERSON_NOT_FOUND','账号未绑定员工编号',400);return this.service.records(input.importId??0,user.role==='admin'?null:user.personCode,input.page);}
 @Post('imports/:id/judge')async judge(@Req()req:Request,@Param('id')value:string,@Body()body:unknown){const {user}=await this.auth.require(req,{admin:true,write:true});const input=parse(z.object({requestId:z.string().uuid().optional()}).strict(),body);return this.jobs.enqueue('judge',String(parse(id,value)),user.id,input.requestId??randomUUID());}
 @Post('imports/:id/finalize')async finalize(@Req()req:Request,@Param('id')value:string,@Body()body:unknown){const {user}=await this.auth.require(req,{admin:true,write:true});const input=parse(z.object({confirm:z.literal('FINALIZE_IMPORT'),requestId:z.string().uuid().optional()}).strict(),body);return this.jobs.enqueue('finalize',String(parse(id,value)),user.id,input.requestId??randomUUID());}
 @Post('months/rebuild')async rebuild(@Req()req:Request,@Body()body:unknown){const {user}=await this.auth.require(req,{admin:true,write:true});const input=parse(z.object({month,confirm:z.literal('REBUILD_MONTH'),requestId:z.string().uuid().optional()}).strict(),body);return this.jobs.enqueue('rebuild',input.month,user.id,input.requestId??randomUUID());}
 @Post('appeals')async appeal(@Req()req:Request,@Body()body:unknown){const {user}=await this.auth.require(req,{write:true});if(!user.personCode)throw new BusinessError('PERSON_NOT_FOUND','账号未绑定员工编号',400);const input=parse(z.object({rawId:id,reason:z.string().trim().max(2000)}).strict(),body);return this.service.appeal(input.rawId,user.personCode,input.reason);}
 @Post('appeals/:id/resolve')async resolve(@Req()req:Request,@Param('id')value:string,@Body()body:unknown){const {user}=await this.auth.require(req,{admin:true,write:true});const input=parse(z.object({decision:z.enum(['accept','reject'])}).strict(),body);return this.service.resolve(parse(id,value),input.decision,user.id);}
}
