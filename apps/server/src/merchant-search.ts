import {merchantRuntimeKey,merchantRuntimeSearch} from './merchant-search-runtime';
import {Inject,Injectable} from '@nestjs/common';
import {randomUUID} from 'node:crypto';
import {writeStoreSearchTelemetry,searchFailureKind} from './store-search-resilience';
import type {PoolClient} from 'pg';
import {z} from 'zod';
import {Database} from './database';
import {BusinessError} from './errors';
import {MERCHANT_SEARCH_VERSION,merchantCoreKey,merchantQueryGrams} from './merchant-search-index';
import {normalizeStoreQuery,storeQueryTerms} from '../../../packages/domain/src/store-search';
import {storeNameBigrams} from '../../../packages/domain/src/store-name-index';
import {storeNameCompatibility,storeNameKey,storeNameProfile,type StoreNameProfile} from '../../../packages/domain/src/store-name-compatibility';
import {storeNameCandidateEvidence,storeSearchMatchEvidence,storeRelatedNameTerms} from '../../../packages/domain/src/store-search-evidence';
import type {StoreNameMethod} from '../../../packages/domain/src/store-name-index';

const group=z.coerce.number().int().min(0).max(2147483647);
const querySchema=z.object({q:z.string().trim().min(1).max(200).refine(value=>!value.includes('\0')),page:z.coerce.number().int().min(1).max(10000).default(1),groupGrid:group.optional(),groupRegion:group.optional()}).strict().refine(v=>!(v.groupGrid!==undefined&&v.groupRegion!==undefined));
const match=`(strpos(normalized_id,$2)>0 OR strpos(normalized_name,$2)>0 OR strpos(normalized_address,$2)>0 OR NOT EXISTS(SELECT 1 FROM unnest($3::text[]) term WHERE strpos(normalized_id,term)=0 AND strpos(normalized_name,term)=0 AND strpos(normalized_address,term)=0))`;
const literalWhere=`generation_id=$1 AND bd_merchant_search_literal_keys(normalized_id,normalized_name,normalized_address) @> $4::text[] AND ${match}`;
interface Retrieval {method:StoreNameMethod|null;kind:'literal'|'folded'|'names'|'empty';keys:string[];status:string;scoreByKey?:Record<string,number>;partial?:boolean}
/** Same guarded .6 weighted-bigram / exact-core policy as the frozen name-index evaluation, on persisted corpus statistics. */
export async function merchantNameRetrieval(client:PoolClient,generation:string,query:string,groupCount:number,skipLiteral=false):Promise<Retrieval>{
 const q=normalizeStoreQuery(query),terms=storeQueryTerms(query),grams=merchantQueryGrams(query);
 if(!skipLiteral&&(await client.query(`SELECT 1 FROM bd_merchant_search_document WHERE ${literalWhere} LIMIT 1`,[generation,q,terms,grams])).rowCount)return {kind:'literal',method:null,keys:[],status:'ready'};
 const key=storeNameKey(query);
 if(key&&(await client.query('SELECT 1 FROM bd_merchant_search_name WHERE generation_id=$1 AND name_key=$2',[generation,key])).rowCount)return {kind:'folded',method:'folded_name',keys:[key],status:'ready'};
 if(!key||[...key].length<3||/^\p{N}+$/u.test(key))return {kind:'empty',method:null,keys:[],status:'unsupported_query'};
 const profile=storeNameProfile(query);let tooMany=false;
 const compatible=(rows:any[])=>rows.filter(row=>storeNameCompatibility(profile,row.profile as StoreNameProfile)==='compatible');
 const documentCount=async(keys:string[])=>keys.length?Number((await client.query('SELECT count(*)::int AS count FROM bd_merchant_search_document WHERE generation_id=$1 AND name_key=ANY($2::text[])',[generation,keys])).rows[0].count):0;
 if([...key].length>=4){
  const bigrams=storeNameBigrams(key),length=[...key].length;
  const rows=(await client.query(`WITH query_weights AS MATERIALIZED(SELECT x.gram,coalesce(w.weight,1+ln($4::double precision+1)) AS weight FROM unnest($2::text[]) x(gram) LEFT JOIN bd_merchant_search_gram_weight w ON w.generation_id=$1 AND w.gram=x.gram),name_overlap AS MATERIALIZED(SELECT n.name_key,sum(q.weight) AS weight FROM query_weights q CROSS JOIN LATERAL(SELECT name_key FROM bd_merchant_search_name WHERE generation_id=$1 AND grams @> ARRAY[q.gram] AND key_length>=4 AND key_length>=$3::double precision*.45 AND key_length<=$3::double precision/.45 OFFSET 0) n GROUP BY n.name_key),scored AS(SELECT n.name_key,n.profile,2*o.weight/((SELECT sum(weight) FROM query_weights)+n.gram_weight) AS score FROM name_overlap o JOIN bd_merchant_search_name n ON n.generation_id=$1 AND n.name_key=o.name_key) SELECT * FROM scored WHERE score>=.6 ORDER BY score DESC,name_key`,[generation,bigrams,length,groupCount])).rows;
  const keys=compatible(rows).map(row=>row.name_key),count=await documentCount(keys);
  if(count>0&&count<=20)return {kind:'names',method:'weighted_name',keys,status:'ready',scoreByKey:Object.fromEntries(compatible(rows).map(row=>[row.name_key,Number(row.score)]))};tooMany=count>20;
 }
 if([...profile.core].length>=3){
  const rows=(await client.query('SELECT name_key,profile FROM bd_merchant_search_name WHERE generation_id=$1 AND core_key=$2',[generation,merchantCoreKey(profile)])).rows;
  const keys=compatible(rows).map(row=>row.name_key),count=await documentCount(keys);
  if(count>0&&count<=20)return {kind:'names',method:'exact_core',keys,status:'ready'};tooMany ||= count>20;
 }
 const related=storeRelatedNameTerms(query);
 if(related.length){
  const rejected=new Set(['branch_conflict','name_number_conflict','latin_core_conflict','business_family_conflict','mixed_script_location_conflict']);
  const rows=(await client.query(`SELECT name_key,profile FROM bd_merchant_search_name WHERE generation_id=$1 AND grams && $2::text[] AND EXISTS(SELECT 1 FROM unnest($3::text[]) term WHERE strpos(name_key,term)>0)`,[generation,[...new Set(related.flatMap(term=>storeNameBigrams(storeNameKey(term))))],related.map(storeNameKey)])).rows;
  const keys=rows.filter(row=>profile.latin.every(term=>row.name_key.includes(term))&&!rejected.has(storeNameCompatibility(profile,row.profile))).map(row=>row.name_key),count=await documentCount(keys);
  if(count>0&&count<=20)return {kind:'names',method:null,keys,status:'ready',partial:true};tooMany ||= count>20;
 }
 return {kind:'empty',method:null,keys:[],status:tooMany?'too_many_candidates':'no_candidates'};
}

@Injectable()
export class MerchantSearchService{
 constructor(@Inject(Database)private readonly db:Database){}
 async search(actorId:number,value:unknown){
  const parsed=querySchema.safeParse(value);if(!parsed.success||!normalizeStoreQuery(parsed.data.q))throw new BusinessError('VALIDATION','商户搜索参数无效',400);const input=parsed.data;
  const started=performance.now();const response=await this.db.transaction(async client=>{
   await client.query("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY; SET LOCAL jit=off; SET LOCAL work_mem='32MB'; SET LOCAL statement_timeout='2500ms'");
   if(!(await client.query("SELECT 1 FROM users WHERE id=$1 AND is_active=true AND status='active' AND role IN('admin','leader','staff')",[actorId])).rowCount)throw new BusinessError('FORBIDDEN','仅在岗用户可搜索商户',403);
   const capability=(await client.query("SELECT to_regclass('public.bd_merchant_search_log') AS telemetry,to_regclass('public.bd_merchant_search_active') AS active")).rows[0];
   if(!capability.active)throw new BusinessError('MERCHANT_SEARCH_NOT_READY','商户搜索索引尚未就绪',503);const telemetry=!!capability.telemetry;
   const generation=(await client.query('SELECT g.* FROM bd_merchant_search_active a JOIN bd_merchant_search_generation g ON g.id=a.generation_id WHERE g.ready')).rows[0];
   if(!generation||generation.strategy_version!==MERCHANT_SEARCH_VERSION)throw new BusinessError('MERCHANT_SEARCH_NOT_READY','商户搜索索引尚未就绪',503);
   const geographyVersion=await merchantRuntimeKey(client),runtimeKey=generation.id+':'+geographyVersion;
   let retrieval:Retrieval={kind:'literal',method:null,keys:[],status:'ready'};
   let result=await merchantRuntimeSearch(this.db,runtimeKey,input.q,input);
   if(result.matchedTotal===0){
    retrieval=await merchantNameRetrieval(client,generation.id,input.q,generation.group_count,true);
    const candidates=retrieval.keys.length?(await client.query('SELECT store_id AS "storeId",name_key FROM bd_merchant_search_document WHERE generation_id=$1 AND name_key=ANY($2::text[])',[generation.id,retrieval.keys])).rows.map(row=>({storeId:row.storeId,score:retrieval.scoreByKey?.[row.name_key]??0})):[];
    result=await merchantRuntimeSearch(this.db,runtimeKey,input.q,input,candidates);
   }
   const {storeIds,key,...metadata}=result;
   const details=storeIds.length?(await client.query(`SELECT m.store_id AS "merchantId",m.name,m.address,coalesce(d.lat,m.lat) AS lat,coalesce(d.lon,m.lon) AS lon FROM bd_merchant_search_document m LEFT JOIN bd_merchant_detail d ON d.store_id=m.store_id WHERE m.generation_id=$1 AND m.store_id=ANY($2::text[])`,[generation.id,storeIds])).rows:[];
   const byId=new Map(details.map(row=>[row.merchantId,row]));
   const resultPage={...metadata,items:storeIds.map(id=>byId.get(id)).filter(Boolean)};
   return {_telemetry:telemetry,...resultPage,items:resultPage.items.map((row:any)=>{const source={storeId:row.merchantId,snapshot:{name:row.name,address:row.address??''}};return {...row,searchMatch:retrieval.method?storeNameCandidateEvidence(input.q,source,retrieval.method):storeSearchMatchEvidence(input.q,source,retrieval.partial?'related':'literal')};}),page:input.page,pageSize:20,searchVersion:MERCHANT_SEARCH_VERSION,sourceVersion:generation.id,geographyVersion,nameMatchMethod:retrieval.method,searchMode:retrieval.partial||retrieval.method==='weighted_name'||retrieval.method==='exact_core'?'related':'literal',status:retrieval.status};
  });
  const {_telemetry,...result}=response;let searchLogId:string|null=null;
  if(_telemetry){try{const id=randomUUID();await writeStoreSearchTelemetry(this.db,`INSERT INTO bd_merchant_search_log(id,actor_id,query_text,source_version,strategy_version,group_grid_id,group_region_id,page,matched_total,filtered_total,result_merchant_ids,duration_ms,geography_version) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12,$13)`,[id,actorId,input.q,result.sourceVersion,result.searchVersion,input.groupGrid??null,input.groupRegion??null,input.page,result.matchedTotal,result.total,JSON.stringify(result.items.map((item:any)=>item.merchantId)),Math.round(performance.now()-started),result.geographyVersion]);searchLogId=id;}catch(error){console.error(JSON.stringify({event:'merchant_search_log_failed',reason:searchFailureKind(error)}));}}
  return {...result,searchLogId};
 }
 async feedback(actorId:number,value:unknown){
  const parsed=z.object({eventId:z.string().uuid(),searchLogId:z.string().uuid(),merchantId:z.string().min(1).max(500),kind:z.enum(['opened','confirmed'])}).strict().safeParse(value);
  if(!parsed.success)throw new BusinessError('VALIDATION','商户搜索反馈参数无效',400);const input=parsed.data;
  return this.db.transaction(async client=>{
   if(!(await client.query("SELECT 1 FROM users WHERE id=$1 AND is_active AND status='active' AND role IN('admin','leader','staff')",[actorId])).rowCount)throw new BusinessError('FORBIDDEN','当前账号不可反馈',403);
   const log=(await client.query('SELECT result_merchant_ids FROM bd_merchant_search_log WHERE id=$1 AND actor_id=$2',[input.searchLogId,actorId])).rows[0];
   if(!log||!Array.isArray(log.result_merchant_ids)||!log.result_merchant_ids.includes(input.merchantId))throw new BusinessError('FORBIDDEN','只能反馈此次搜索实际返回的商户',403);
   const prior=(await client.query('SELECT * FROM bd_merchant_search_feedback WHERE event_id=$1',[input.eventId])).rows[0];
   if(prior){if(prior.actor_id!==actorId||prior.search_log_id!==input.searchLogId||prior.merchant_id!==input.merchantId||prior.kind!==input.kind)throw new BusinessError('REQUEST_ID_CONFLICT','反馈标识已用于另一条反馈',409);return {accepted:true,eventId:input.eventId,duplicate:true};}
   const inserted=await client.query('INSERT INTO bd_merchant_search_feedback(event_id,search_log_id,actor_id,merchant_id,kind) VALUES($1,$2,$3,$4,$5) ON CONFLICT(event_id) DO NOTHING RETURNING event_id',[input.eventId,input.searchLogId,actorId,input.merchantId,input.kind]);
   if(!inserted.rowCount){const existing=(await client.query('SELECT * FROM bd_merchant_search_feedback WHERE event_id=$1',[input.eventId])).rows[0];if(existing.actor_id!==actorId||existing.search_log_id!==input.searchLogId||existing.merchant_id!==input.merchantId||existing.kind!==input.kind)throw new BusinessError('REQUEST_ID_CONFLICT','反馈标识已用于另一条反馈',409);}
   return {accepted:true,eventId:input.eventId,duplicate:!inserted.rowCount};
  });
 }
}
