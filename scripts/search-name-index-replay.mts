import {storeMatchTier} from '../packages/domain/src/store-search';
import {createReadStream} from 'node:fs';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {createInterface} from 'node:readline';
import {createHash} from 'node:crypto';
import {resolve,join} from 'node:path';
import {performance} from 'node:perf_hooks';
import {StoreNameIndex,STORE_NAME_INDEX_VERSION} from '../packages/domain/src/store-name-index';
const indexSha256=createHash('sha256').update(await readFile('packages/domain/src/store-name-index.ts')).digest('hex');
const root=resolve(process.argv[2]??'data/refactor/search-evaluation/2026-11-13T12-24-40-840Z-dd3974b9');
const tierOne=process.argv[3]==='--tier-one';
if(!process.argv[3])throw Error('Explicit frozen address context or --tier-one is required');
const manifest=JSON.parse(await readFile(join(root,'manifest.json'),'utf8'));
async function rows(file:string){const expected=manifest.files.find((x:any)=>x.file===file),hash=createHash('sha256'),stream=createReadStream(join(root,file)),result:any[]=[];if(!expected)throw Error('Missing frozen file');stream.on('data',bytes=>hash.update(bytes));for await(const line of createInterface({input:stream,crlfDelay:Infinity}))if(line)result.push(JSON.parse(line));if(hash.digest('hex')!==expected.sha256||result.length!==expected.rows)throw Error('Snapshot mismatch '+file);return result;}
if(tierOne){
 const logs=await rows('local-search-requests.jsonl'),tasks=await rows('task-snapshots.jsonl'),results=[];
 const indexes=new Map<number,StoreNameIndex>();
 for(const log of logs){const scope=tasks.filter(task=>task.grid_task_id===log.grid_task_id);let index=indexes.get(log.grid_task_id);if(!index){index=new StoreNameIndex(scope.map(task=>({id:task.id,name:task.snapshot?.name??'',address:task.snapshot?.address})));indexes.set(log.grid_task_id,index);}
  const allowed=(task:any)=>(!log.task_state||task.status===log.task_state)&&(!log.business_status||(task.business_status??'normal')===log.business_status);
  const literal=scope.filter(task=>storeMatchTier(log.query_text,{storeId:task.store_id,name:task.snapshot?.name??'',address:task.snapshot?.address??''})!==null),before=literal.filter(allowed);
  const retrieved=literal.length?null:index.search(log.query_text),fallback=literal.length?[]:retrieved!.candidates.length?retrieved!.candidates:index.relatedWords(log.query_text),ids=new Set(fallback.map(candidate=>candidate.id));
  const after=literal.length?before:scope.filter(task=>ids.has(task.id)&&allowed(task));
  results.push({requestId:log.id,query:log.query_text,historicalResultCount:log.matched_count,captureLiteralCount:before.length,captureCurrentCount:after.length,method:literal.length?'literal':retrieved?.method??'word_related',candidateBeforeFilters:fallback.length,returnedTaskIds:after.map(task=>task.id),correctTarget:'unknown'});
 }
 const report={createdAt:new Date().toISOString(),snapshot:manifest.version,strategy:STORE_NAME_INDEX_VERSION,indexSha256,requests:logs.length,historicalResultSearches:logs.filter(log=>log.matched_count>0).length,captureLiteralResultSearches:results.filter(row=>row.captureLiteralCount>0).length,captureCurrentResultSearches:results.filter(row=>row.captureCurrentCount>0).length,confirmedSuccessRate:null,databaseAccessed:false,productionAccessed:false,warning:'Offline replay on capture-time task state, not historical state/location reconstruction. No new expected-target labels or conversion claims.',results};
 const output=join(root,'runtime-index-replays',new Date().toISOString().replace(/[:.]/g,'-'));await mkdir(output,{recursive:true,mode:0o700});await writeFile(join(output,'report.json'),JSON.stringify(report,null,2),{mode:0o600});console.log(JSON.stringify({output,...report},null,2));
}else{
const entities=await rows('entities.jsonl'),merchants=await rows('merchants.jsonl'),folder=resolve(process.argv[3]),context=JSON.parse(await readFile(join(folder,'manifest.json'),'utf8')),text=await readFile(join(folder,context.file),'utf8');
if(context.baseSnapshot!==manifest.version||context.baseEntitiesSha256!==manifest.files.find((f:any)=>f.file==='entities.jsonl').sha256||createHash('sha256').update(text).digest('hex')!==context.sha256)throw Error('Address snapshot mismatch');
const addresses=new Map<number,string|null>(text.trim().split('\n').map(line=>{const row=JSON.parse(line);return [row.id,row.address];}));if(addresses.size!==entities.length||entities.some(entity=>!addresses.has(entity.id)))throw Error('Address IDs mismatch');
const start=performance.now(),index=new StoreNameIndex(merchants.map((row,id)=>({id:id+1,name:row.name??'',address:row.address}))),built=performance.now();
const stats={covered:0,unique:0,ambiguous:0,missing:0},methods:Record<string,number>={},results=[];const timings:number[]=[];
for(const [position,entity] of entities.entries()){if(position%10000===0)console.error(JSON.stringify({replayProgress:position,total:entities.length,elapsedMs:Math.round(performance.now()-built)}));const begin=performance.now(),result=index.search(entity.name_local??'',addresses.get(entity.id));timings.push(performance.now()-begin);const count=result.candidates.length;stats.covered+=Number(count>0);stats.unique+=Number(count===1);stats.ambiguous+=Number(count>1);stats.missing+=Number(count===0);methods[result.method??result.status]=(methods[result.method??result.status]??0)+1;
 results.push({id:entity.id,method:result.method,status:result.status,candidateCount:count,candidates:result.candidates.map(candidate=>({candidateKey:merchants[candidate.id-1].candidateKey,score:candidate.score}))});
}
timings.sort((a,b)=>a-b);const output=join(root,'runtime-index-replays',new Date().toISOString().replace(/[:.]/g,'-'));await mkdir(output,{recursive:true,mode:0o700});
if(createHash('sha256').update(await readFile('packages/domain/src/store-name-index.ts')).digest('hex')!==indexSha256)throw Error('Index source changed during replay; reject mixed evidence');
const report={createdAt:new Date().toISOString(),snapshot:manifest.version,strategy:STORE_NAME_INDEX_VERSION,indexSha256,denominator:entities.length,...stats,coveragePct:100*stats.covered/entities.length,methods,buildMs:built-start,replayMs:performance.now()-built,p95QueryMs:timings[Math.floor((timings.length-1)*.95)],addressContextSha256:context.sha256,databaseAccessed:false,runtimeLibrary:true,productionAccessed:false,warning:'Same shared retrieval code as API. Offline query address is known for some entities; user query generally has no target address and authorized task corpus differs. This is not correct-entity success rate.'};
await writeFile(join(output,'report.json'),JSON.stringify(report,null,2),{mode:0o600});await writeFile(join(output,'results.jsonl'),results.map(r=>JSON.stringify(r)).join('\n')+'\n',{mode:0o600});console.log(JSON.stringify({output,...report},null,2));

}
