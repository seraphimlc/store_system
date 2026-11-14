import type {PoolClient} from 'pg';
import type {StoreSuggestionPage} from '../../../packages/contracts/src/store-search';
import {canSuggestStoreName,groupStoreNameSuggestions,STORE_SUGGESTION_SCOPE_LIMIT,STORE_SUGGESTION_VERSION} from '../../../packages/domain/src/store-similarity';
import {storeSearchSql,storeSearchTermsSql} from '../../../packages/domain/src/store-search';
import {storeSearchColumns,storeSearchMatch} from './store-search-sql';
export interface StoreSuggestionInput {q:string;state:string;businessStatus:string;page:number}
/** Caller must establish current grid permissions in the same read-only transaction. No task writes/logged hits. */
export async function storeNameSuggestions(client:PoolClient,gridId:number,input:StoreSuggestionInput):Promise<StoreSuggestionPage>{
 const base:Omit<StoreSuggestionPage,'status'>={query:input.q,strategyVersion:STORE_SUGGESTION_VERSION,page:input.page,pageSize:5,total:null,candidateTotal:null,hasMore:false,items:[]};
 if(!canSuggestStoreName(input.q))return {...base,status:'unsupported_query'};
 const values=[gridId,input.q,input.state,input.businessStatus];
 const literal=(await client.query(`WITH search_input AS(SELECT ${storeSearchSql('$2::text')} AS q,${storeSearchTermsSql('$2::text')} AS terms),scoped AS(SELECT ${storeSearchColumns('store_id',"snapshot->>'name'","snapshot->>'address'",'$2::text')} FROM bd_store_task WHERE grid_task_id=$1 AND ($3='' OR status=$3) AND ($4='' OR coalesce(result->>'business_status','normal')=$4)) SELECT EXISTS(SELECT 1 FROM scoped WHERE ${storeSearchMatch('(SELECT q FROM search_input)')}) AS found`,values)).rows[0];
 if(literal.found)return {...base,status:'literal_results'};
 const rows=(await client.query<{id:number;name:string|null}>(`SELECT id,snapshot->>'name' AS name FROM bd_store_task WHERE grid_task_id=$1 AND ($2='' OR status=$2) AND ($3='' OR coalesce(result->>'business_status','normal')=$3) ORDER BY id LIMIT $4`,[gridId,input.state,input.businessStatus,STORE_SUGGESTION_SCOPE_LIMIT+1])).rows;
 if(rows.length>STORE_SUGGESTION_SCOPE_LIMIT)return {...base,status:'scope_too_large'};
 const groups=groupStoreNameSuggestions(input.q,rows),offset=(input.page-1)*base.pageSize;
 return {...base,status:'ready',total:groups.length,candidateTotal:groups.reduce((n,row)=>n+row.candidateCount,0),hasMore:offset+base.pageSize<groups.length,items:groups.slice(offset,offset+base.pageSize).map(({name,candidateCount})=>({name,candidateCount}))};
}
