import type {PoolClient} from 'pg';
import {Body,Controller,Get,Inject,Injectable,Param,Post,Req,Res} from '@nestjs/common';import type {Request,Response} from 'express';import {z} from 'zod';import {randomUUID} from 'node:crypto';import {mkdir,rename,unlink,readFile,writeFile} from 'node:fs/promises';import {createReadStream} from 'node:fs';import {join,resolve} from 'node:path';import ExcelJS from 'exceljs';
import {Database} from './database';import {AuthService} from './auth';import {BusinessError} from './errors';import {PayrollPaymentsService} from './payroll-payments';import {PayrollService} from './payroll';import {validMonth} from '../../../packages/domain/src/payroll';import {monthRange} from './business-dates';
function directory(){return resolve(process.env.NEXT_TASK_DIRECTORY||'data/refactor/jobs','payroll-exports');}
@Injectable()
export class PayrollExportService{
 constructor(@Inject(Database)private readonly db:Database,@Inject(PayrollPaymentsService)private readonly payments:PayrollPaymentsService,@Inject(PayrollService)private readonly payroll:PayrollService){}
 async prepare(month:string,half:number,token:string,actor:number){
  const file=await this.db.transaction(async client=>{await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');return this.buildInTransaction(client,month,half,token,actor);});
  try{return {...file,registration:await this.payments.register(month,half,actor,token)};}catch(error){await this.remove(file.id);throw error;}
 }
 async remove(id:string){if(!z.string().uuid().safeParse(id).success)return;await Promise.all(['.xlsx','.json','.tmp'].map(suffix=>unlink(join(directory(),id+suffix)).catch(()=>{})));}
 async buildInTransaction(client:PoolClient,month:string,half:number,token:string,actor:number){
  const preview=await this.payments.previewInTransaction(client,month,half);if(preview.token!==token)throw new BusinessError('PAYROLL_PREVIEW_CHANGED','金额或找平余额已变化，请重新预览再导出',409);
  const data=await this.payroll.read(month,undefined,client);const rowMap=new Map(data.rows.map(row=>[row.code,row]));const id=randomUUID(),root=directory();await mkdir(root,{recursive:true,mode:0o700});const temporary=join(root,id+'.tmp'),path=join(root,id+'.xlsx');
  try{
   const workbook=new ExcelJS.stream.xlsx.WorkbookWriter({filename:temporary,useSharedStrings:false,useStyles:false});const sheet=workbook.addWorksheet(half===1?'上半月发薪1-15':'下半月发薪16-月末');sheet.addRow(['员工编号','姓名','该期点数','有效店数','点数(1点/2点)','2点成功率','奖金(円)','总金额(円)','找平金额(円)','应该付金额(円)']).commit();
   for(const item of preview.rows){const row=rowMap.get(item.personCode),stats=half===1?row?.half1:row?.half2;sheet.addRow([item.personCode,item.name,item.points,stats?.records??0,`${stats?.p1??0} / ${stats?.p2??0}`,stats&&(stats.p1+stats.p2)?stats.p2/(stats.p1+stats.p2):0,item.bonus,item.grossAmount,item.adjustment,item.payable]).commit();}
   sheet.addRow(['合计','','','','','',preview.rows.reduce((sum,row)=>sum+row.bonus,0),preview.rows.reduce((sum,row)=>sum+row.grossAmount,0),preview.rows.reduce((sum,row)=>sum+row.adjustment,0),preview.rows.reduce((sum,row)=>sum+row.payable,0)]).commit();sheet.commit();
   const bounds=monthRange(month),start=half===1?bounds.start:month+'-16',end=half===1?month+'-15':bounds.end;let cursor=0;
   // Order by person/date/ID, and close each sheet before the next person to bound output buffers.
   let currentCode='',detail:ExcelJS.Worksheet|undefined;let cursorCode='',cursorDate=start;const names=new Set<string>();
   for(;;){const rows=(await client.query<{id:number;code:string;date:string;name:string;store:string;modified:string;visible:string;deploy:string;points:number}>(`SELECT f.id,f.person_code AS code,f.japan_date::text AS date,coalesce(p.display_name,f.person_code) AS name,r.store_name_local_raw AS store,r.modified_raw AS modified,r.visible_raw AS visible,r.deploy_raw AS deploy,f.points FROM formal_records f JOIN raw_records r ON r.id=f.raw_record_id LEFT JOIN persons p ON p.code=f.person_code WHERE f.japan_date BETWEEN $1 AND $2 AND f.person_code IS NOT NULL AND (f.person_code,f.japan_date,f.id)>($3::text,$4::date,$5::int) ORDER BY f.person_code,f.japan_date,f.id LIMIT 1000`,[start,end,cursorCode,cursorDate,cursor])).rows;if(!rows.length)break;
    for(const row of rows){if(row.code!==currentCode){detail?.commit();currentCode=row.code;const base=`${row.name}(${row.code})`.replace(/[\\/*?:\[\]]/g,'-').slice(0,31);let name=base,index=2;while(names.has(name)){const suffix='-'+index++;name=base.slice(0,31-suffix.length)+suffix;}names.add(name);detail=workbook.addWorksheet(name);detail.addRow(['日期','店铺名','巡店时间','S1(审核状态)','投放','点数','员工编号']).commit();}detail!.addRow([row.date,row.store,row.modified,row.visible,row.deploy,row.points,row.code]).commit();}
    const last=rows.at(-1)!;cursor=last.id;cursorCode=last.code;cursorDate=last.date;
   }
   detail?.commit();await workbook.commit();
   // Build failure never registers payroll. A changed snapshot aborts before any financial write.
   await rename(temporary,path);
   await writeFile(join(root,id+'.json'),JSON.stringify({actor,month,half,filename:`发薪表_${half===1?'上半月':'下半月'}_${month}.xlsx`}),{mode:0o600});
   return {id,downloadUrl:'/api/payroll/exports/'+id};
  }catch(error){await unlink(temporary).catch(()=>{});await unlink(path).catch(()=>{});await unlink(join(root,id+'.json')).catch(()=>{});throw error;}
 }
 async file(id:string){if(!z.string().uuid().safeParse(id).success)throw new BusinessError('VALIDATION','导出编号无效',400);try{const metadata=JSON.parse(await readFile(join(directory(),id+'.json'),'utf8'));return {path:join(directory(),id+'.xlsx'),filename:metadata.filename as string};}catch{throw new BusinessError('NOT_FOUND','导出文件不存在，请重新导出',404);}}
}
@Controller('payroll/exports')
export class PayrollExportController{
 constructor(@Inject(AuthService)private readonly auth:AuthService,@Inject(PayrollExportService)private readonly service:PayrollExportService){}
 @Post()async prepare(@Req()req:Request,@Body()body:unknown){const {user}=await this.auth.require(req,{admin:true,write:true});const parsed=z.object({month:z.string().refine(validMonth),half:z.number().int().min(1).max(2),token:z.string().regex(/^[a-f0-9]{64}$/),confirm:z.literal('EXPORT_AND_REGISTER_PAYROLL')}).strict().safeParse(body);if(!parsed.success)throw new BusinessError('VALIDATION','请检查发薪导出确认信息',400);return this.service.prepare(parsed.data.month,parsed.data.half,parsed.data.token,user.id);}
 @Get(':id')async download(@Req()req:Request,@Param('id')id:string,@Res()res:Response){await this.auth.require(req,{admin:true});const file=await this.service.file(id);res.setHeader('Content-Type','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');res.setHeader('Content-Disposition',`attachment; filename="payroll.xlsx"; filename*=UTF-8''${encodeURIComponent(file.filename)}`);const stream=createReadStream(file.path);stream.on('error',()=>{if(res.headersSent)res.destroy();else res.status(404).json({code:'NOT_FOUND',message:'导出文件不存在'});});res.on('close',()=>stream.destroy());stream.pipe(res);}
}
