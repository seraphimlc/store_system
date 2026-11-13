import {Inject,Injectable} from '@nestjs/common';
import type {PoolClient} from 'pg';
import {createWriteStream} from 'node:fs';
import {mkdtemp,readFile,rm} from 'node:fs/promises';
import {finished} from 'node:stream/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {z} from 'zod';
import type {McpActor} from './mcp';
import {McpReadError} from './mcp-read';
import {BusinessError} from './errors';
import {ReconciliationExportService} from './reconciliation-export';
import {McpExportFiles,type ExportCleanup} from './mcp-export-files';
const schema=z.object({month:z.string().regex(/^[0-9]{4}-(0[1-9]|1[0-2])$/).nullable().optional(),task_id:z.number().int().positive().max(2147483647).nullable().optional(),kind:z.enum(['report','detail','diff']).default('report')}).strict();
export const mcpReconExportTool={name:'visit_recon_export',title:'导出对账文件',description:'仅管理员：kind=report完整月度报告/detail对账原始汇总/diff月度和逐日差异。task_id或month二选一；month选当前未替代任务，只允许已完成任务。只读，不生成AI、不确认找平。返回XLSX base64(4MiB内)和私有download_url；下载需本账号有效管理员Bearer Token。',inputSchema:{type:'object' as const,properties:{month:{type:['string','null'],pattern:'^[0-9]{4}-(0[1-9]|1[0-2])$'},task_id:{type:['integer','null'],minimum:1},kind:{type:'string',enum:['report','detail','diff'],default:'report'}},additionalProperties:false},annotations:{readOnlyHint:true,destructiveHint:false,idempotentHint:true,openWorldHint:false}};
function replaced(value:unknown):boolean{return value!=null&&value!==false&&value!==0&&value!==''&&(Array.isArray(value)?value.length>0:typeof value==='object'?Object.keys(value).length>0:true);}
@Injectable()
export class McpReconExportService{
 constructor(@Inject(ReconciliationExportService)private readonly exports:ReconciliationExportService,@Inject(McpExportFiles)private readonly files:McpExportFiles){}
 async run(client:PoolClient,actor:McpActor,args:Record<string,unknown>,auditId:number,cleanup:ExportCleanup){
  if(actor.role!=='admin')throw new McpReadError('FORBIDDEN_TOOL','仅管理员可导出对账');
  const parsed=schema.safeParse(args);if(!parsed.success)throw new McpReadError('BAD_PARAM','对账导出参数无效');const input=parsed.data;
  if(!input.task_id&&!input.month)throw new McpReadError('BAD_PARAM','必须提供task_id或month');
  const task=input.task_id?(await client.query('SELECT id,status,params FROM recon_tasks WHERE id=$1',[input.task_id])).rows[0]:
   (await client.query("SELECT id,status,params FROM recon_tasks WHERE kind='monthly_v3' AND params->>'month'=$1 ORDER BY id DESC",[input.month])).rows.find(row=>!replaced(row.params?.replaced_by));
  if(!task)throw new McpReadError('NOT_FOUND','对账任务不存在');if(task.status!=='done')throw new McpReadError('RECON_NOT_DONE','对账尚未完成，无法导出');
  const directory=await mkdtemp(join(tmpdir(),'mcp-recon-export-'));const path=join(directory,'output.xlsx'),output=createWriteStream(path,{mode:0o600}),closed=finished(output);void closed.catch(()=>{});
  try{
   const info=await this.exports.write(client,task.id,output,input.kind);await closed;
   const file=await this.files.save(await readFile(path),info.filename,actor,auditId,cleanup);
   await client.query('UPDATE mcp_audit_log SET params_json=$2 WHERE id=$1',[auditId,JSON.stringify({export_id:file.export_id})]);
   return {...file,task_id:task.id,month:info.month,kind:input.kind,currency:'JPY',note:'文件来自已有对账结果；未生成AI或修改业务数据'};
  }catch(error){if(error instanceof BusinessError)throw new McpReadError(error.code,error.message);throw error;}
  finally{if(!output.closed)output.destroy();await closed.catch(()=>{});await rm(directory,{recursive:true,force:true});}
 }
}
