import {Controller,Get,Inject,Injectable,Param,Query,Req} from '@nestjs/common';import type {Request} from 'express';import {z} from 'zod';
import {Database} from './database';import {AuthService} from './auth';import {BusinessError} from './errors';
export const reportQuery=z.object({bucket:z.enum(['','valid','master_late','from_sub','cross_file_dup','blank','no_ref','appealing']).default(''),q:z.string().trim().max(200).default(''),page:z.coerce.number().int().min(1).max(10000).default(1)}).strict();
@Injectable()
export class ImportReportService{
 constructor(@Inject(Database)private readonly db:Database){}
 async read(id:number,query:z.infer<typeof reportQuery>){return this.db.transaction(async client=>{
  await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');
  const file=(await client.query('SELECT id,file_name AS name,status,format,parsed_rows AS "parsedRows",warnings,errors FROM imports WHERE id=$1',[id])).rows[0];if(!file)throw new BusinessError('NOT_FOUND','文件不存在',404);
  const summary=(await client.query(`SELECT count(*)::int AS "rawTotal",count(*) FILTER(WHERE clean_status='valid')::int AS valid,
   count(*) FILTER(WHERE clean_status='master_late')::int AS "masterLate",count(*) FILTER(WHERE clean_status='from_sub')::int AS "fromSub",count(*) FILTER(WHERE clean_status='cross_file_dup')::int AS "crossFileDup",count(*) FILTER(WHERE clean_status IN('blank','visible_blank'))::int AS blank,count(*) FILTER(WHERE clean_status='no_ref')::int AS "noRef",
   count(*) FILTER(WHERE clean_status IN('master_late','from_sub','cross_file_dup','blank','visible_blank','no_ref'))::int AS "filteredTotal",
   ARRAY(SELECT DISTINCT left(modified_raw,7) FROM raw_records WHERE import_id=$1 AND length(modified_raw)>=7 ORDER BY 1) AS months,
   (SELECT count(*)::int FROM appeals WHERE import_id=$1 AND status='pending') AS "pendingAppeals",
   (SELECT count(*)::int FROM formal_records WHERE import_id=$1) AS "formalTotal"
   FROM raw_records WHERE import_id=$1`,[id])).rows[0];
  const result=(await client.query(`WITH matching AS(
   SELECT r.*,coalesce(p.display_name,nullif(r.submitter_raw,''),'未识别') AS person_name FROM raw_records r LEFT JOIN persons p ON p.code=r.submitter_code
   WHERE r.import_id=$1 AND ($2='' OR ($2='appealing' AND EXISTS(SELECT 1 FROM appeals a WHERE a.raw_record_id=r.id)) OR ($2='blank' AND r.clean_status IN('blank','visible_blank')) OR ($2 NOT IN('appealing','blank') AND r.clean_status=$2))
    AND ($3='' OR r.store_id_raw ILIKE $3 OR r.store_name_local_raw ILIKE $3 OR r.submitter_raw ILIKE $3 OR p.display_name ILIKE $3)
  ), paged AS(SELECT * FROM matching ORDER BY modified_raw,excel_row,id OFFSET $4 LIMIT 50)
  SELECT (SELECT count(*)::int FROM matching) AS total,coalesce((SELECT json_agg(row) FROM(
   SELECT r.id,r.submitter_code AS code,r.person_name AS name,r.store_id_raw AS "storeId",r.store_name_local_raw AS "storeName",left(r.modified_raw,10) AS date,coalesce(r.visible_raw,'') AS visible,coalesce(r.deploy_raw,'') AS deploy,
    CASE WHEN r.clean_status='visible_blank' THEN 'blank' ELSE coalesce(r.clean_status,'') END AS status,r.excel_row AS "excelRow",r.sheet_name AS sheet,
    EXISTS(SELECT 1 FROM formal_records f WHERE f.import_id=$1 AND f.raw_record_id=r.id) AS "inFormal",
    (SELECT json_build_object('status',a.status,'reason',coalesce(a.reason,'')) FROM appeals a WHERE a.raw_record_id=r.id ORDER BY a.id DESC LIMIT 1) AS appeal
   FROM paged r ORDER BY r.modified_raw,r.excel_row,r.id)row),'[]') AS items`,[id,query.bucket,query.q?'%'+query.q+'%':'',(query.page-1)*50])).rows[0];
  return {file,summary,...result,page:query.page,pageSize:50,pages:Math.max(1,Math.ceil(result.total/50)),bucket:query.bucket,q:query.q};
 });}
}
@Controller('imports')
export class ImportReportController{
 constructor(@Inject(AuthService)private readonly auth:AuthService,@Inject(ImportReportService)private readonly service:ImportReportService){}
 @Get(':id/report')async read(@Req()req:Request,@Param('id')id:string,@Query()query:unknown){await this.auth.require(req,{admin:true});const identity=z.coerce.number().int().positive().safeParse(id),input=reportQuery.safeParse(query);if(!identity.success||!input.success)throw new BusinessError('VALIDATION','报告筛选条件无效',400);return this.service.read(identity.data,input.data);}
}
