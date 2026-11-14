import {createHash,randomUUID} from 'node:crypto';
import type {PoolClient} from 'pg';
import {normalizeStoreQuery,storeQueryTerms} from '../../../packages/domain/src/store-search';
import {storeNameBigrams} from '../../../packages/domain/src/store-name-index';
import {createStoreNameProfiler,storeNameKey} from '../../../packages/domain/src/store-name-compatibility';

export const MERCHANT_SEARCH_VERSION='merchant-guarded-v2';
export const MERCHANT_SOURCE='public.kanto_merchants_v20261029';
/** Persist both single characters and bigrams. Verification still checks the actual fields: grams are not evidence. */
export function merchantLiteralGrams(fields:string[]){
 const result=new Set<string>();
 for(const field of fields){for(const char of field)result.add(char);for(const gram of storeNameBigrams(field))result.add(gram);}
 return [...result].sort();
}
export function merchantQueryGrams(query:string){
 return [...new Set(storeQueryTerms(query).flatMap(term=>[...term].length<2?[term]:storeNameBigrams(term)))].sort();
}
export function merchantCoreKey(profile:{core:string;branch:string|null}){return JSON.stringify([profile.core,profile.branch]);}
/** Caller owns a REPEATABLE READ transaction. The old generation stays queryable until atomic publication/commit. */
export async function buildMerchantSearchIndex(client:PoolClient){
 if(!(await client.query("SELECT pg_try_advisory_xact_lock(hashtext('merchant-search-build')) AS locked")).rows[0].locked)throw Error('Another merchant index build is running');
 const generation=randomUUID(),hash=createHash('sha256'),groups=new Map<string,string>();let last:string|null=null,count=0;
 await client.query('INSERT INTO bd_merchant_search_generation(id,strategy_version,source_relation) VALUES($1,$2,$3)',[generation,MERCHANT_SEARCH_VERSION,MERCHANT_SOURCE]);
 for(;;){
  const rows:{storeId:string;name:string;address:string;lat:number;lon:number}[]=(await client.query(`SELECT place_id AS "storeId",name,address,lat,lon FROM public.kanto_merchants_v20261029 WHERE ($1::text IS NULL OR place_id>$1) ORDER BY place_id LIMIT 1000`,[last])).rows;
  if(!rows.length)break;
  const documents=rows.map(row=>{
   if(typeof row.storeId!=='string'||!row.storeId)throw Error('Missing merchant source identity');
   const name=row.name??'',address=row.address??null,nameKey=storeNameKey(name),normalizedId=normalizeStoreQuery(row.storeId),normalizedName=normalizeStoreQuery(name),normalizedAddress=normalizeStoreQuery(address??'');
   if(nameKey){const previous=groups.get(nameKey);if(previous===undefined||name<previous)groups.set(nameKey,name);}
   const doc={store_id:row.storeId,name,address,lat:row.lat,lon:row.lon,name_key:nameKey,normalized_id:normalizedId,normalized_name:normalizedName,normalized_address:normalizedAddress};
   hash.update(JSON.stringify([doc.store_id,doc.name,doc.address,doc.lat,doc.lon])+'\n');return doc;
  });
  await client.query(`INSERT INTO bd_merchant_search_document SELECT $1::uuid,d.* FROM jsonb_to_recordset($2::jsonb) AS d(store_id text,name text,address text,lat double precision,lon double precision,name_key text,normalized_id text,normalized_name text,normalized_address text)`,[generation,JSON.stringify(documents)]);
  count+=documents.length;last=rows.at(-1)!.storeId;
 }
 const profiler=createStoreNameProfiler();let pending:any[]=[];
 const flush=async()=>{if(!pending.length)return;await client.query(`INSERT INTO bd_merchant_search_name(generation_id,name_key,name,profile,key_length,grams,core_key) SELECT $1::uuid,n.* FROM jsonb_to_recordset($2::jsonb) AS n(name_key text,name text,profile jsonb,key_length integer,grams text[],core_key text)`,[generation,JSON.stringify(pending)]);pending=[];};
 for(const [key,name] of groups){const profile=profiler(name);pending.push({name_key:key,name,profile,key_length:[...key].length,grams:storeNameBigrams(key),core_key:merchantCoreKey(profile)});if(pending.length===1000)await flush();}await flush();
 await client.query(`INSERT INTO bd_merchant_search_gram_weight SELECT $1::uuid,gram,1+ln(($2::double precision+1)/(count(*)+1)) FROM bd_merchant_search_name n CROSS JOIN LATERAL unnest(n.grams) AS gram WHERE n.generation_id=$1 GROUP BY gram`,[generation,groups.size]);
 await client.query(`WITH totals AS(SELECT n.name_key,sum(w.weight) AS weight FROM bd_merchant_search_name n CROSS JOIN LATERAL unnest(n.grams) AS x(gram) JOIN bd_merchant_search_gram_weight w ON w.generation_id=n.generation_id AND w.gram=x.gram WHERE n.generation_id=$1 GROUP BY n.name_key) UPDATE bd_merchant_search_name n SET gram_weight=t.weight FROM totals t WHERE n.generation_id=$1 AND n.name_key=t.name_key`,[generation]);
 const sourceSha256=hash.digest('hex');
 await client.query('UPDATE bd_merchant_search_generation SET source_sha256=$2,document_count=$3,group_count=$4,ready=true WHERE id=$1',[generation,sourceSha256,count,groups.size]);
 await client.query('INSERT INTO bd_merchant_search_active(singleton,generation_id) VALUES(true,$1) ON CONFLICT(singleton) DO UPDATE SET generation_id=excluded.generation_id',[generation]);
 return {generation,strategyVersion:MERCHANT_SEARCH_VERSION,sourceSha256,documentCount:count,groupCount:groups.size};
}
