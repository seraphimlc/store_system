import {retrieveStoreNames} from './store-name-retrieval';
import {STORE_NAME_INDEX_VERSION,type StoreNameCandidate,type StoreNameMethod} from '../../../packages/domain/src/store-name-index';
import {storeNameCandidateEvidence} from '../../../packages/domain/src/store-search-evidence';
import {storeRelatedNameTerms,storeSearchMatchEvidence,STORE_RELATED_VERSION} from '../../../packages/domain/src/store-search-evidence';
import {mobileVisitList} from './mobile-visit-list';
import {STORE_SEARCH_VERSION,storeSearchSql,storeSearchTermsSql,normalizeStoreQuery} from '../../../packages/domain/src/store-search';
import {storeSearchColumns,storeSearchMatch,storeSearchTier} from './store-search-sql';
import type {PoolClient} from 'pg';
export interface VisitQuery {q:string;state:string;businessStatus:string;page:number;lat?:number;lon?:number;focus?:number;related?:'1';scope?:'grid'|'batch';groupGrid?:number;groupRegion?:number;scopeGridIds?:number[]}
/** The same authorized grid snapshot is used for both the map and the drawer. */
export async function mobileVisits(client:PoolClient,gridId:number,input:VisitQuery,closed:boolean){
 if(!normalizeStoreQuery(input.q))return {...await mobileVisitList(client,gridId,{...input,q:''},closed),searchVersion:STORE_SEARCH_VERSION};
 const match=storeSearchMatch('(SELECT q FROM search_input)');
 const scopeIds=input.scopeGridIds??[gridId];
 const relatedTerms=input.related?storeRelatedNameTerms(input.q):[];
 const number=(value:string)=>`CASE WHEN ${value} ~ '^-?[0-9]+([.][0-9]+)?$' THEN (${value})::double precision END`;
 const lat=`coalesce(d.lat,m.lat,${number("s.snapshot->>'lat'")})`,lon=`coalesce(d.lon,m.lon,${number("s.snapshot->>'lon'")})`;
 const distance=`6371000*2*asin(sqrt(least(1,power(sin(radians(lat-$5::double precision)/2),2)+cos(radians(lat))*cos(radians($5::double precision))*power(sin(radians(lon-$6::double precision)/2),2))))`;
 const execute=async(nameCandidates:StoreNameCandidate[]=[],summary?:any,knownLiteral=false)=> {const result=(await client.query(`WITH search_input AS(SELECT ${storeSearchSql('$10::text')} AS q,${storeSearchTermsSql('$10::text')} AS terms,$2::text AS original_query),all_rows AS(SELECT s.id,s.grid_task_id AS "gridTaskId",g.region_task_id AS "regionTaskId",g.snapshot->>'name' AS "gridName",r.snapshot->>'name' AS "regionName",s.store_id AS "storeId",s.snapshot,s.result,s.status,s.revision,
  ${storeSearchColumns('s.store_id',"s.snapshot->>'name'","s.snapshot->>'address'","CASE WHEN $17::boolean THEN '' ELSE $10::text END")},
  ${lat} AS lat,${lon} AS lon,(m.place_id IS NULL) AS "sourceMissing"
  FROM bd_store_task s JOIN bd_grid_task g ON g.id=s.grid_task_id JOIN bd_region_task r ON r.id=g.region_task_id LEFT JOIN bd_merchant_detail d ON d.store_id=s.store_id AND NOT $8::boolean
  LEFT JOIN public.kanto_merchants_v20261029 m ON m.place_id=s.store_id WHERE s.grid_task_id=ANY($1::int[]) AND (NOT $15::boolean OR s.id IN(SELECT (value->>'id')::int FROM jsonb_array_elements($14::jsonb)))),
 name_candidates AS(SELECT (value->>'id')::int AS id,(value->>'score')::double precision AS score,(value->>'tier')::int AS tier FROM jsonb_array_elements($14::jsonb)),
 literal_matching AS(SELECT * FROM all_rows WHERE ($17::boolean OR (NOT $15::boolean AND ${match}))
 AND ($3='' OR status=$3) AND ($4='' OR coalesce(result->>'business_status','normal')=$4)),
 matching AS(SELECT * FROM literal_matching UNION ALL SELECT * FROM all_rows WHERE NOT EXISTS(SELECT 1 FROM literal_matching) AND ($15::boolean OR NOT EXISTS(SELECT 1 FROM all_rows WHERE ${match})) AND id IN(SELECT id FROM name_candidates) AND ($3='' OR status=$3) AND ($4='' OR coalesce(result->>'business_status','normal')=$4)),
 selected_matching AS(SELECT * FROM matching WHERE ($12::int IS NULL OR "gridTaskId"=$12) AND ($13::int IS NULL OR "regionTaskId"=$13)),
 ranked AS(SELECT *,coalesce((SELECT score FROM name_candidates WHERE name_candidates.id=selected_matching.id),0) AS "nameScore",CASE WHEN $17::boolean THEN (SELECT tier FROM name_candidates WHERE name_candidates.id=selected_matching.id) WHEN EXISTS(SELECT 1 FROM literal_matching) THEN ${storeSearchTier('(SELECT q FROM search_input)')} ELSE 8 END AS "matchTier",CASE WHEN EXISTS(SELECT 1 FROM literal_matching) THEN 0 ELSE (SELECT count(*) FROM unnest($11::text[]) AS term WHERE strpos(search_name,term)>0) END AS "matchedTermCount",CASE WHEN $5::double precision IS NOT NULL AND lat BETWEEN -90 AND 90 AND lon BETWEEN -180 AND 180 THEN ${distance} END AS "distanceMeters" FROM selected_matching),
 page_rows AS(SELECT * FROM ranked ORDER BY CASE WHEN id=$9 THEN 0 ELSE 1 END,"matchTier","nameScore" DESC,"matchedTermCount" DESC,"distanceMeters" ASC NULLS LAST,id LIMIT 20 OFFSET $7),
 paged AS(SELECT p.*,coalesce((d.photo_urls)::jsonb,(p.result->'photo_urls')::jsonb,'[]'::jsonb) AS photos,coalesce(d.revision,0) AS "photoRevision",coalesce(d.revision,0) AS "profileRevision",
 d.google_place_id AS "googlePlaceId",d.place_lookup_state AS "placeLookupState",
 coalesce((SELECT e.new_value->>'location_accuracy' FROM bd_task_event e JOIN bd_store_task x ON x.id=e.store_task_id WHERE x.store_id=p."storeId" AND e.event_type='verify_store_location' ORDER BY e.created_at DESC,e.id DESC LIMIT 1),nullif(m.location_accuracy,''),p.snapshot->>'location_accuracy','pending') AS accuracy
 FROM page_rows p LEFT JOIN bd_merchant_detail d ON d.store_id=p."storeId" AND NOT $8::boolean LEFT JOIN public.kanto_merchants_v20261029 m ON m.place_id=p."storeId"
 ORDER BY CASE WHEN p.id=$9 THEN 0 ELSE 1 END,p."matchTier",p."nameScore" DESC,p."matchedTermCount" DESC,p."distanceMeters" ASC NULLS LAST,p.id)
 SELECT CASE WHEN $16::boolean AND NOT $15::boolean AND NOT EXISTS(SELECT 1 FROM all_rows WHERE ${match}) THEN
 (SELECT coalesce(json_agg(doc ORDER BY id),'[]') FROM (SELECT id,coalesce(snapshot->>'name','') AS name,snapshot->>'address' AS address FROM all_rows) doc) ELSE NULL END AS "nameDocuments",
 (SELECT count(*)::int FROM selected_matching) AS total,(SELECT count(*)::int FROM matching) AS "scopeMatchedTotal",
 (SELECT coalesce(json_agg(x),'[]') FROM (SELECT "gridTaskId" AS id,max("gridName") AS name,"regionTaskId",max("regionName") AS "regionName",count(*)::int AS count FROM matching GROUP BY "gridTaskId","regionTaskId" ORDER BY "gridTaskId") x) AS "gridGroups",
 (SELECT coalesce(json_agg(x),'[]') FROM (SELECT "regionTaskId" AS id,max("regionName") AS name,count(*)::int AS count FROM matching GROUP BY "regionTaskId" ORDER BY "regionTaskId") x) AS "regionGroups",(SELECT count(*)::int FROM literal_matching) AS "literalTotal",coalesce((SELECT json_agg(to_jsonb(p)-'search_id'-'search_name'-'search_address'-'matchedTermCount'-'nameScore') FROM paged p),'[]') AS items,
  (SELECT count(*)::int FROM all_rows) AS "gridTotal",
  (SELECT count(*)::int FROM all_rows WHERE ${match}) AS "keywordMatches",
  (SELECT count(*)::int FROM all_rows WHERE ${match}) AS "normalizedMatches",
  (SELECT count(*) FILTER(WHERE coalesce(snapshot->>'name','')='')::int FROM all_rows) AS "missingName",
  (SELECT count(*) FILTER(WHERE coalesce(snapshot->>'address','')='')::int FROM all_rows) AS "missingAddress",
  (SELECT count(*) FILTER(WHERE "sourceMissing")::int FROM all_rows) AS "missingSource",
  (SELECT count(*) FILTER(WHERE lat IS NULL OR lon IS NULL)::int FROM all_rows) AS "missingLocation"`,[scopeIds,input.q,input.state,input.businessStatus,input.lat??null,input.lon??null,(input.page-1)*20,closed,input.focus??null,input.q,relatedTerms,input.groupGrid??null,input.groupRegion??null,JSON.stringify(nameCandidates),summary!==undefined,!!input.related,knownLiteral])).rows[0];
 if(summary){for(const key of ['gridTotal','keywordMatches','normalizedMatches','missingName','missingAddress','missingSource','missingLocation'])result[key]=summary[key];}
 return result;};
 let result:any,nameMethod:StoreNameMethod|null=null,nameStatus:string|undefined,nameCandidateCount=0;
 if(input.related){
  const summary=(await client.query(`WITH scope_rows AS(SELECT s.id,s.store_id,s.snapshot,${lat} AS lat,${lon} AS lon,(m.place_id IS NULL) AS source_missing
   FROM bd_store_task s LEFT JOIN bd_merchant_detail d ON d.store_id=s.store_id AND NOT $2::boolean LEFT JOIN public.kanto_merchants_v20261029 m ON m.place_id=s.store_id WHERE s.grid_task_id=ANY($1::int[]))
   SELECT count(*)::int AS "gridTotal",count(*) FILTER(WHERE coalesce(snapshot->>'name','')='')::int AS "missingName",count(*) FILTER(WHERE coalesce(snapshot->>'address','')='')::int AS "missingAddress",count(*) FILTER(WHERE source_missing)::int AS "missingSource",count(*) FILTER(WHERE lat IS NULL OR lon IS NULL)::int AS "missingLocation",
   coalesce(json_agg(json_build_object('id',id,'storeId',store_id,'name',coalesce(snapshot->>'name',''),'address',snapshot->>'address') ORDER BY id),'[]') AS documents FROM scope_rows`,[scopeIds,closed])).rows[0];
  const documents=summary.documents;delete summary.documents;
  result={...summary,items:[],total:0,scopeMatchedTotal:0,literalTotal:0,gridGroups:[],regionGroups:[],keywordMatches:0,normalizedMatches:0};
  const names=await retrieveStoreNames(documents,input.q);nameStatus=names.status;
  if(names.literalCandidates.length){result.keywordMatches=names.literalCandidates.length;result.normalizedMatches=names.literalCandidates.length;result=await execute(names.literalCandidates,result,true);}
  else if(names.candidates.length){nameMethod=names.method;nameCandidateCount=names.candidates.length;result=await execute(names.candidates,result);}
  else if(names.wordCandidates.length){nameCandidateCount=names.wordCandidates.length;result=await execute(names.wordCandidates,result);}
 }else result=await execute();
 delete result.nameDocuments;
 const relatedStatus=!input.related?undefined:nameMethod?'ready':result.literalTotal>0?'literal_results':result.keywordMatches>0?'filters_excluded':!relatedTerms.length?'unsupported_query':result.gridTotal>5000?'scope_too_large':'ready';
 const mode=nameMethod==='folded_name'?'literal':relatedStatus==='ready'?'related':'literal';
 return {...result,items:result.items.map((row:any)=>({...row,searchMatch:nameMethod?storeNameCandidateEvidence(input.q,row,nameMethod):storeSearchMatchEvidence(input.q,row,mode)})),nameCandidateCount,nameSearchStatus:nameStatus,nameMatchMethod:nameMethod,searchScope:input.scope??'grid',searchMode:mode,relatedStatus,page:input.page,pageSize:20,order:'relevance',searchVersion:(input.scope==='batch'?'batch-':'')+(nameMethod?STORE_NAME_INDEX_VERSION:mode==='related'?STORE_RELATED_VERSION:STORE_SEARCH_VERSION)};
}
