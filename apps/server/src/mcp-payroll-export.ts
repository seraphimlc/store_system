import {Inject,Injectable} from '@nestjs/common';
import type {PoolClient} from 'pg';
import {readFile} from 'node:fs/promises';
import {z} from 'zod';
import type {McpActor} from './mcp';
import {McpReadError} from './mcp-read';
import {BusinessError} from './errors';
import {PayrollPaymentsService} from './payroll-payments';
import {PayrollExportService} from './payroll-export';
import {McpExportFiles,type ExportCleanup} from './mcp-export-files';
const schema=z.object({month:z.string().regex(/^[0-9]{4}-(0[1-9]|1[0-2])$/).refine(v=>Number(v.slice(0,4))>=1&&Number(v.slice(0,4))<=9998),seq:z.number().int().min(1).max(2),dry_run:z.boolean().default(false),token:z.string().regex(/^[a-f0-9]{64}$/).optional(),confirm_text:z.string().nullable().optional()}).strict();
export const mcpPayrollExportTool={name:'visit_payroll_export',title:'导出发薪表',description:'仅管理员且需write scope：month/seq(1或2)。dry_run=true生成预览文件和金额，不登记；正式导出须同一预览token及“确认导出发薪 YYYY-MM 第N期”。首次无确认语先返回预览，不会付款。正式导出=登记发放和抵扣，重复导出不覆盖同期期实发。金额以现有pay_plan/台账为准。返回XLSX内联base64(4MiB内)和私有download_url；下载需本账号有效管理员Bearer凭据。',inputSchema:{type:'object' as const,properties:{month:{type:'string',pattern:'^[0-9]{4}-(0[1-9]|1[0-2])$'},seq:{type:'integer',minimum:1,maximum:2},dry_run:{type:'boolean',default:false},token:{type:'string',pattern:'^[a-f0-9]{64}$'},confirm_text:{type:['string','null']}},required:['month','seq'],additionalProperties:false},annotations:{readOnlyHint:false,destructiveHint:true,idempotentHint:true,openWorldHint:false}};
@Injectable()
export class McpPayrollExportService{
 constructor(@Inject(PayrollPaymentsService)private readonly payments:PayrollPaymentsService,@Inject(PayrollExportService)private readonly exports:PayrollExportService,@Inject(McpExportFiles)private readonly files:McpExportFiles){}
 async run(client:PoolClient,actor:McpActor,args:Record<string,unknown>,auditId:number,cleanup:ExportCleanup){
  if(actor.role!=='admin'||!actor.scopes.includes('write'))throw new McpReadError('FORBIDDEN_TOOL','发薪导出仅管理员且需要write scope');
  const parsed=schema.safeParse(args);if(!parsed.success)throw new McpReadError('BAD_PARAM','发薪月份、期次或确认参数无效');const input=parsed.data;
  const dry=input.dry_run||input.confirm_text==null;
  if(!dry){const expected=`确认导出发薪 ${input.month} 第${input.seq}期`;if(input.confirm_text?.trim()!==expected)throw new McpReadError('CONFIRM_REQUIRED','确认语不匹配',`请复述确认语：${expected}`);if(!input.token)throw new McpReadError('PAYROLL_PREVIEW_REQUIRED','请先dry_run预览并提供token');}
  try{
   await client.query("SELECT pg_advisory_xact_lock(hashtext('settlement-write'),0)");
   await client.query("SELECT pg_advisory_xact_lock(hashtext('payroll-financial-write'),0)");
   await client.query("SELECT pg_advisory_xact_lock(hashtext('payroll-month'),hashtext($1))",[input.month]);
   const preview=await this.payments.previewInTransaction(client,input.month,input.seq);
   if(input.token&&input.token!==preview.token)throw new McpReadError('PAYROLL_PREVIEW_CHANGED','金额或找平余额已变化，请重新预览');
   const built=await this.exports.buildInTransaction(client,input.month,input.seq,preview.token,actor.uid);
   // Keep no browser-downloadable draft: copy to audit-gated MCP storage and remove the staging artifact.
   let file;try{const artifact=await this.exports.file(built.id);file=await this.files.save(await readFile(artifact.path),artifact.filename,actor,auditId,cleanup);}finally{await this.exports.remove(built.id);}
   const registration=dry?{month:input.month,half:input.seq,created:0,reused:0,paymentIds:[] as number[]}:await this.payments.registerInTransaction(client,input.month,input.seq,actor.uid,preview.token);
   await client.query('UPDATE mcp_audit_log SET params_json=$2 WHERE id=$1',[auditId,JSON.stringify({export_id:file.export_id})]);
   return {...file,month:input.month,seq:input.seq,dry_run:dry,token:preview.token,currency:'JPY',amount_summary:{persons:preview.rows.length,total_amount:preview.rows.reduce((n,row)=>n+row.grossAmount,0),total_payable:preview.rows.reduce((n,row)=>n+row.payable,0),currency:'JPY'},
    ledger:{seq:input.seq,registered:registration.created,reused:registration.reused,note:dry?'预演仅生成文件，未登记发放':'导出发薪表即登记该期已发；同期再次导出不覆盖实发'},registration};
  }catch(error){if(error instanceof BusinessError)throw new McpReadError(error.code,error.message,error.getStatus()===409?'重新预览后核对再导出':'');throw error;}
 }
}
