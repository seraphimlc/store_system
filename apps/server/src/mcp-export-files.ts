import {Inject,Injectable} from '@nestjs/common';
import {randomUUID,createHash} from 'node:crypto';
import {readFile,writeFile,mkdir,unlink} from 'node:fs/promises';
import {resolve,join} from 'node:path';
import {z} from 'zod';
import {Database} from './database';
import {McpReadError} from './mcp-read';
import type {McpActor} from './mcp';
export type ExportCleanup=(cleanup:()=>Promise<void>)=>void;
function directory(){return resolve(process.env.NEXT_TASK_DIRECTORY||'data/refactor/jobs','mcp-exports');}
@Injectable()
export class McpExportFiles{
 constructor(@Inject(Database)private readonly db:Database){}
 async save(bytes:Buffer,filename:string,actor:McpActor,auditId:number,cleanup:ExportCleanup){
  const id=randomUUID(),root=directory();await mkdir(root,{recursive:true,mode:0o700});
  cleanup(async()=>{await Promise.all(['.xlsx','.json'].map(suffix=>unlink(join(root,id+suffix)).catch(()=>{})));});
  await writeFile(join(root,id+'.xlsx'),bytes,{mode:0o600,flag:'wx'});
  await writeFile(join(root,id+'.json'),JSON.stringify({filename,userId:actor.uid,auditId}),{mode:0o600,flag:'wx'});
  return {export_id:id,filename,size_bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex'),mime_type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',download_url:'/mcp/exports/'+id,
   ...(bytes.length<=4*1024*1024?{content_base64:bytes.toString('base64')}:{hint:'文件超过内联上限，请用当前凭据下载私有文件'})};
 }
 async download(id:string,actor:McpActor){
  if(actor.role!=='admin')throw new McpReadError('FORBIDDEN_TOOL','仅管理员可下载导出');
  if(!z.string().uuid().safeParse(id).success)throw new McpReadError('NOT_FOUND','导出不存在');
  let metadata:{filename:string;userId:number;auditId:number};
  try{metadata=JSON.parse(await readFile(join(directory(),id+'.json'),'utf8'));}catch{throw new McpReadError('NOT_FOUND','导出不存在或尚未完成');}
  if(metadata.userId!==actor.uid)throw new McpReadError('NOT_FOUND','导出不存在或不属于当前账号');
  const audit=(await this.db.query("SELECT id FROM mcp_audit_log WHERE id=$1 AND user_id=$2 AND ok=true AND tool IN('visit_payroll_export','visit_recon_export') AND params_json::jsonb->>'export_id'=$3",[metadata.auditId,actor.uid,id])).rows[0];
  if(!audit)throw new McpReadError('NOT_FOUND','导出尚未完成或已失效');
  try{return {filename:metadata.filename,bytes:await readFile(join(directory(),id+'.xlsx'))};}catch{throw new McpReadError('NOT_FOUND','导出文件不存在，请重新导出');}
 }
}
