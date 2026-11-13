import {Inject,Injectable} from '@nestjs/common';
import type {PoolClient} from 'pg';
import {createHash} from 'node:crypto';
import {z} from 'zod';
import type {McpActor} from './mcp';
import {McpReadError} from './mcp-read';
import {BusinessError} from './errors';
import {SettlementService} from './settlement';
import {assertMonthsOpen} from './financial-control';
import {monthRange} from './business-dates';
import {settlementPoints} from '../../../packages/domain/src/settlement';
const schema=z.object({month:z.string().regex(/^[0-9]{4}-(0[1-9]|1[0-2])$/).refine(value=>Number(value.slice(0,4))>=1&&Number(value.slice(0,4))<=9998),action:z.enum(['preview','run']).default('preview'),preview_id:z.number().int().positive().nullable().optional(),confirm_text:z.string().nullable().optional()}).strict();
export const mcpRebuildTool={name:'visit_rebuild',title:'月度重算',description:'仅管理员：action=preview统计当前正式表、源记录判定/点数分布，并返回同凭据30分钟有效preview_id；不执行重算。action=run需write scope、preview_id及“确认重算 YYYY-MM”。数据/规则/账表变化需重新预览；执行后预览失效。复用现有结算事务，封账、缺源、未决申诉拒绝；历史实发与快照保护保留。',inputSchema:{type:'object' as const,properties:{month:{type:'string',pattern:'^[0-9]{4}-(0[1-9]|1[0-2])$'},action:{type:'string',enum:['preview','run'],default:'preview'},preview_id:{type:['integer','null'],minimum:1},confirm_text:{type:['string','null']}},required:['month'],additionalProperties:false},annotations:{readOnlyHint:false,destructiveHint:true,idempotentHint:false,openWorldHint:false}};
type Row=Record<string,any>;
@Injectable()
export class McpRebuildService{
 constructor(@Inject(SettlementService)private readonly settlement:SettlementService){}
 private async state(client:PoolClient,month:string){
  const bounds=monthRange(month);
  const raw=(await client.query(`SELECT id,import_id,store_id_raw,modified_raw,visible_raw,deploy_raw,submitter_code,clean_status,
    md5(coalesce(store_name_local_raw,'')) AS name_digest,filtered_by_raw_id,confirm_state,filter_reason FROM raw_records WHERE left(modified_raw,7)=$1 ORDER BY id`,[month])).rows;
  const formal=(await client.query('SELECT id,raw_record_id,points,person_code FROM formal_records WHERE japan_date BETWEEN $1 AND $2 ORDER BY id',[bounds.start,bounds.end])).rows;
  const rules=(await client.query<{id:number;point_rules:unknown[]|null;visible:unknown}>('SELECT id,layout->\'point_rules\' AS point_rules,layout->\'value_map\'->\'visible\' AS visible FROM imports WHERE id=ANY($1::int[]) ORDER BY id',[[...new Set(raw.map(row=>row.import_id))]])).rows;
  const merged=(await client.query<{id:string}>('SELECT DISTINCT s.store_id_raw AS id FROM store_entities s JOIN store_merge_logs m ON m.entity_id=s.id WHERE s.master_id<>s.id ORDER BY id')).rows.map(row=>row.id);
  const configured=(process.env.DEDUP_SUB_STORES??'0101047092026031200555097|0202047092026032480084411|0101047092026081903348200|0101047092026060970019734').split('|').filter(Boolean);
  const financial=(await client.query(`SELECT
    (SELECT md5(coalesce(string_agg(row_to_json(t)::text,'' ORDER BY id),'')) FROM payroll_payments t) AS payments,
    (SELECT md5(coalesce(string_agg(row_to_json(t)::text,'' ORDER BY id),'')) FROM payroll_adjusts t) AS adjusts,
    (SELECT md5(coalesce(string_agg(row_to_json(t)::text,'' ORDER BY id),'')) FROM payroll_settlement_links t) AS links,
    (SELECT md5(coalesce(string_agg(row_to_json(t)::text,'' ORDER BY id),'')) FROM payroll_period_rows t) AS periods,
    (SELECT md5(coalesce(string_agg(row_to_json(t)::text,'' ORDER BY id),'')) FROM sys_configs t) AS config,
    (SELECT md5(coalesce(string_agg(row_to_json(t)::text,'' ORDER BY id),'')) FROM appeals t WHERE import_id=ANY($1::int[])) AS appeals`,[[...new Set(raw.map(row=>row.import_id))]])).rows[0];
  const fingerprint=createHash('sha256').update(JSON.stringify({month,raw,formal,rules,merged,configured,financial,bonusEnv:[process.env.BONUS_GROUP,process.env.BONUS_AMOUNT,process.env.BONUS_GROUP_SCHEDULE,process.env.BONUS_AMOUNT_SCHEDULE]})).digest('hex');
  const byStatus:Record<string,number>={valid:0,cross_file_dup:0,master_late:0,from_sub:0,no_ref:0,blank:0,other:0},byPoints:Record<string,number>={},subIds=new Set([...configured,...merged]);let subHits=0;
  const ruleMap=new Map(rules.map(row=>[row.id,row.point_rules]));
  for(const row of raw){const status=row.clean_status==='visible_blank'?'blank':row.clean_status;byStatus[Object.hasOwn(byStatus,status)?status:'other']++;
   if(row.clean_status==='valid'){const points=settlementPoints(row.modified_raw?.slice(0,10)??null,row.deploy_raw,row.visible_raw??'',ruleMap.get(row.import_id));byPoints[points]=(byPoints[points]??0)+1;if(subIds.has(row.store_id_raw))subHits++;}}
  return {fingerprint,rawTotal:raw.length,data:{month,formal_rows_now:formal.length,formal_points_now:formal.reduce((sum,row)=>sum+(row.points||0),0),raw_total:raw.length,raw_by_status:byStatus,raw_by_points:byPoints,estimated_insert_rows:byStatus.valid,dedup_sub_hits:subHits,affected_persons:[...new Set<string>(raw.map(row=>row.submitter_code).filter(Boolean))].sort(),notes:['估算基于当前clean_status，重判可能改变分类；estimated_insert_rows为入表上限，不是承诺结果']}};
 }
 async run(client:PoolClient,actor:McpActor,args:Record<string,unknown>,auditId:number):Promise<Record<string,unknown>>{
  if(actor.role!=='admin')throw new McpReadError('FORBIDDEN_TOOL','仅管理员可预演或执行重算');
  if(args.action==='run'&&!actor.scopes.includes('write'))throw new McpReadError('FORBIDDEN_TOOL','执行重算需要write scope');
  const parsed=schema.safeParse(args);if(!parsed.success)throw new McpReadError('BAD_PARAM','重算月份、动作或预演编号无效');const input=parsed.data;
  if(input.action==='run'&&input.confirm_text?.trim()!==`确认重算 ${input.month}`)throw new McpReadError('CONFIRM_REQUIRED','确认语不匹配',`请复述确认语：确认重算 ${input.month}`);
  try{
   // Same lock order as the settlement service, held through version recheck and business commit.
   await client.query("SELECT pg_advisory_xact_lock(hashtext('settlement-write'),0)");await client.query("SELECT pg_advisory_xact_lock(hashtext('payroll-financial-write'),0)");
   await assertMonthsOpen(client,[input.month]);
   if(input.action==='preview'){
    const snapshot=await this.state(client,input.month);await client.query('UPDATE mcp_audit_log SET params_json=$2 WHERE id=$1',[auditId,JSON.stringify({month:input.month,preview:true,fingerprint:snapshot.fingerprint})]);
    const audit=(await client.query<{created_at:Date}>('SELECT created_at FROM mcp_audit_log WHERE id=$1',[auditId])).rows[0];
    return {...snapshot.data,preview_id:auditId,expires_at:new Date(audit.created_at.getTime()+30*60000).toISOString().replace('.000Z','Z')};
   }
   if(!input.preview_id)throw new McpReadError('PREVIEW_REQUIRED','请先预演，再提供preview_id');
   const preview=(await client.query<Row>('SELECT token_id,user_id,tool,ok,created_at,params_json FROM mcp_audit_log WHERE id=$1 FOR UPDATE',[input.preview_id])).rows[0];
   if(!preview||preview.tool!=='visit_rebuild'||preview.ok!==true||preview.token_id!==actor.tokenId||preview.user_id!==actor.uid)throw new McpReadError('PREVIEW_INVALID','预演不存在或不属于当前凭据');
   let metadata:Row;try{metadata=JSON.parse(preview.params_json);}catch{throw new McpReadError('PREVIEW_INVALID','预演数据无效，请重新预演');}
   if(!metadata.preview||metadata.month!==input.month)throw new McpReadError('PREVIEW_INVALID','预演月份不匹配');
   if(metadata.consumed_by)throw new McpReadError('PREVIEW_USED','该预演已执行，请重新查询和预演');
   if(Date.now()-preview.created_at.getTime()>30*60000||preview.created_at.getTime()>Date.now()+60000)throw new McpReadError('PREVIEW_EXPIRED','预演已过期，请重新预演');
   const current=await this.state(client,input.month);if(current.fingerprint!==metadata.fingerprint)throw new McpReadError('PREVIEW_CHANGED','源数据、规则或账表已变化，请重新预演');
   if(!current.rawTotal)throw new McpReadError('REBUILD_SOURCE_MISSING','该月缺少源记录，不能执行重算');
   const result=await this.settlement.rebuildTransaction(client,input.month,actor.uid);
   await client.query('UPDATE mcp_audit_log SET params_json=$2 WHERE id=$1',[input.preview_id,JSON.stringify({...metadata,consumed_by:auditId})]);
   return {month:input.month,files:result.files,formal_before:result.formalBefore,formal_after:result.formalAfter,points_before:result.pointsBefore,points_after:result.pointsAfter,hint:'已按当前结算事务完成重算；历史实发未覆盖，结果请结合工资/找平查询核对'};
  }catch(error){if(error instanceof BusinessError)throw new McpReadError(error.code,error.message,error.getStatus()===409?'先处理限制，再重新预演':'');throw error;}
 }
}
