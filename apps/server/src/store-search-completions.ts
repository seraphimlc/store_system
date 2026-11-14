import type {PoolClient} from 'pg';
import type {StoreCompletionPage} from '../../../packages/contracts/src/store-search';
import {normalizeStoreQuery,storeSearchSql} from '../../../packages/domain/src/store-search';
/** Current authorized scope only; prefix names are not identities, typo corrections or search hits. */
export async function storeNameCompletions(client:PoolClient,gridId:number,input:{q:string;state:string;businessStatus:string}):Promise<StoreCompletionPage>{
 const q=normalizeStoreQuery(input.q),length=[...q].length;
 if(length<2||length>80||/^\p{N}+$/u.test(q))return {status:'unsupported_query',query:input.q,total:null,candidateTotal:null,items:[]};
 const result=(await client.query(`WITH scope_rows AS(SELECT id,snapshot->>'name' AS name FROM bd_store_task WHERE grid_task_id=$1 AND ($3='' OR status=$3) AND ($4='' OR coalesce(result->>'business_status','normal')=$4) ORDER BY id LIMIT 5001),scoped AS(SELECT *,${storeSearchSql('name')} AS key FROM scope_rows),grouped AS(SELECT key,min(id) AS first_id,count(*)::int AS candidate_count FROM scoped WHERE (SELECT count(*) FROM scope_rows)<=5000 AND starts_with(key,${storeSearchSql('$2::text')}) AND key<>'' GROUP BY key),limited AS(SELECT g.first_id,s.name,g.candidate_count FROM grouped g JOIN scoped s ON s.id=g.first_id ORDER BY g.first_id LIMIT 8) SELECT (SELECT count(*)::int FROM scoped) AS scope_count,CASE WHEN (SELECT count(*) FROM scoped)<=5000 THEN (SELECT count(*)::int FROM grouped) END AS total,CASE WHEN (SELECT count(*) FROM scoped)<=5000 THEN (SELECT coalesce(sum(candidate_count),0)::int FROM grouped) END AS candidate_total,coalesce((SELECT json_agg(json_build_object('name',name,'candidateCount',candidate_count)) FROM limited),'[]') AS items`,[gridId,input.q,input.state,input.businessStatus])).rows[0];
 if(result.scope_count>5000)return {status:'scope_too_large',query:input.q,total:null,candidateTotal:null,items:[]};
 return {status:'ready',query:input.q,total:result.total,candidateTotal:result.candidate_total,items:result.items};
}
