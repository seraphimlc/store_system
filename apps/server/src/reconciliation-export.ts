import type {Writable} from 'node:stream';
import {ReconciliationInsightService} from './reconciliation-insight';
import {Controller,Get,Inject,Injectable,Param,Req,Res} from '@nestjs/common';import type {Request,Response} from 'express';import {z} from 'zod';import ExcelJS from 'exceljs';import {AuthService} from './auth';import {Database} from './database';import {BusinessError} from './errors';
@Injectable()
export class ReconciliationExportService{
 constructor(@Inject(ReconciliationInsightService)private readonly insight:ReconciliationInsightService){}
 async write(client:Pick<Database,'query'>,id:number,output:Writable,kind:'report'|'detail'|'diff'='report'){
  const task=(await client.query<{status:string;params:{month:string};summary:{attribution?:{consistent:number;only_system:number;only_report:number;net:number;only_report_reason:Record<string,number>};ai_interpret?:{text:string;model:string;at:string};sys_only?:{code:string;name:string;points:number}[]}}>('SELECT status,params,summary FROM recon_tasks WHERE id=$1',[id])).rows[0];if(!task)throw new BusinessError('NOT_FOUND','对账任务不存在',404);if(task.status!=='done')throw new BusinessError('RECON_NOT_DONE','对账尚未完成，无法导出',409);
  const insight=kind==='report'&&task.summary.ai_interpret?.text?await this.insight.readInTransaction(client,id):null;

  const workbook=new ExcelJS.stream.xlsx.WorkbookWriter({stream:output,useSharedStrings:false,useStyles:false});
  try{
   let cursor=0;
   if(kind!=='detail'){
   const people=workbook.addWorksheet('月度差异');people.addRow(['员工编号','系统点数','对账点数','系统减对账','说明']).commit();cursor=0;
   for(;;){const rows=(await client.query<{id:number;submitter_code:string;system_value:number;report_value:number;diff:number;note:string}>('SELECT id,submitter_code,system_value,report_value,diff,note FROM recon_results WHERE task_id=$1 AND id>$2 ORDER BY id LIMIT 1000',[id,cursor])).rows;if(!rows.length)break;for(const row of rows)people.addRow([row.submitter_code,row.system_value,row.report_value,row.diff,row.note]).commit();cursor=rows.at(-1)!.id;}people.commit();
   const daily=workbook.addWorksheet('逐日问题');daily.addRow(['日期','编号','系统点数','对账点数','系统店数','对账店数','系统减对账','来源']).commit();cursor=0;
   for(;;){if(output.destroyed)throw new Error('EXPORT_CLOSED');const rows=(await client.query<{id:number;ref_date:string;person_code:string;sys_points:number;rep_points:number;sys_count:number;rep_count:number;diff:number;side:string}>('SELECT id,ref_date::text,person_code,sys_points,rep_points,sys_count,rep_count,diff,side FROM recon_day_rows WHERE task_id=$1 AND id>$2 ORDER BY id LIMIT 1000',[id,cursor])).rows;if(!rows.length)break;for(const row of rows)daily.addRow([row.ref_date,row.person_code,row.sys_points,row.rep_points,row.sys_count,row.rep_count,row.diff,row.side]).commit();cursor=rows.at(-1)!.id;}daily.commit();
   }
   if(kind!=='diff'){
   const all=workbook.addWorksheet('对账原始汇总');all.addRow(['日期','编号','姓名','对账点数','店数']).commit();cursor=0;
   for(;;){const rows=(await client.query<{id:number;ref_date:string;person_code:string;person_name:string;points:number;cnt:number}>('SELECT id,ref_date::text,person_code,person_name,points,cnt FROM recon_data_rows WHERE task_id=$1 AND id>$2 ORDER BY id LIMIT 1000',[id,cursor])).rows;if(!rows.length)break;for(const row of rows)all.addRow([row.ref_date,row.person_code,row.person_name,row.points,row.cnt]).commit();cursor=rows.at(-1)!.id;}all.commit();
   }
   if(kind==='report'){
   const missing=workbook.addWorksheet('系统有报表无');missing.addRow(['编号','姓名','系统点数','风险说明']).commit();for(const row of task.summary.sys_only??[]){const item=typeof row==='string'?{code:row,name:row,points:null}:row;missing.addRow([item.code,item.name,item.points,'报表未列出，请在发薪前核对']).commit();}missing.commit();
   if(task.summary.attribution){const a=task.summary.attribution,sheet=workbook.addWorksheet('逐店归因');sheet.addRow(['指标','记录数']).commit();for(const [label,count]of [['两侧一致',a.consistent],['仅系统',a.only_system],['仅对账',a.only_report],['净差（系统减对账）',a.net]])sheet.addRow([label,count]).commit();for(const [reason,count]of Object.entries(a.only_report_reason??{}))sheet.addRow([reason,count]).commit();sheet.commit();}
   if(task.summary.ai_interpret?.text){const sheet=workbook.addWorksheet('AI解读');sheet.addRow(['正文','模型','生成时间','是否过期']).commit();sheet.addRow([task.summary.ai_interpret.text,task.summary.ai_interpret.model,task.summary.ai_interpret.at,insight?.stale?'数据已变化，请重新生成':'与当前数字一致']).commit();sheet.commit();}
   }
   await workbook.commit();
  }catch(error){output.destroy();throw error;}
  return {filename:`对账结果_${task.params.month}_${id}.xlsx`,month:task.params.month};
 }
}
@Controller('reconciliation')
export class ReconciliationExportController{
 constructor(@Inject(AuthService)private readonly auth:AuthService,@Inject(Database)private readonly db:Database,@Inject(ReconciliationExportService)private readonly service:ReconciliationExportService){}
 @Get(':id/export')async export(@Req()req:Request,@Param('id')value:string,@Res()res:Response){
  await this.auth.require(req,{admin:true});const parsed=z.coerce.number().int().positive().safeParse(value);if(!parsed.success)throw new BusinessError('VALIDATION','对账任务编号无效',400);const id=parsed.data;
  const task=(await this.db.query<{status:string;params:{month:string}}>('SELECT status,params FROM recon_tasks WHERE id=$1',[id])).rows[0];
  if(!task)throw new BusinessError('NOT_FOUND','对账任务不存在',404);if(task.status!=='done')throw new BusinessError('RECON_NOT_DONE','对账尚未完成，无法导出',409);
  res.setHeader('Content-Type','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');res.setHeader('Content-Disposition',`attachment; filename="reconciliation.xlsx"; filename*=UTF-8''${encodeURIComponent(`对账结果_${task.params.month}_${id}.xlsx`)}`);
  try{await this.service.write(this.db,id,res);}catch{res.destroy();}
 }
}
