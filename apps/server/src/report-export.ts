import {Controller,Get,Inject,Query,Req,Res} from '@nestjs/common';
import type {Request,Response} from 'express';
import {z} from 'zod';
import ExcelJS from 'exceljs';
import {AuthService} from './auth';
import {Database} from './database';
import {BusinessError} from './errors';
import {validDate} from './business-dates';
import {ReportComparisonService,reportPoints,scoreShare} from './report-comparison';
const schema=z.object({start:z.string().refine(validDate),end:z.string().refine(validDate),personCode:z.string().max(32).default(''),page:z.coerce.number().int().optional(),kind:z.enum(['reports','compare']).default('compare')}).strict().refine(value=>value.start<=value.end);
@Controller('reports/export')
export class ReportExportController{
 constructor(@Inject(AuthService)private readonly auth:AuthService,@Inject(Database)private readonly db:Database,@Inject(ReportComparisonService)private readonly comparison:ReportComparisonService){}
 @Get()async export(@Req()req:Request,@Query()query:unknown,@Res()res:Response){
  await this.auth.require(req,{admin:true});const parsed=schema.safeParse(query);if(!parsed.success)throw new BusinessError('VALIDATION','导出区间或类型无效',400);const input=parsed.data;
  const client=await this.db.pool.connect();let transaction=false,released=false;
  try{
   await client.query('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');transaction=true;
   // Preflight before sending headers. Streaming writer releases completed row objects.
   const comparison=input.kind==='compare'?await this.comparison.compare(input.start,input.end,input.personCode,'acc',client):null;
   res.setHeader('Content-Type','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');
   const name=input.kind==='reports'?'员工自报明细.xlsx':'员工自报对比.xlsx';
   res.setHeader('Content-Disposition',`attachment; filename="staff-${input.kind}.xlsx"; filename*=UTF-8''${encodeURIComponent(name)}`);
   const workbook=new ExcelJS.stream.xlsx.WorkbookWriter({stream:res,useStyles:false,useSharedStrings:false});
   if(input.kind==='reports'){
    const sheet=workbook.addWorksheet('自报明细');sheet.addRow(['日期','编号','姓名','担当区域','1点店数','2点店数','总店数','分数(点数)','2点分数占比%','来源']).commit();
    let cursor=0;
    for(;;){
     if(res.destroyed)throw new Error('EXPORT_CLIENT_CLOSED');
     const rows=(await client.query<{id:number;date:string;person_code:string;name:string;area:string;p1:number;p2:number;source:string}>(`SELECT r.id,r.report_date::text AS date,r.person_code,coalesce(p.display_name,r.person_code) AS name,r.area,r.p1_cnt AS p1,r.p2_cnt AS p2,r.source FROM staff_daily_reports r LEFT JOIN persons p ON p.code=r.person_code WHERE r.report_date BETWEEN $1::date AND $2::date AND ($3='' OR r.person_code=$3) AND r.id>$4 ORDER BY r.id LIMIT 1000`,[input.start,input.end,input.personCode,cursor])).rows;
     if(!rows.length)break;
     for(const row of rows)sheet.addRow([row.date,row.person_code,row.name,row.area,row.p1,row.p2,row.p1+row.p2,reportPoints(row.p1,row.p2),scoreShare(row.p1,row.p2)===null?null:scoreShare(row.p1,row.p2)!*100,row.source]).commit();
     cursor=rows.at(-1)!.id;
    }
    sheet.commit();
   }else if(comparison){
    const people=workbook.addWorksheet('逐人对比');people.addRow(['编号','姓名','系统1点','系统2点','系统店数','系统分数','自报1点','自报2点','自报店数','自报分数','系统减自报','准确率','漏填天数']).commit();
    for(const p of comparison.persons)people.addRow([p.person_code,p.name,p.sys_p1,p.sys_p2,p.sys_total,p.sys_points,p.rep_p1,p.rep_p2,p.rep_total,p.rep_points,p.d_total,p.acc,p.gaps]).commit();people.commit();
    const daily=workbook.addWorksheet('逐日对比');daily.addRow(['编号','日期','类型','系统店数','系统分数','自报店数','自报分数','系统减自报','准确率']).commit();for(const d of comparison.daily)daily.addRow([d.person_code,d.date,d.kind,d.sys_total,d.sys_points,d.rep_total,d.rep_points,d.dt,d.acc]).commit();daily.commit();
   }
   await client.query('ROLLBACK');transaction=false;client.release();released=true;
   await workbook.commit();
  }catch(error){if(transaction){await client.query('ROLLBACK');client.release();released=true;}if(res.headersSent){res.destroy();return;}throw error;}finally{if(!released)client.release();}
 }
}
