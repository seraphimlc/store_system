import {aiLoginCandidates} from './login-name-model';
import 'reflect-metadata';
import {Body,CanActivate,Controller,ExecutionContext,Get,Inject,Injectable,Param,Post,Req,Res,UploadedFile,UseGuards,UseInterceptors} from '@nestjs/common';
import {FileInterceptor} from '@nestjs/platform-express';import {diskStorage} from 'multer';import type {Request,Response} from 'express';import type {PoolClient} from 'pg';
import {z} from 'zod';import {randomUUID} from 'node:crypto';import {mkdir,readFile,unlink,open,access} from 'node:fs/promises';import {createReadStream} from 'node:fs';import {createInterface} from 'node:readline';import {resolve,join} from 'node:path';
import {assertMonthsOpen} from './financial-control';
import {AuthService} from './auth';import {Database} from './database';import {BusinessError} from './errors';import {uploadRoot,privateUploadPath,readUploadPath,safeUploadName,storeUpload} from './private-files';import {parseVisitWorkbookAdaptive} from './visit-layout-model';
import {parseVisitWorkbook,VisitParseError,parseSubmitter,type ParsedVisit,type VisitLayout,type WorkbookResult} from './visit-workbook';
import {EmployeeService} from './employee-service';import {passwordWork} from './password-work';import {hash} from '@node-rs/argon2';import {StoreMasterService} from './store-master';
import {SettlementService} from './settlement';import {SettlementSyncService} from './settlement-sync';import {ReportComparisonService} from './report-comparison';import {DurableQueue} from '../../worker/src/queue';import {loadConfig} from './config';import type {ReportModel} from './report-model';
import {ReportAnalysisService} from './report-analysis';
const coordinate=z.number().int().min(1).max(1000);const layoutSchema=z.object({sheet_name:z.string().max(200).optional(),header_row:z.number().int().min(1).max(20).optional(),cols:z.object({store_id:coordinate,store_name:coordinate,modified_time:coordinate,submitter:coordinate,visible:coordinate,deploy:coordinate}).partial().strict().optional(),value_map:z.object({visible:z.record(z.string().max(100),z.enum(['candidate','blank'])).optional(),deploy:z.record(z.string().max(100),z.number().int().min(0).max(9)).optional()}).strict().optional(),point_rules:z.array(z.object({visible:z.array(z.string().max(100)).max(100).nullable().optional(),deploy:z.array(z.string().max(100)).max(100).nullable().optional(),points:z.number().int().min(0).max(9)}).strict()).max(100).optional()}).strict();
interface ImportFile {id:number;file_name:string;stored_path:string;status:string;layout:VisitLayout|null;uploaded_by:number}
interface ImportTask {id:number;actor:number;requestId:string;reparse:boolean;layout?:VisitLayout}
const taskSchema=z.object({id:z.number().int().positive(),actor:z.number().int().positive(),requestId:z.string().uuid(),reparse:z.boolean(),layout:layoutSchema.optional()}).strict();
function parse<T>(schema:z.ZodType<T>,input:unknown){const value=schema.safeParse(input);if(!value.success)throw new BusinessError('VALIDATION','文件布局或提交参数无效',400);return value.data;}
@Injectable()
export class AdminUploadGuard implements CanActivate{
 constructor(@Inject(AuthService)private readonly auth:AuthService){}
 async canActivate(context:ExecutionContext){await this.auth.require(context.switchToHttp().getRequest<Request>(),{admin:true,write:true});return true;}
}
@Injectable()
export class VisitImportsService{
 readonly queue:DurableQueue;
 constructor(@Inject(Database)private readonly db:Database,@Inject(EmployeeService)private readonly employees:EmployeeService,@Inject(SettlementService)private readonly settlement:SettlementService){this.queue=new DurableQueue(resolve(process.env.NEXT_TASK_DIRECTORY||'data/refactor/jobs'));}
 async enqueue(task:ImportTask){const jobId=`import-${task.id}-${task.requestId}`;try{await this.queue.enqueue('import.visit',task,jobId);}catch(error){if((error as NodeJS.ErrnoException).code!=='EEXIST'){await this.db.query("UPDATE imports SET status='failed',errors=$1 WHERE id=$2 AND status='uploaded'",[JSON.stringify(['后台任务入队失败，请重试解析']),task.id]);throw new BusinessError('IMPORT_QUEUE_FAILED','解析任务未能入队，请重试解析',503);}const saved=JSON.parse(await readFile(join(this.queue.directory,jobId+'.identity'),'utf8'));if(JSON.stringify(saved.payload)!==JSON.stringify(task))throw new BusinessError('REQUEST_ID_CONFLICT','提交标识已被其他解析参数使用',409);}return {jobId};}
 async upload(filename:string,path:string,actor:number){
  const name=safeUploadName(filename);if(!name.toLowerCase().endsWith('.xlsx'))throw new BusinessError('UPLOAD_EXTENSION','仅支持.xlsx文件',400);
  const stored=await storeUpload(path),requestId=randomUUID();let identity:number;
  try{identity=await this.db.transaction(async client=>{
   await client.query("SELECT pg_advisory_xact_lock(hashtext('import-sha'),hashtext($1))",[stored.sha]);
   const old=(await client.query('SELECT id FROM imports WHERE file_sha256=$1',[stored.sha])).rows[0];if(old)throw new BusinessError('DUPLICATE_UPLOAD','该文件已存在（同内容SHA256）',409);
   const user=(await client.query("SELECT id FROM users WHERE id=$1 AND role='admin' AND status IN ('active','leave') AND is_active=true FOR SHARE",[actor])).rows[0];if(!user)throw new BusinessError('FORBIDDEN','管理员权限已变化',403);
   return (await client.query<{id:number}>(`INSERT INTO imports(file_name,file_sha256,file_size,stored_path,uploaded_by,uploaded_at,parsed_sheets,ignored_sheets,total_rows,parsed_rows,status,warnings,errors,created_at,layout) VALUES($1,$2,$3,$4,$5,now(),'[]','[]',0,0,'uploaded','[]','[]',now(),$6) RETURNING id`,[name,stored.sha,stored.size,stored.path,actor,JSON.stringify({upload_request:requestId})])).rows[0].id;
  });}catch(error){if((error as {code?:string}).code==='23505')throw new BusinessError('DUPLICATE_UPLOAD','该文件已存在（同内容SHA256）',409);throw error;}
  return {id:identity,...await this.enqueue({id:identity,actor,requestId,reparse:false})};
 }
 async reparse(id:number,actor:number,layout:VisitLayout|undefined,requestId:string){
  const file=(await this.db.query<ImportFile>('SELECT id,status,stored_path,layout,file_name,uploaded_by FROM imports WHERE id=$1',[id])).rows[0];if(!file)throw new BusinessError('NOT_FOUND','文件不存在',404);
  if((await this.db.query('SELECT id FROM formal_records WHERE import_id=$1 UNION ALL SELECT id FROM appeals WHERE import_id=$1 LIMIT 1',[id])).rowCount)throw new BusinessError('REPARSE_HAS_HISTORY','文件已有正式记录或申诉，请先处理历史引用；不能直接替换源记录',409);
  return {id,...await this.enqueue({id,actor,requestId,reparse:true,...layout?{layout}:{}})};
 }
 async reconcile(){
  const rows=(await this.db.query<ImportFile&{layout:VisitLayout&{upload_request?:string}}>("SELECT id,stored_path,uploaded_by,status,layout FROM imports WHERE status='uploaded' ORDER BY id LIMIT 1000")).rows;
  for(const row of rows){const requestId=row.layout?.upload_request;if(!requestId||!z.string().uuid().safeParse(requestId).success)continue;
   try{readUploadPath(row.stored_path);}catch{continue;}
   let isFailed=false;try{await access(join(this.queue.directory,'failed',`import-${row.id}-${requestId}.json`));isFailed=true;}catch{}
   if(isFailed){await this.db.query("UPDATE imports SET status='failed',errors=$1 WHERE id=$2 AND status='uploaded'",[JSON.stringify(['解析任务中断，请查看诊断并重试解析']),row.id]);continue;}
   await this.enqueue({id:row.id,actor:row.uploaded_by,requestId,reparse:false});
  }
 }
 async detail(id:number){const file=(await this.db.query('SELECT id,file_name AS "fileName",status,format,header_row AS "headerRow",data_start_row AS "dataStartRow",parsed_sheets AS "parsedSheets",ignored_sheets AS "ignoredSheets",total_rows AS "totalRows",parsed_rows AS "parsedRows",layout,warnings,errors FROM imports WHERE id=$1',[id])).rows[0];if(!file)throw new BusinessError('NOT_FOUND','文件不存在',404);return file;}
 async original(id:number){const file=(await this.db.query<{file_name:string;stored_path:string}>('SELECT file_name,stored_path FROM imports WHERE id=$1',[id])).rows[0];if(!file)throw new BusinessError('NOT_FOUND','文件不存在',404);return {name:file.file_name,path:readUploadPath(file.stored_path)};}
 async status(jobId:string){if(!/^import-\d+-[a-f0-9-]{36}$/.test(jobId))throw new BusinessError('VALIDATION','解析任务编号无效',400);for(const state of ['queued','running','done','failed'])try{const task=JSON.parse(await readFile(join(this.queue.directory,state,jobId+'.json'),'utf8'));return {jobId,status:state==='queued'?'pending':state,result:state==='done'||state==='failed'?task.result:undefined};}catch(error){if((error as NodeJS.ErrnoException).code!=='ENOENT')throw error;}throw new BusinessError('NOT_FOUND','解析任务不存在',404);}
 private async writeRows(client:PoolClient,id:number,path:string){
  const reader=createInterface({input:createReadStream(path),crlfDelay:Infinity});let rows:ParsedVisit[]=[];
  const flush=async()=>{if(!rows.length)return;const values=rows.map(row=>({...row,import_id:id,visible_raw:row.visible_raw||null,deploy_raw:row.deploy_raw||null}));await client.query(`INSERT INTO raw_records(import_id,sheet_name,excel_row,store_id_raw,store_name_local_raw,store_name_en_raw,modified_raw,submitter_raw,submitter_code,record_id_raw,visible_raw,deploy_raw,original_row,created_at,confirm_state)
   SELECT import_id,sheet_name,excel_row,store_id_raw,store_name_local_raw,store_name_en_raw,modified_raw,submitter_raw,submitter_code,record_id_raw,visible_raw,deploy_raw,original_row,now(),'pending' FROM json_populate_recordset(NULL::raw_records,$1::json)`,[JSON.stringify(values)]);rows=[];};
  for await(const line of reader){rows.push(JSON.parse(line));if(rows.length===1000)await flush();}await flush();
 }
 private async buildPeople(client:PoolClient,id:number,result:WorkbookResult,passwordHashes:Map<string,string>,candidates:Map<string,string>){
  let created=0;
  for(const [code,name]of result.people){
   await client.query('INSERT INTO persons(code,display_name,first_seen_import_id,created_at) VALUES($1,$2,$3,now()) ON CONFLICT(code) DO UPDATE SET first_seen_import_id=coalesce(persons.first_seen_import_id,excluded.first_seen_import_id)',[code,name||code,id]);
   if((await client.query('SELECT id FROM users WHERE person_code=$1 LIMIT 1',[code])).rowCount)continue;
   const username=await this.employees.suggest(name,code,candidates.get(name.trim())||'',client);
   await client.query(`INSERT INTO users(username,password_hash,display_name,person_code,role,is_active,status,must_change_password,lang,created_at) VALUES($1,$2,$3,$4,'staff',true,'active',true,'',now())`,[username,passwordHashes.get(code),name||code,code]);created++;
  }return created;
 }
 private async buildStores(client:PoolClient,id:number,result:WorkbookResult){
  const inserted=await client.query(`WITH source AS (SELECT DISTINCT ON(btrim(store_id_raw)) btrim(store_id_raw) AS sid,store_name_local_raw AS name FROM raw_records WHERE import_id=$1 AND btrim(store_id_raw)<>'' ORDER BY btrim(store_id_raw),id),missing AS MATERIALIZED (
   SELECT nextval(pg_get_serial_sequence('store_entities','id'))::int AS id,s.sid,s.name FROM source s WHERE NOT EXISTS(SELECT 1 FROM store_entities e WHERE e.store_id_raw=s.sid))
   INSERT INTO store_entities(id,store_id_raw,name_local,name_norm,first_seen_import_id,master_id,master_store_id,created_at,updated_at)
   SELECT id,sid,name,lower(regexp_replace(normalize(name,NFKC),'[[:space:]]','','g')),$1,id,sid,now(),now() FROM missing`,[id]);
  // Group only exact trimmed names. Never apply settlement-space folding to master identity.
  await client.query(`WITH affected AS (SELECT DISTINCT btrim(e.name_local) AS name FROM store_entities e JOIN raw_records r ON btrim(r.store_id_raw)=e.store_id_raw WHERE r.import_id=$1),members AS (
   SELECT e.id,e.store_id_raw,btrim(e.name_local) AS name FROM store_entities e JOIN affected a ON a.name=btrim(e.name_local) WHERE btrim(e.name_local)<>'' AND NOT EXISTS(SELECT 1 FROM store_merge_logs l WHERE l.entity_id=e.id)),anchors AS (
   SELECT m.id,m.store_id_raw,m.name,min(r.modified_raw) FILTER(WHERE btrim(coalesce(r.visible_raw,''))<>'') AS first_time FROM members m LEFT JOIN raw_records r ON btrim(r.store_id_raw)=m.store_id_raw GROUP BY m.id,m.store_id_raw,m.name),ranked AS (
   SELECT id,first_value(id) OVER(PARTITION BY name ORDER BY first_time IS NULL,first_time,id::text) AS master,first_value(store_id_raw) OVER(PARTITION BY name ORDER BY first_time IS NULL,first_time,id::text) AS master_sid FROM anchors)
   UPDATE store_entities e SET master_id=r.master,master_store_id=r.master_sid,updated_at=now() FROM ranked r WHERE e.id=r.id`,[id]);
  await client.query(`WITH affected AS (SELECT DISTINCT btrim(store_id_raw) AS sid FROM raw_records WHERE import_id=$1),earliest AS (
   SELECT DISTINCT ON(btrim(r.store_id_raw)) btrim(r.store_id_raw) AS sid,r.id FROM raw_records r JOIN affected a ON a.sid=btrim(r.store_id_raw) WHERE btrim(coalesce(r.visible_raw,''))<>'' ORDER BY btrim(r.store_id_raw),coalesce(nullif(r.modified_raw,''),'z'),r.id)
   UPDATE store_entities e SET raw_data_id=a.id FROM earliest a WHERE e.store_id_raw=a.sid`,[id]);
  await client.query('UPDATE store_entities child SET master_raw_data_id=parent.raw_data_id FROM store_entities parent WHERE child.master_id=parent.id AND child.master_id<>child.id AND child.first_seen_import_id=$1',[id]);
  if(result.addresses.size)await client.query(`UPDATE store_entities e SET address_local=a.address,updated_at=now() FROM json_to_recordset($1::json) AS a(id text,address text) WHERE e.store_id_raw=a.id AND btrim(coalesce(e.address_local,''))=''`,[JSON.stringify([...result.addresses].map(([id,address])=>({id,address})))]);
  return inserted.rowCount??0;
 }
 async complete(id:number,actor:number,stats:Record<string,unknown>){
  await this.settlement.judgeImport(id,actor);
  const months=(await this.db.query<{month:string}>('SELECT DISTINCT left(modified_raw,7) AS month FROM raw_records WHERE import_id=$1 ORDER BY month',[id])).rows;
  const pending=(await this.db.query(`SELECT a.id FROM appeals a JOIN raw_records r ON r.id=a.raw_record_id WHERE a.status='pending' AND left(r.modified_raw,7)=ANY($1::text[]) LIMIT 1`,[months.map(row=>row.month)])).rows[0];
  if(pending)return {ok:true,id,...stats,admissionPending:true,message:'解析判定已完成；相关月份仍有未决申诉，处理后再入表或月重算'};
  const admitted=await this.settlement.finalize(id,actor);
  const master=await new StoreMasterService(this.db,this.settlement).automatic(id,actor);
  for(const {month}of months)if(!master.recomputed.includes(month))await this.settlement.rebuild(month,actor);
  if(process.env.AI_API_KEY&&process.env.VISIT_REPORT_AI!=='0'){
   const range=(await this.db.query<{start:string;end:string}>('SELECT min(japan_date)::text AS start,max(japan_date)::text AS end FROM formal_records WHERE import_id=$1',[id])).rows[0];
   if(range.start&&range.end)await new ReportAnalysisService(this.db,new ReportComparisonService(this.db)).start(range.start,range.end,actor).catch(()=>{});
  }
  return {ok:true,id,...stats,admitted,master,months:months.map(row=>row.month)};
 }
 async process(input:unknown,layoutModel?:ReportModel,nameModel?:ReportModel){
  const task=taskSchema.parse(input);const file=(await this.db.query<ImportFile>('SELECT id,status,file_name,stored_path,layout,uploaded_by FROM imports WHERE id=$1',[task.id])).rows[0];if(!file)return {ok:false,code:'NOT_FOUND',message:'文件已不存在'};
  if(file.status==='parsed'&&file.layout?.parse_request===task.requestId)return this.complete(task.id,task.actor,{reused:true});
  const spool=join(uploadRoot(),'parse-'+task.id+'-'+task.requestId+'.jsonl');await mkdir(uploadRoot(),{recursive:true,mode:0o700});
  let prepared=false;
  try{
   const handle=await open(spool,'w',0o600);let result:WorkbookResult;
   try{result=await parseVisitWorkbookAdaptive(readUploadPath(file.stored_path),async row=>{await handle.write(JSON.stringify(row)+'\n');},task.layout,layoutModel);}finally{await handle.close();}
   if(!result.parsed_rows)throw new VisitParseError('NO_VISIT_ROWS','工作簿没有巡店数据行');
   if(!(await this.db.query("SELECT id FROM users WHERE id=$1 AND role='admin' AND status IN ('active','leave') AND is_active=true",[task.actor])).rowCount)throw new BusinessError('FORBIDDEN','管理员权限已变化，文件未导入',403);
   const passwordHashes=new Map<string,string>();const existing=new Set((await this.db.query<{person_code:string}>('SELECT person_code FROM users WHERE person_code=ANY($1::text[])',[[...result.people.keys()]])).rows.map(row=>row.person_code));
   const candidates=await aiLoginCandidates([...result.people].filter(([code])=>!existing.has(code)).map(([,name])=>name),nameModel);
   for(const [code]of result.people)if(!existing.has(code))passwordHashes.set(code,await passwordWork.run(()=>hash(process.env.NEXT_DEFAULT_STAFF_PASSWORD||'demo123')));
   const written=await this.db.transaction(async client=>{
    const user=(await client.query("SELECT id FROM users WHERE id=$1 AND role='admin' AND status IN ('active','leave') AND is_active=true FOR SHARE",[task.actor])).rows[0];if(!user)throw new BusinessError('FORBIDDEN','管理员权限已变化，文件未导入',403);
    await client.query("SELECT pg_advisory_xact_lock(hashtext('settlement-write'),0)");
    await assertMonthsOpen(client,result.months);
    await client.query('SELECT id FROM imports WHERE id=$1 FOR UPDATE',[task.id]);
    if((await client.query('SELECT id FROM formal_records WHERE import_id=$1 UNION ALL SELECT id FROM appeals WHERE import_id=$1 LIMIT 1',[task.id])).rowCount)throw new BusinessError('REPARSE_HAS_HISTORY','已有正式记录或申诉，不能替换源记录',409);
    await client.query('DELETE FROM raw_records WHERE import_id=$1',[task.id]);
    const people=await this.buildPeople(client,task.id,result,passwordHashes,candidates);await this.writeRows(client,task.id,spool);const stores=await this.buildStores(client,task.id,result);
    await client.query(`UPDATE imports SET status='parsed',format=$1,header_row=$2,data_start_row=$3,parsed_sheets=$4,ignored_sheets=$5,total_rows=$6,parsed_rows=$6,layout=$7,warnings=$8,errors='[]' WHERE id=$9`,[result.format,result.header_row,result.data_start_row,JSON.stringify(result.parsed_sheets),JSON.stringify(result.ignored_sheets),result.parsed_rows,JSON.stringify({...result.layout,parse_request:task.requestId}),JSON.stringify([...result.warnings,...result.addressConflicts?[`同一Store ID存在不同地址，已跳过 ${result.addressConflicts} 项`]:[]]),task.id]);
    return {people,stores};
   });prepared=true;
   return this.complete(task.id,task.actor,{rows:result.parsed_rows,createdPeople:written.people,createdStores:written.stores});
  }catch(error){const code=error instanceof BusinessError||error instanceof VisitParseError?error.code:'IMPORT_PARSE_FAILED';const message=error instanceof BusinessError||error instanceof VisitParseError?error.message:'工作簿无法解析或处理，请检查文件与布局';await this.db.query('UPDATE imports SET status=$1,errors=$2 WHERE id=$3',[prepared?'parsed':'failed',JSON.stringify([message]),task.id]);return {ok:false,code,message,id:task.id,parsed:prepared};}
  finally{await unlink(spool).catch(()=>{});}
 }
}
@Controller('imports')
export class VisitImportsController{
 constructor(@Inject(AuthService)private readonly auth:AuthService,@Inject(VisitImportsService)private readonly service:VisitImportsService){}
 @Post('upload')@UseGuards(AdminUploadGuard)@UseInterceptors(FileInterceptor('file',{storage:diskStorage({destination:(_req,_file,callback)=>{mkdir(join(uploadRoot(),'incoming'),{recursive:true,mode:0o700}).then(()=>callback(null,join(uploadRoot(),'incoming'))).catch(()=>callback(new Error('PRIVATE_UPLOAD_UNAVAILABLE'),''));},filename:(_req,_file,callback)=>callback(null,randomUUID()+'.xlsx')}),limits:{fileSize:50*1024*1024,files:1,fields:1,fieldSize:65536},fileFilter:(_req,file,callback)=>{if(!safeUploadName(file.originalname).toLowerCase().endsWith('.xlsx'))return callback(new BusinessError('UPLOAD_EXTENSION','仅支持.xlsx文件',400),false);callback(null,true);}}))
 async upload(@Req()req:Request,@UploadedFile()file:Express.Multer.File|undefined){if(!file)throw new BusinessError('UPLOAD_REQUIRED','请选择.xlsx文件',400);try{const {user}=await this.auth.require(req,{admin:true,write:true});return await this.service.upload(file.originalname,file.path,user.id);}finally{await unlink(file.path).catch(()=>{});}}
 @Get('jobs/:id')async status(@Req()req:Request,@Param('id')id:string){await this.auth.require(req,{admin:true});return this.service.status(id);}
 @Get(':id')async detail(@Req()req:Request,@Param('id')id:string){await this.auth.require(req,{admin:true});return this.service.detail(parse(z.coerce.number().int().positive(),id));}
 @Get(':id/original')async original(@Req()req:Request,@Param('id')id:string,@Res()response:Response){await this.auth.require(req,{admin:true});const file=await this.service.original(parse(z.coerce.number().int().positive(),id));response.setHeader('Content-Type','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');response.setHeader('Content-Disposition',`attachment; filename="visit-source.xlsx"; filename*=UTF-8''${encodeURIComponent(file.name)}`);const stream=createReadStream(file.path);stream.on('error',()=>{if(response.headersSent)response.destroy();else response.status(404).json({code:'FILE_MISSING',message:'原件不存在'});});response.on('close',()=>stream.destroy());stream.pipe(response);}
 @Post(':id/reparse')async reparse(@Req()req:Request,@Param('id')id:string,@Body()body:unknown){const {user}=await this.auth.require(req,{admin:true,write:true});const input=parse(z.object({layout:layoutSchema.optional(),requestId:z.string().uuid()}).strict(),body);return this.service.reparse(parse(z.coerce.number().int().positive(),id),user.id,input.layout,input.requestId);}
}
export async function runVisitImportTask(payload:unknown){const db=new Database(loadConfig());try{const comparison=new ReportComparisonService(db);return await new VisitImportsService(db,new EmployeeService(db),new SettlementService(db,new SettlementSyncService(db,comparison))).process(payload);}finally{await db.onModuleDestroy();}}

export async function reconcileVisitImports(){const db=new Database(loadConfig());try{const service=new VisitImportsService(db,new EmployeeService(db),new SettlementService(db,new SettlementSyncService(db,new ReportComparisonService(db))));await service.reconcile();}finally{await db.onModuleDestroy();}}
