import {Inject,Injectable} from '@nestjs/common';
import type {PoolClient} from 'pg';
import {mkdir,writeFile,unlink} from 'node:fs/promises';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {setTimeout as delay} from 'node:timers/promises';
import {z} from 'zod';
import {Database} from './database';
import type {McpActor} from './mcp';
import {McpReadError} from './mcp-read';
import {BusinessError} from './errors';
import {VisitImportsService} from './visit-imports';
import {ReconciliationService} from './reconciliation';
import {readWorkbookRows} from './visit-workbook';
import {uploadRoot,safeUploadName} from './private-files';
import {assertMonthsOpen} from './financial-control';
const norm=(v:unknown)=>String(v??'').trim(),template='STORE_TASK_EXCEL_SHEET';
const reconMarkers=['statement date','agent name','iso pid','iso name','action type','shop name','store basic id'];
const visitMarkers=['modified time','submitter','deploy new a+posm','review status','store name-local'];
const manualSheets=['月度总览','人员汇总','最终有效数据','全部重复数据'];
const options=[{kind:'daily_records',label:'巡店记录'},{kind:'recon',label:'对账明细'}];
export interface UploadInspection{kind:string;reason:string;sheets:string[];header:string[];estimated_rows:number;estimated_persons:number;inferred_month:string|null}
export async function inspectMcpUpload(path:string,forced?:string|null):Promise<UploadInspection>{
 const sheets=new Map<string,{header:string[];first:string[];rows:number;people:Record<string,Set<string>>;months:Map<string,number>}>();
 for await(const row of readWorkbookRows(path)){
  let sheet=sheets.get(row.sheet);if(!sheet){sheet={header:[],first:[],rows:0,people:{daily_records:new Set(),recon:new Set()},months:new Map()};sheets.set(row.sheet,sheet);}
  const values=row.values.map(norm),any=values.some(Boolean);
  if(row.row<=3&&!sheet.first.some(Boolean))sheet.first=values;
  if(!sheet.header.length&&row.row<=30&&any){sheet.header=values;continue;}
  if(sheet.header.length&&any){
   if(sheet.rows<20000){sheet.rows++;const low=sheet.header.map(v=>v.toLowerCase());for(const [kind,keys]of Object.entries({daily_records:['submitter','提交人'],recon:['agent name','agent','担当','姓名']})){const index=low.findIndex(key=>keys.includes(key));if(index>=0&&values[index])sheet.people[kind].add(values[index]);}}
   if(row.row<=300){const index=sheet.header.findIndex(value=>['statement date','modified time','日期','记录日期'].includes(value.toLowerCase()));const month=index<0?'':(values[index]??'').slice(0,7);if(/^[0-9]{4}-(0[1-9]|1[0-2])$/.test(month))sheet.months.set(month,(sheet.months.get(month)??0)+1);}
  }
 }
 const names=[...sheets.keys()].map(norm),first=[...sheets.values()][0],header=first?.first??[],joined=header.map(v=>v.toLowerCase()).join('|');
 let kind='unknown',reason='未识别出巡店记录或对账明细的表头特征';
 if(names.includes(template)){kind='daily_records';reason=`sheet 名为 ${template}（巡店记录模板）`;}
 else if(names.some(name=>manualSheets.includes(name))){kind='manual';reason='sheet 含「月度总览/人员汇总/最终有效数据」等（手工结算对照件，系统无对应通道）';}
 else{const recon=reconMarkers.filter(word=>joined.includes(word)).length,visit=visitMarkers.filter(word=>joined.includes(word)).length;if(recon>=2&&!visit){kind='recon';reason=`表头命中对账专有列 ${recon} 个`;}else if(visit>=2){kind='daily_records';reason=`表头命中巡店记录列 ${visit} 个（无模板 sheet 名）`;}else if(recon>=2){kind='recon';reason=`表头命中对账列 ${recon} 个`;}}
 if(forced){kind=forced;reason=`由调用方指定（kind=${forced}）`;}
 const estimate=sheets.get(template)??first,inferred=[...(first?.months??[])].sort((a,b)=>b[1]-a[1])[0]?.[0]??null;
 return {kind,reason,sheets:names,header:names.includes(template)||kind==='manual'?[]:header,estimated_rows:estimate?.rows??0,estimated_persons:estimate?.people[kind]?.size??0,inferred_month:kind==='recon'?inferred:null};
}
const schema=z.object({filename:z.string().max(255).nullable().optional(),content_base64:z.string().nullable().optional(),path:z.string().nullable().optional(),month:z.string().regex(/^[0-9]{4}-(0[1-9]|1[0-2])$/).nullable().optional(),kind:z.string().nullable().optional(),dry_run:z.boolean().default(false),wait_seconds:z.number().int().min(0).max(45).default(30)}).strict();
export const mcpUploadTool={name:'visit_upload',title:'巡店与对账上传',description:'仅管理员且需write scope。上传.xlsx的content_base64及filename，自动识别巡店/对账文件；kind可显式daily_records（兼容visit）或recon。类型不明确返回选项，不能自行猜。dry_run=true只识别/预估，不创建业务记录或调用AI；正式上传由现有Worker处理，默认最多等待30秒，未完成会明确返回accepted/任务编号，不能视为入表成功。wait_seconds=0只登记入队。对账month可从日期推断。服务器path模式关闭。',inputSchema:{type:'object' as const,properties:{filename:{type:['string','null']},content_base64:{type:['string','null']},path:{type:['string','null']},month:{type:['string','null'],pattern:'^[0-9]{4}-(0[1-9]|1[0-2])$'},kind:{type:['string','null']},dry_run:{type:'boolean',default:false},wait_seconds:{type:'integer',minimum:0,maximum:45,default:30}},additionalProperties:false},annotations:{readOnlyHint:false,destructiveHint:true,idempotentHint:false,openWorldHint:true}};
@Injectable()
export class McpUploadService{
 constructor(@Inject(Database)private readonly db:Database,@Inject(VisitImportsService)private readonly visits:VisitImportsService,@Inject(ReconciliationService)private readonly recon:ReconciliationService){}
 async prepare(client:PoolClient,actor:McpActor,args:Record<string,unknown>):Promise<{data:Record<string,unknown>;afterCommit?:()=>Promise<Record<string,unknown>>}>{
  if(actor.role!=='admin'||!actor.scopes.includes('write'))throw new McpReadError('FORBIDDEN_TOOL','上传仅管理员且需要write scope');
  const parsed=schema.safeParse(args);if(!parsed.success)throw new McpReadError('BAD_PARAM','上传参数、月份或等待时间无效');const input=parsed.data;
  if(input.path)throw new McpReadError('FORBIDDEN_TOOL','本机路径模式未开启','请使用content_base64');
  const raw=input.content_base64;if(!raw||raw.length>24*1024*1024||raw.length%4||!/^[A-Za-z0-9+/]+={0,2}$/.test(raw))throw new McpReadError('BAD_REQUEST','文件base64无效或过大');
  const bytes=Buffer.from(raw,'base64');if(bytes.length>16*1024*1024||!bytes.length||bytes.toString('base64')!==raw)throw new McpReadError('BAD_REQUEST','文件内容无效或超过16MiB');
  const name=safeUploadName(input.filename??'upload.xlsx');if(!name.toLowerCase().endsWith('.xlsx'))throw new McpReadError('BAD_PARAM','仅支持.xlsx文件');
  const supplied=input.kind?.trim().toLowerCase()||null,forced=supplied==='visit'?'daily_records':supplied;if(forced&&!['daily_records','recon'].includes(forced))throw new McpReadError('BAD_PARAM','kind只能是daily_records或recon');
  const directory=join(uploadRoot(),'mcp-staging');await mkdir(directory,{recursive:true,mode:0o700});const path=join(directory,randomUUID()+'.xlsx');await writeFile(path,bytes,{mode:0o600});
  try{
   let info:UploadInspection;try{info=await inspectMcpUpload(path,forced);}catch{throw new McpReadError('PARSE_FAILED','无法读取有效Excel工作簿','请检查.xlsx文件或修正解析布局');}
   const selected=input.month??info.inferred_month;
   const replacing=info.kind==='recon'&&selected?(await client.query("SELECT id,params FROM recon_tasks WHERE kind='monthly_v3' AND params->>'month'=$1 ORDER BY id",[selected])).rows.filter(row=>!row.params.replaced_by).map(row=>row.id):[];
   if(input.dry_run)return {data:{dry_run:true,detected:{kind:info.kind,reason:info.reason},month:selected,will_replace_task_ids:replacing,estimated_rows:info.estimated_rows,estimated_persons:info.estimated_persons,hint:'dry_run=true：未写入业务数据；以上为识别与影响预估，确认后再正式上传'}};
   if(info.kind==='manual')throw new McpReadError('MANUAL_SETTLEMENT_FILE','这是手工结算对照件，没有对应入库通道','如确认属于巡店或对账请指定kind重传',{options});
   if(info.kind==='unknown')throw new McpReadError('NEED_FILE_KIND','无法自动识别文件类型','请询问用户属于巡店记录还是对账明细后再指定kind',{options,seen:{sheets:info.sheets,header:info.header.filter(Boolean).slice(0,12)}});
   if(info.kind==='daily_records'){
    const prepared=await this.visits.uploadInTransaction(client,name,path,actor.uid),jobId=`import-${prepared.id}-${prepared.task.requestId}`;
    const pending={file_id:prepared.id,file_name:name,accepted:true,status:'uploaded',job_id:jobId,detected:{kind:info.kind,reason:info.reason},pipeline_ok:false,is_overwrite:false,note:'已登记上传，后台解析/判定/入表尚未完成；请查询任务状态'};
    return {data:pending,afterCommit:async()=>{try{await this.visits.enqueue(prepared.task);}catch{return {...pending,status:'failed',queue_status:'failed',note:'文件已登记但入队失败，请在文件详情重试解析；不要重复上传'};}return this.waitVisit(pending,input.wait_seconds);}};
   }
   if(!selected)throw new McpReadError('BAD_PARAM','无法从对账文件推断结算月','请提供month（YYYY-MM）');
   await assertMonthsOpen(client,[selected]);
   const task=await this.recon.uploadInTransaction(client,selected,name,path,actor.uid,'auto'),jobId=`recon-${task.id}-${task.params.request_id}`;
   const pending={task_id:task.id,month:selected,accepted:true,status:'pending',job_id:jobId,replaced_previous_ids:[] as number[],is_overwrite:false,currency:'JPY',detected:{kind:info.kind,reason:info.reason,month_inferred:input.month==null},note:'已登记对账任务，尚未处理完成，旧当前版本暂未替换'};
   return {data:pending,afterCommit:async()=>{try{await this.recon.enqueue(task);}catch{return {...pending,queue_status:'pending_retry',note:'任务已登记但入队失败，可重试入队或由现有恢复流程处理'};}return this.waitRecon(pending,input.wait_seconds);}};
  }catch(error){if(error instanceof BusinessError)throw new McpReadError(error.code==='DUPLICATE_UPLOAD'?'DUPLICATE_FILE':error.code,error.message);throw error;}
  finally{await unlink(path).catch(()=>{});}
 }
 private async waitVisit(pending:Record<string,any>,seconds:number){const end=Date.now()+seconds*1000;do{
  const file=(await this.db.query('SELECT status,parsed_rows,format FROM imports WHERE id=$1',[pending.file_id])).rows[0];
  let job:any;try{job=await this.visits.status(pending.job_id);}catch{}
  if(job?.status==='done'||job?.status==='failed'||file?.status==='failed'){
   const result=job?.result??{},complete=job?.status==='done'&&result.ok===true&&!result.admissionPending;
   return {...pending,status:file?.status??job?.status,processing_status:job?.status??'failed',rows_parsed:file?.parsed_rows??0,format:file?.format??null,months:result.months??[],formal_added:result.admitted?.added??null,pipeline_ok:complete,note:complete?'解析及自动处理完成，财务结果以系统查询为准':result.admissionPending?'解析已完成，但未决申诉阻止自动入表':'解析或后续处理失败，请查看文件详情诊断'};
  }
  if(Date.now()>=end)return pending;await delay(250);
 }while(true);}
 private async waitRecon(pending:Record<string,any>,seconds:number){const end=Date.now()+seconds*1000;do{
  const task=(await this.db.query('SELECT status,params FROM recon_tasks WHERE id=$1',[pending.task_id])).rows[0];
  if(task&&['done','failed'].includes(task.status)){const replaced=task.status==='done'?(await this.db.query("SELECT id FROM recon_tasks WHERE kind='monthly_v3' AND params->>'replaced_by'=$1 ORDER BY id",[String(pending.task_id)])).rows.map(row=>row.id):[];return {...pending,status:task.status,processing_status:task.status,replaced_previous_ids:replaced,is_overwrite:replaced.length>0,note:task.status==='done'?'对账已完成；差异可用visit_recon查询':'对账失败，旧当前版本未被本任务替换，请查看任务诊断'};}
  if(Date.now()>=end)return pending;await delay(250);
 }while(true);}
}
