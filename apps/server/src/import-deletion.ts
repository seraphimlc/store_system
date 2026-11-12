import 'reflect-metadata';import {Body,Controller,Get,Inject,Injectable,Param,Post,Req} from '@nestjs/common';import type {Request} from 'express';import type {PoolClient} from 'pg';import {z} from 'zod';import {createHash} from 'node:crypto';import {access,readFile,unlink} from 'node:fs/promises';import {join,resolve} from 'node:path';
import {Database} from './database';import {AuthService} from './auth';import {BusinessError} from './errors';import {SettlementService} from './settlement';import {SettlementSyncService} from './settlement-sync';import {ReportComparisonService} from './report-comparison';import {assertMonthsOpen,requireAdmin} from './financial-control';import {privateUploadPath} from './private-files';import {DurableQueue} from '../../worker/src/queue';import {loadConfig} from './config';
interface File {id:number;file_name:string;file_sha256:string;stored_path:string;status:string;layout:{parse_request?:string;upload_request?:string}|null}
interface DeleteTask {id:number;actor:number;requestId:string;token:string;path:string}
const taskSchema=z.object({id:z.number().int().positive(),actor:z.number().int().positive(),requestId:z.string().uuid(),token:z.string().regex(/^[a-f0-9]{64}$/),path:z.string().max(4096)}).strict();
@Injectable()
export class ImportDeletionService{
 readonly queue:DurableQueue;
 constructor(@Inject(Database)private readonly db:Database,@Inject(SettlementService)private readonly settlement:SettlementService){this.queue=new DurableQueue(resolve(process.env.NEXT_TASK_DIRECTORY||'data/refactor/jobs'));}
 private async active(file:File){if(file.status==='uploaded')throw new BusinessError('IMPORT_BUSY','文件正在等待解析，请完成或处理失败任务后再删除',409);for(const request of [file.layout?.parse_request,file.layout?.upload_request]){if(!request||!z.string().uuid().safeParse(request).success)continue;for(const state of ['queued','running'])try{await access(join(this.queue.directory,state,`import-${file.id}-${request}.json`));throw new BusinessError('IMPORT_BUSY','文件处理任务仍在运行，不能同时删除',409);}catch(error){if(error instanceof BusinessError)throw error;if((error as NodeJS.ErrnoException).code!=='ENOENT')throw error;}}}
 private async state(client:PoolClient,id:number,lock=false){
  const file=(await client.query<File>(`SELECT id,file_name,file_sha256,stored_path,status,layout FROM imports WHERE id=$1${lock?' FOR UPDATE':''}`,[id])).rows[0];if(!file)return null;
  const rows=(await client.query<{n:number;maximum:number|null;digest:string}>(`SELECT count(*)::int AS n,max(id) AS maximum,md5(coalesce(string_agg(concat_ws('|',id,clean_status,confirm_state,filtered_by_raw_id,modified_raw,submitter_code,visible_raw,deploy_raw),'\n' ORDER BY id),'')) AS digest FROM raw_records WHERE import_id=$1`,[id])).rows[0];
  const formal=(await client.query<{n:number;points:string;maximum:number|null}>('SELECT count(*)::int AS n,coalesce(sum(points),0)::text AS points,max(id) AS maximum FROM formal_records WHERE import_id=$1',[id])).rows[0];
  const appeals=(await client.query<{status:string;n:number}>('SELECT status,count(*)::int AS n FROM appeals WHERE import_id=$1 GROUP BY status ORDER BY status',[id])).rows;
  const months=(await client.query<{month:string}>(`SELECT DISTINCT month FROM (SELECT left(modified_raw,7) AS month FROM raw_records WHERE import_id=$1 UNION ALL SELECT to_char(japan_date,'YYYY-MM') FROM formal_records WHERE import_id=$1) dates WHERE month~'^\\d{4}-(0[1-9]|1[0-2])$' ORDER BY month`,[id])).rows.map(row=>row.month);
  const token=createHash('sha256').update(JSON.stringify({id:file.id,sha:file.file_sha256,status:file.status,rows,formal,appeals,months})).digest('hex');
  return {file,rows,formal,appeals,months,token};
 }
 async preview(id:number){return this.db.transaction(async client=>{await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');const state=await this.state(client,id);if(!state)throw new BusinessError('NOT_FOUND','文件不存在',404);await this.active(state.file);await assertMonthsOpen(client,state.months);return {id,fileName:state.file.file_name,rawRows:state.rows.n,formalRows:state.formal.n,points:Number(state.formal.points),appeals:state.appeals.reduce((sum,row)=>sum+row.n,0),months:state.months,token:state.token};});}
 async enqueue(id:number,actor:number,token:string,requestId:string){
  const jobId=`file-delete-${id}-${requestId}`;let existing:DeleteTask|undefined;
  try{existing=JSON.parse(await readFile(join(this.queue.directory,jobId+'.identity'),'utf8')).payload;}catch(error){if((error as NodeJS.ErrnoException).code!=='ENOENT')throw error;}
  if(existing){if(existing.id!==id||existing.actor!==actor||existing.token!==token)throw new BusinessError('REQUEST_ID_CONFLICT','提交标识已被其他删除操作使用',409);return {jobId,reused:true};}
  const preview=await this.preview(id);if(preview.token!==token)throw new BusinessError('IMPORT_DELETE_CHANGED','文件判定或正式记录已变化，请重新预览再删除',409);
  const file=(await this.db.query<File>('SELECT stored_path FROM imports WHERE id=$1',[id])).rows[0];if(!file)throw new BusinessError('NOT_FOUND','文件不存在',404);
  const payload:DeleteTask={id,actor,token,requestId,path:file.stored_path};
  try{await this.queue.enqueue('import.delete',payload,jobId);}catch(error){if((error as NodeJS.ErrnoException).code!=='EEXIST')throw new BusinessError('IMPORT_QUEUE_FAILED','删除任务入队失败，请重试',503);const previous=JSON.parse(await readFile(join(this.queue.directory,jobId+'.identity'),'utf8')).payload;if(JSON.stringify(previous)!==JSON.stringify(payload))throw new BusinessError('REQUEST_ID_CONFLICT','提交标识已被其他删除参数使用',409);}
  return {jobId,reused:false};
 }
 async status(jobId:string){if(!/^file-delete-\d+-[a-f0-9-]{36}$/.test(jobId))throw new BusinessError('VALIDATION','删除任务编号无效',400);for(const state of ['queued','running','done','failed'])try{const task=JSON.parse(await readFile(join(this.queue.directory,state,jobId+'.json'),'utf8'));return {jobId,status:state==='queued'?'pending':state,result:state==='done'||state==='failed'?task.result:undefined};}catch(error){if((error as NodeJS.ErrnoException).code!=='ENOENT')throw error;}throw new BusinessError('NOT_FOUND','删除任务不存在',404);}
 private async removeOriginal(path:string){
  const references=(await this.db.query<{n:number}>(`SELECT (SELECT count(*) FROM imports WHERE stored_path=$1)+(SELECT count(*) FROM recon_tasks WHERE params->>'path'=$1) AS n`,[path])).rows[0].n;if(Number(references)>0)return {originalRemoved:false,originalRetained:'referenced'};
  let file:string;try{file=privateUploadPath(path);}catch{return {originalRemoved:false,originalRetained:'outside-configured-private-directory'};}
  try{await unlink(file);return {originalRemoved:true};}catch(error){if((error as NodeJS.ErrnoException).code==='ENOENT')return {originalRemoved:true};return {originalRemoved:false,originalRetained:'cleanup-failed'};}
 }
 async process(input:unknown){const parsed=taskSchema.safeParse(input);if(!parsed.success)return {ok:false,code:'IMPORT_DELETE_INVALID',message:'删除任务参数无效'};const task=parsed.data;
  try{
   const result=await this.db.transaction(async client=>{
    await requireAdmin(client,task.actor);await client.query("SELECT pg_advisory_xact_lock(hashtext('settlement-write'),0)");await client.query("SELECT pg_advisory_xact_lock(hashtext('payroll-financial-write'),0)");
    const state=await this.state(client,task.id,true);if(!state)return {ok:true,id:task.id,alreadyDeleted:true,months:[] as string[]};
    if(state.file.stored_path!==task.path||state.token!==task.token)throw new BusinessError('IMPORT_DELETE_CHANGED','文件判定或正式记录已变化，删除已取消，请重新预览',409);
    await this.active(state.file);await assertMonthsOpen(client,state.months);
    const affectedStores=(await client.query<{sid:string}>('SELECT DISTINCT btrim(store_id_raw) AS sid FROM raw_records WHERE import_id=$1',[task.id])).rows.map(row=>row.sid);
    // Keep all store master IDs/addresses, people, payments and settlement links. Detach only this source's anchors.
    await client.query('UPDATE store_entities SET raw_data_id=NULL WHERE raw_data_id IN(SELECT id FROM raw_records WHERE import_id=$1)',[task.id]);
    await client.query('UPDATE store_entities SET master_raw_data_id=NULL WHERE master_raw_data_id IN(SELECT id FROM raw_records WHERE import_id=$1)',[task.id]);
    await client.query('UPDATE raw_records SET filtered_by_raw_id=NULL WHERE import_id<>$1 AND filtered_by_raw_id IN(SELECT id FROM raw_records WHERE import_id=$1)',[task.id]);
    await client.query('DELETE FROM appeals WHERE import_id=$1 OR raw_record_id IN(SELECT id FROM raw_records WHERE import_id=$1)',[task.id]);
    await client.query('DELETE FROM formal_records WHERE import_id=$1 OR raw_record_id IN(SELECT id FROM raw_records WHERE import_id=$1)',[task.id]);
    for(const table of ['clean_records','confirm_tasks','manual_decisions']){const exists=(await client.query<{name:string|null}>('SELECT to_regclass($1)::text AS name',['public.'+table])).rows[0].name;if(!exists)continue;await client.query(`DELETE FROM ${table} WHERE import_id=$1${table==='clean_records'?' OR raw_record_id IN(SELECT id FROM raw_records WHERE import_id=$1)':''}`,[task.id]);}
    await client.query('DELETE FROM raw_records WHERE import_id=$1',[task.id]);await client.query('UPDATE persons SET first_seen_import_id=NULL WHERE first_seen_import_id=$1',[task.id]);await client.query('UPDATE store_entities SET first_seen_import_id=NULL WHERE first_seen_import_id=$1',[task.id]);await client.query('UPDATE recon_tasks SET source_import_id=NULL WHERE source_import_id=$1',[task.id]);await client.query('DELETE FROM imports WHERE id=$1',[task.id]);
    const rebuilt=[];for(const month of state.months)rebuilt.push(await this.settlement.rebuildTransaction(client,month,task.actor));
    // Restore anchors only for surviving sources of the affected stores; never delete the master universe.
    await client.query(`WITH missing AS(SELECT e.id,e.store_id_raw FROM store_entities e WHERE e.raw_data_id IS NULL AND e.store_id_raw=ANY($1::text[]) AND EXISTS(SELECT 1 FROM raw_records r WHERE btrim(r.store_id_raw)=e.store_id_raw)),anchors AS(SELECT DISTINCT ON(m.id) m.id,r.id AS raw FROM missing m JOIN raw_records r ON btrim(r.store_id_raw)=m.store_id_raw WHERE btrim(coalesce(r.visible_raw,''))<>'' ORDER BY m.id,coalesce(nullif(r.modified_raw,''),'z'),r.id) UPDATE store_entities e SET raw_data_id=a.raw FROM anchors a WHERE e.id=a.id`,[affectedStores]);
    return {ok:true,id:task.id,alreadyDeleted:false,months:state.months,deletedRawRows:state.rows.n,deletedFormalRows:state.formal.n,rebuilt};
   });
   return {...result,...await this.removeOriginal(task.path)};
  }catch(error){if(error instanceof BusinessError)return {ok:false,id:task.id,code:error.code,message:error.message};throw error;}
 }
}
@Controller('imports')
export class ImportDeletionController{
 constructor(@Inject(AuthService)private readonly auth:AuthService,@Inject(ImportDeletionService)private readonly service:ImportDeletionService){}
 @Get(':id/deletion-preview')async preview(@Req()req:Request,@Param('id')id:string){await this.auth.require(req,{admin:true});const input=z.coerce.number().int().positive().safeParse(id);if(!input.success)throw new BusinessError('VALIDATION','文件编号无效',400);return this.service.preview(input.data);}
 @Post(':id/delete')async remove(@Req()req:Request,@Param('id')id:string,@Body()body:unknown){const {user}=await this.auth.require(req,{admin:true,write:true});const target=z.coerce.number().int().positive().safeParse(id),input=z.object({token:z.string().regex(/^[a-f0-9]{64}$/),requestId:z.string().uuid(),confirm:z.literal('DELETE_IMPORT')}).strict().safeParse(body);if(!target.success||!input.success)throw new BusinessError('VALIDATION','删除确认参数无效',400);return this.service.enqueue(target.data,user.id,input.data.token,input.data.requestId);}
 @Get('deletion-jobs/:id')async status(@Req()req:Request,@Param('id')id:string){await this.auth.require(req,{admin:true});return this.service.status(id);}
}
export async function runImportDeletionTask(input:unknown){const db=new Database(loadConfig());try{return await new ImportDeletionService(db,new SettlementService(db,new SettlementSyncService(db,new ReportComparisonService(db)))).process(input);}finally{await db.onModuleDestroy();}}
