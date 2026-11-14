import {parentPort,workerData} from 'node:worker_threads';
import {Client} from 'pg';
import {MerchantLiteralIndex,type MerchantLiteralDocument} from '../../../packages/domain/src/merchant-literal-index';
import {MerchantFacetIndex,type MerchantGeographicGroup} from '../../../packages/domain/src/merchant-facet-index';

interface State {key:string;literal:MerchantLiteralIndex;facets:MerchantFacetIndex;storeIds:string[];lookup:Map<string,number>}
let state:State|undefined;
function csr(merchantCount:number,rows:number[],gridIds:number[],regionIds:number[]){
 const offsets=new Uint32Array(merchantCount+1);for(const row of rows)offsets[row+1]++;for(let i=1;i<offsets.length;i++)offsets[i]+=offsets[i-1];const next=offsets.slice(),result={rows:new Uint32Array(rows.length),gridIds:new Uint32Array(rows.length),regionIds:new Uint32Array(rows.length)};
 for(let i=0;i<rows.length;i++){const p=next[rows[i]]++;result.rows[p]=rows[i];result.gridIds[p]=gridIds[i];result.regionIds[p]=regionIds[i];}return result;
}
async function load(){
 const client=new Client({connectionString:workerData.connectionString,application_name:'merchant-search-runtime-readonly',statement_timeout:15000});await client.connect();
 try{
  await client.query('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY');
  const current=(await client.query('SELECT a.generation_id::text AS generation,v.revision::text AS revision FROM bd_merchant_search_active a CROSS JOIN bd_merchant_search_geography_version v WHERE v.singleton')).rows[0];
  if(!current||current.generation+':'+current.revision!==workerData.key)throw Error('SEARCH_VERSION_CHANGED');
  let documents:MerchantLiteralDocument[]|null=[],last:string|null=null;const storeIds:string[]=[],lookup=new Map<string,number>();
  for(;;){const rows:{store_id:string;name:string;address:string|null}[]=(await client.query('SELECT store_id,name,address FROM bd_merchant_search_document WHERE generation_id=$1 AND ($2::text IS NULL OR store_id>$2) ORDER BY store_id LIMIT 2000',[current.generation,last])).rows;if(!rows.length)break;
   for(const row of rows){lookup.set(row.store_id,documents.length);storeIds.push(row.store_id);documents.push({id:documents.length+1,storeId:row.store_id,name:row.name,address:row.address});}last=rows.at(-1)!.store_id;
  }
  const gridLabels=(await client.query(`SELECT g.id,g.name,r.id AS "regionId",r.name AS "regionName" FROM bd_work_grid g JOIN bd_region r ON r.id=g.zone_id AND r.release_id=g.release_id WHERE g.state='ready' AND r.state='ready' ORDER BY g.id`)).rows as MerchantGeographicGroup[];
  const regionLabels=[...new Map(gridLabels.map(g=>[g.regionId!,{id:g.regionId!,name:g.regionName!}])).values()].sort((a,b)=>a.id-b.id);
  const linkRows:number[]=[],grids:number[]=[],regions:number[]=[];let after=0;
  for(;;){const links=(await client.query(`SELECT b.id,b.source_id,b.grid_id,r.id AS region_id FROM bd_grid_store b JOIN bd_work_grid g ON g.id=b.grid_id AND g.release_id=b.release_id AND g.state='ready' JOIN bd_region r ON r.id=g.zone_id AND r.release_id=g.release_id AND r.state='ready' WHERE b.id>$1 ORDER BY b.id LIMIT 5000`,[after])).rows;if(!links.length)break;
   for(const link of links){const row=lookup.get(link.source_id);if(row!==undefined){linkRows.push(row);grids.push(link.grid_id);regions.push(link.region_id);}}after=links.at(-1)!.id;
  }
  await client.query('ROLLBACK');const facets=new MerchantFacetIndex(documents.length,csr(documents.length,linkRows,grids,regions),gridLabels,regionLabels),literal=new MerchantLiteralIndex(documents);documents=null;
  state={key:workerData.key,literal,facets,storeIds,lookup};parentPort!.postMessage({ready:true,key:state.key,documents:storeIds.length,rss:process.memoryUsage().rss});
 }finally{await client.end();}
}
parentPort!.on('message',message=>{
 if(!state)return;
 try{
  let matches;
  if(message.candidates){const candidates=(message.candidates as {storeId:string;score:number}[]).map(c=>({...c,row:state!.lookup.get(c.storeId)})).filter(c=>c.row!==undefined).sort((a,b)=>a.row!-b.row!);matches={rows:Uint32Array.from(candidates.map(c=>c.row!)),tiers:new Uint8Array(candidates.length).fill(8)};}
  else matches=state.literal.searchPacked(message.query);
  const result=state.facets.select(matches,message.filter),scores=message.candidates?new Map<string,number>(message.candidates.map((c:any)=>[c.storeId,c.score])):null;
  const ids=Array.from(result.rows,row=>state!.storeIds[row]);if(scores)ids.sort((a,b)=>(scores.get(b)??0)-(scores.get(a)??0)||state!.lookup.get(a)!-state!.lookup.get(b)!);
  const {rows,tiers,...metadata}=result;parentPort!.postMessage({id:message.id,result:{...metadata,storeIds:ids,key:state.key}});
 }catch{parentPort!.postMessage({id:message.id,error:'SEARCH_RUNTIME_QUERY_FAILED'});}
});
void load().catch(()=>{parentPort!.postMessage({error:'SEARCH_RUNTIME_LOAD_FAILED'});process.exitCode=1;parentPort!.close();});
