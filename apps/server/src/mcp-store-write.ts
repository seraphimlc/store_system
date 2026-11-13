import {Inject,Injectable} from '@nestjs/common';
import type {PoolClient} from 'pg';
import {randomUUID,createHash} from 'node:crypto';
import {z} from 'zod';
import type {McpActor} from './mcp';
import {McpReadError} from './mcp-read';
import {StoreMasterService,StoreSelection} from './store-master';
import {BusinessError} from './errors';
const id=z.number().int().positive().max(2147483647);
const schema=z.object({view:z.enum(['search','pairs']).nullable().optional(),action:z.enum(['merge','split','skip','apply']),q:z.string().nullable().optional(),limit:z.number().int().positive().optional(),pair_id:id.nullable().optional(),keep:id.nullable().optional(),entity_id:id.nullable().optional(),kind:z.enum(['exact','fuzzy']).nullable().optional(),note:z.string().max(4000).nullable().optional(),confirm_text:z.string().nullable().optional(),dry_run:z.boolean().default(false),token:z.string().regex(/^[a-f0-9]{64}$/).optional(),request_id:z.string().uuid().optional()}).strict();
export function isMcpStoreAction(tool:string,args:Record<string,unknown>){return tool==='visit_store'&&args.action!=null;}
export function isMcpStoreWrite(tool:string,args:Record<string,unknown>){return isMcpStoreAction(tool,args)&&args.dry_run!==true;}
export const mcpStoreDefinition={description:'仅管理员：view=search/pairs查询；action=merge(pair_id,keep)/skip(pair_id)/split(entity_id)/apply。dry_run=true预览并返回token（read凭据可用），正式写入需write scope，可选token防覆盖、request_id(UUID)防重复。split确认语“确认拆分 ID”，apply为“确认批量应用合并”。复用主档与封账保护，不改历史实发。所有操作同步返回执行结果，可能耗时。',inputSchema:{type:'object' as const,properties:{view:{type:['string','null'],enum:['search','pairs',null]},action:{type:['string','null'],enum:['merge','split','skip','apply',null]},q:{type:['string','null']},limit:{type:'integer',minimum:1,default:20},pair_id:{type:['integer','null'],minimum:1},keep:{type:['integer','null'],minimum:1},entity_id:{type:['integer','null'],minimum:1},kind:{type:['string','null'],enum:['exact','fuzzy',null]},note:{type:['string','null']},confirm_text:{type:['string','null']},dry_run:{type:'boolean',default:false},token:{type:'string',pattern:'^[a-f0-9]{64}$'},request_id:{type:'string',format:'uuid'}},additionalProperties:false},annotations:{readOnlyHint:false,destructiveHint:true,idempotentHint:false,openWorldHint:false}};
@Injectable()
export class McpStoreWriteService{
 constructor(@Inject(StoreMasterService)private readonly stores:StoreMasterService){}
 async run(client:PoolClient,actor:McpActor,args:Record<string,unknown>,auditId:number):Promise<Record<string,unknown>>{
  if(actor.role!=='admin'||args.dry_run!==true&&!actor.scopes.includes('write'))throw new McpReadError('FORBIDDEN_TOOL','主档操作仅管理员可用，正式写入需要write scope');
  const parsed=schema.safeParse(args);if(!parsed.success)throw new McpReadError('BAD_PARAM','请检查主档操作、实体、候选对及版本参数');
  const input=parsed.data;
  // User text must not impersonate the business service's idempotency marker.
  if(input.note?.includes('[request:'))throw new McpReadError('BAD_PARAM','备注包含保留的请求标记');
  let selection:StoreSelection;
  if(input.action==='merge'){if(!input.pair_id||!input.keep)throw new McpReadError('BAD_PARAM','merge需要pair_id和keep');selection={action:'merge-pair',pairId:input.pair_id,keepId:input.keep};}
  else if(input.action==='skip'){if(!input.pair_id)throw new McpReadError('BAD_PARAM','skip需要pair_id');selection={action:'skip-pair',pairId:input.pair_id};}
  else if(input.action==='split'){if(!input.entity_id)throw new McpReadError('BAD_PARAM','split需要entity_id');selection={action:'split',entityId:input.entity_id};}
  else selection={action:'recommended'};
  if(!input.dry_run){const expected=input.action==='split'?`确认拆分 ${input.entity_id}`:input.action==='apply'?'确认批量应用合并':null;if(expected&&input.confirm_text?.trim()!==expected)throw new McpReadError('CONFIRM_REQUIRED','确认语不匹配',`请复述确认语：${expected}`);}
  try{
   if(input.dry_run){const preview=await this.stores.previewInTransaction(client,selection);return {dry_run:true,action:input.action,...preview,hint:'预览不修改主档或工资；正式执行请携带token及所需确认语'};}
   // The service locks before revalidating the supplied version. No background queue or second transaction.
   const requestId=input.request_id??randomUUID();
   const requestHash=createHash('sha256').update(JSON.stringify({selection,token:input.token??null,note:input.note??null,kind:input.kind??null})).digest('hex');
   await client.query('SELECT pg_advisory_xact_lock(hashtext($1),hashtext($2))',[String(actor.uid),'mcp-store:'+requestId]);
   const oldRequest=(await client.query("SELECT params_json FROM mcp_audit_log WHERE user_id=$1 AND tool='visit_store' AND ok=true AND params_json::jsonb->>'request_id'=$2 ORDER BY id LIMIT 1",[actor.uid,requestId])).rows[0];
   if(oldRequest){
    const identity=JSON.parse(oldRequest.params_json);
    if(identity.request_hash!==requestHash)throw new McpReadError('REQUEST_ID_CONFLICT','request_id已用于不同的主档操作');
    return {reused:true,request_id:requestId,hint:'该请求已执行，请查询当前主档；未再次合并或重算'};
   }
   // The only persisted arguments are an opaque request id and digest, never user notes or personal data.
   await client.query('UPDATE mcp_audit_log SET params_json=$2 WHERE id=$1',[auditId,JSON.stringify({request_id:requestId,request_hash:requestHash})]);
   const preview=await this.stores.previewInTransaction(client,selection);
   const pair=input.pair_id?(await client.query('SELECT kind,entity_a,entity_b FROM store_pairs WHERE id=$1',[input.pair_id])).rows[0]:null;
   if(pair&&input.kind&&pair.kind!==input.kind)throw new McpReadError('BAD_PARAM','kind与候选对类型不一致');
   const old=input.entity_id?(await client.query('SELECT master_id FROM store_entities WHERE id=$1',[input.entity_id])).rows[0]:null;
   const result=await this.stores.processInTransaction(client,{requestId,actor:actor.uid,selection,token:input.token??preview.token,...input.note?{note:input.note}:{}});
   if(!result.ok)throw new McpReadError('INTERNAL','主档处理失败');
   if('reused'in result&&result.reused)throw new McpReadError('REQUEST_ID_CONFLICT','请求标识已在其他入口使用，请核对原结果或使用新的request_id');
   const recomputed='recomputed'in result?result.recomputed:[],sealed='skippedSealed'in result?result.skippedSealed:[];
   const snapshot=async(entityId:number)=>{const row=(await client.query('SELECT id,store_id_raw,name_local,name_norm,city,address_local,master_id,master_store_id FROM store_entities WHERE id=$1',[entityId])).rows[0];return {...row,name_local:row.name_local||'',name_norm:row.name_norm||'',city:row.city||'',address_local:row.address_local||'',is_master:row.master_id===row.id};};
   if(input.action==='merge'){const other=pair!.entity_a===input.keep?pair!.entity_b:pair!.entity_a,merged=await snapshot(other);return {pair_id:input.pair_id,kind:pair!.kind,keep_entity_id:input.keep,entity:await snapshot(input.keep!),merged_entity_id:other,merged_store_id:merged.store_id_raw,affected_entities:1,recomputed_months:recomputed,skipped_sealed:sealed,request_id:requestId,note:'该对已合并；受影响的开放月份按现有服务重算，历史发放保护保留'};}
   if(input.action==='split')return {entity_id:input.entity_id,entity:await snapshot(input.entity_id!),from_master:old!.master_id,to_master:input.entity_id,affected_entities:1,recomputed_months:recomputed,skipped_sealed:sealed,request_id:requestId,note:'已拆回自己为主档'};
   if(input.action==='skip')return {pair_id:input.pair_id,kind:pair!.kind,status:'skip',request_id:requestId,note:'已标记为不同店，该对不再显示为候选'};
   return {groups_applied:'groupsApplied'in result?result.groupsApplied:0,entities_merged:'mergedEntities'in result?result.mergedEntities:0,groups_left_manual:'leftGroups'in result?result.leftGroups:0,recomputed_months:recomputed,skipped_sealed:sealed,request_id:requestId,hint:'推荐组已按现有主档服务处理；跨城市组留待人工核对'};
  }catch(error){if(error instanceof BusinessError)throw new McpReadError(error.code,error.message,error.getStatus()===409?'重新预览并核对后再执行':'');throw error;}
 }
}
