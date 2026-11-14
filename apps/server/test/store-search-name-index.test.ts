import request from '../node_modules/supertest';
import {mkdir,writeFile} from 'node:fs/promises';
import 'reflect-metadata';import test from 'node:test';import assert from 'node:assert/strict';import {hash} from '@node-rs/argon2';import {performance} from 'node:perf_hooks';
import {createApp} from '../src/main';import {Database} from '../src/database';import {BatchWorkService} from '../src/batch-work';
import {seedBatchWork,clearBatchWork} from '../../../scripts/batch-work-fixture.mjs';
import {stopStoreNameRetrieval} from '../src/store-name-retrieval';
const url=process.env.NEXT_TEST_DATABASE_URL;
test('shared name index runs off the API thread, preserves literal priority, scope, filters, live snapshot updates and score-based pagination',{skip:!url},async()=>{
 assert.equal(process.env.NEXT_TEST_ISOLATED,'1');const app=await createApp({databaseUrl:url!,sessionSecret:'name-index-tests-secret-at-least-forty-characters',cookieSecure:false,port:0,host:'127.0.0.1'}),db=app.get(Database),service=app.get(BatchWorkService);let f:any;
 try{
  f=await seedBatchWork(db,'SNAME-'+Date.now(),await hash('fixture-pass'));const grid=f.grids[0][1],actor=f.users[2];
  const add=async(name:string,address='',target=grid)=>{const row=(await db.query(`INSERT INTO bd_store_task(grid_task_id,store_id,snapshot,status,result,revision,updated_at) VALUES($1,$2,$3,'pending','{}',0,now()) RETURNING id`,[target,crypto.randomUUID(),JSON.stringify({name,address})])).rows[0];f.stores[0][f.grids[0].indexOf(target)].push(row.id);return row.id;};
  const folded=await add('カナ・薬局');
  assert.equal((await service.visits(actor,grid,{q:'かな薬局'})).total,0,'legacy clients remain literal');
  const hit=await service.visits(actor,grid,{q:'かな薬局',related:'1'});assert.equal(hit.nameMatchMethod,'folded_name');assert.equal(hit.searchMode,'literal');assert.equal(hit.searchVersion,'guarded-name-index-v1');assert.deepEqual(hit.items.map((s:any)=>s.id),[folded]);assert.deepEqual(hit.items[0].searchMatch.nameTerms,['カナ・薬局']);
  const fuzzy=await add('新宿中央薬店');await add('KIMONO RENTAL REN');const foreign=await add('青空薬局','',f.grids[0][0]);
  const alternative=await service.visits(actor,grid,{q:'新宿中央薬局',related:'1'});assert.equal(alternative.searchMode,'related');assert.ok(['weighted_name','exact_core'].includes(alternative.nameMatchMethod));assert.deepEqual(alternative.items.map((s:any)=>s.id),[fuzzy]);assert.ok(alternative.items[0].searchMatch.nameTerms.some((term:string)=>term.includes('新宿')));
  assert.equal((await service.visits(actor,grid,{q:'KIMONO RENTAL MOMO',related:'1'})).total,0,'common rental category does not establish same brand');
  assert.ok((await service.visits(actor,grid,{q:'青空・薬局',related:'1'})).items.every((row:any)=>row.id!==foreign),'other grids never enter corpus; related current-grid candidates are allowed');
  assert.equal((await service.visits(actor,grid,{q:'新宿中央薬局',related:'1',state:'completed'})).total,0);
  await db.query("UPDATE bd_store_task SET snapshot=jsonb_build_object('name','完全別名','address','') WHERE id=$1",[fuzzy]);assert.equal((await service.visits(actor,grid,{q:'新宿中央薬局',related:'1'})).nameMatchMethod,null,'source hash includes actual snapshots, not just revision');
  const core=await add('中央苑');const coreResult=await service.visits(actor,grid,{q:'焼肉 中央苑',related:'1'});assert.equal(coreResult.nameMatchMethod,'exact_core');assert.deepEqual(coreResult.items.map((s:any)=>s.id),[core]);
  const literal=await add('焼肉 中央苑');const exact=await service.visits(actor,grid,{q:'焼肉 中央苑',related:'1'});assert.equal(exact.searchVersion,'terms-nfkc-v2');assert.deepEqual(exact.items.map((s:any)=>s.id),[literal]);
  const bulk=(await db.query(`INSERT INTO bd_store_task(grid_task_id,store_id,snapshot,status,result,revision,updated_at) SELECT $1,$2||n,jsonb_build_object('name','無関係な店 '||n),'pending','{}',0,now() FROM generate_series(1,3050)n RETURNING id`,[grid,f.prefix+'-bulk-'])).rows;f.stores[0][1].push(...bulk.map((s:any)=>s.id));
  const query={q:'かな薬局',related:'1'},coldStart=performance.now();await service.visits(actor,grid,query);const coldMs=performance.now()-coldStart,timings=[];
  for(let i=0;i<15;i++){const start=performance.now();const page=await service.visits(actor,grid,query);timings.push(performance.now()-start);assert.equal(page.items[0].id,folded);}
  timings.sort((a,b)=>a-b);console.log(JSON.stringify({nameIndexTiming:{scopeSize:(await db.query('SELECT count(*)::int AS n FROM bd_store_task WHERE grid_task_id=$1',[grid])).rows[0].n,coldMs,warmP95Ms:timings[Math.floor((timings.length-1)*.95)],samples:timings.length,externalProviders:false}}));
  await add('新宿中央薬店');
  const client=request.agent(app.getHttpServer());assert.equal((await client.post('/api/auth/login').send({username:f.codes[2],password:'fixture-pass'})).status,201);
  const route='/api/batch-work/grids/'+grid+'/visits',durations:number[]=[],byMethod:Record<string,number[]>={folded_name:[],weighted_name:[]};let next=0;
  const benchmarkStart=performance.now();
  await Promise.all(Array.from({length:5},async()=>{for(;;){const index=next++;if(index>=200)return;const q=index%2?'新宿中央薬局':'かな薬局',start=performance.now(),response=await client.get(route).query({q,related:'1'}),ms=performance.now()-start;assert.equal(response.status,200,JSON.stringify(response.body));assert.equal(response.body.total,1);assert.equal(response.body.searchMode,index%2?'related':'literal');const method=response.body.nameMatchMethod;assert.ok(method in byMethod);byMethod[method].push(ms);durations.push(ms);}}));
  const p95=(values:number[])=>[...values].sort((a,b)=>a-b)[Math.floor((values.length-1)*.95)];
  const benchmark={createdAt:new Date().toISOString(),samples:durations.length,concurrency:5,source:'isolated synthetic grid; real authenticated Nest HTTP, SQL, telemetry and worker',totalMs:performance.now()-benchmarkStart,p95Ms:p95(durations),byMethod:Object.fromEntries(Object.entries(byMethod).map(([key,values])=>[key,{samples:values.length,p95Ms:p95(values)}])),productionAccessed:false,baselineWritten:false};await mkdir('data/refactor/search-evaluation',{recursive:true,mode:0o700});const evidence='data/refactor/search-evaluation/name-index-http-'+Date.now()+'.json';await writeFile(evidence,JSON.stringify(benchmark,null,2),{mode:0o600});console.log(JSON.stringify({evidence,...benchmark}));assert.ok(benchmark.p95Ms<=300,'Representative grid authenticated HTTP P95 must remain <=300ms');
  const log=(await db.query('SELECT strategy_version,result_task_ids FROM bd_store_search_log WHERE id=$1',[hit.searchLogId])).rows[0];assert.equal(log.strategy_version,'guarded-name-index-v1');assert.deepEqual(log.result_task_ids,[folded]);
 }finally{stopStoreNameRetrieval();if(f)await clearBatchWork(db,f);await app.close();}
});

test('batch-range HTTP replay measures real frozen name diversity separately from representative-grid latency',{skip:!url},async context=>{
 assert.equal(process.env.NEXT_TEST_ISOLATED,'1');
 const {existsSync}=await import('node:fs');const {readFile}=await import('node:fs/promises');
 const corpus='data/refactor/search-evaluation/2026-11-13T12-24-40-840Z-dd3974b9/task-search-documents.jsonl';if(!existsSync(corpus)){context.skip('Local frozen corpus is unavailable; this is not a performance pass');return;}
 const rows=(await readFile(corpus,'utf8')).trim().split('\n').slice(0,20000).map(line=>{const row=JSON.parse(line);return {name:row.original.name??'',address:row.original.address??''};});
 const app=await createApp({databaseUrl:url!,sessionSecret:'batch-scale-tests-secret-at-least-forty-characters',cookieSecure:false,port:0,host:'127.0.0.1'}),db=app.get(Database);let f:any;
 try{
  f=await seedBatchWork(db,'BSCALE-'+Date.now(),await hash('fixture-pass'));const grid=f.grids[0][1],other=f.grids[0][0];
  const inserted=(await db.query(`INSERT INTO bd_store_task(grid_task_id,store_id,snapshot,status,result,revision,updated_at) SELECT CASE WHEN n%2=0 THEN $1::int ELSE $2::int END,$3||n,jsonb_build_object('name',value->>'name','address',value->>'address'),'pending','{}',0,now() FROM jsonb_array_elements($4::jsonb) WITH ORDINALITY AS source(value,n) RETURNING id,grid_task_id`,[grid,other,f.prefix+'-name-',JSON.stringify(rows)])).rows;
  for(const item of inserted)f.stores[0][f.grids[0].indexOf(item.grid_task_id)].push(item.id);
  const target=(await db.query(`INSERT INTO bd_store_task(grid_task_id,store_id,snapshot,status,result,revision,updated_at) VALUES($1,$2,'{"name":"カナ・薬局"}','pending','{}',0,now()) RETURNING id`,[other,f.prefix+'-target'])).rows[0].id;f.stores[0][0].push(target);
  const statementTimings:Record<string,number[]>={resultSql:[],scopeDocuments:[]},transaction=db.transaction.bind(db);
  (db as any).transaction=(fn:any)=>transaction(async connection=>{const original=connection.query;(connection as any).query=(...args:any[])=>{const text=typeof args[0]==='string'?args[0]:args[0]?.text??'',kind=text.startsWith('WITH search_input')?'resultSql':text.startsWith('WITH scope_rows')?'scopeDocuments':null,start=performance.now();const result=(original as any).apply(connection,args);if(kind&&result?.then)return result.finally(()=>statementTimings[kind].push(performance.now()-start));return result;};try{return await fn(connection);}finally{connection.query=original;}});
  stopStoreNameRetrieval();const client=request.agent(app.getHttpServer());assert.equal((await client.post('/api/auth/login').send({username:f.codes[2],password:'fixture-pass'})).status,201);const route='/api/batch-work/grids/'+grid+'/visits';
  const query={q:'かな薬局',related:'1',scope:'batch'},coldStart=performance.now(),cold=await client.get(route).query(query),coldMs=performance.now()-coldStart;assert.equal(cold.status,200);assert.ok(cold.body.items.some((item:any)=>item.id===target));assert.equal(cold.body.searchScope,'batch');assert.equal(cold.body.nameMatchMethod,'folded_name');
  const samples:number[]=[];let cursor=0;await Promise.all(Array.from({length:5},async()=>{for(;;){if(cursor++>=200)return;const start=performance.now(),result=await client.get(route).query(query);samples.push(performance.now()-start);assert.equal(result.status,200);assert.equal(result.body.total,cold.body.total);assert.deepEqual(result.body.gridGroups,cold.body.gridGroups);}}));samples.sort((a,b)=>a-b);
  const statementSummary=Object.fromEntries(Object.entries(statementTimings).map(([kind,values])=>{const sorted=[...values].sort((a,b)=>a-b);return [kind,{samples:values.length,p50Ms:sorted[Math.floor((sorted.length-1)*.5)],p95Ms:sorted[Math.floor((sorted.length-1)*.95)]}];}));
  const report={statementSummary,createdAt:new Date().toISOString(),source:'isolated task fixtures using 20000 frozen task names/addresses; no source task IDs copied',documents:rows.length+5,concurrency:5,samples:samples.length,coldMs,warmP95Ms:samples[Math.floor((samples.length-1)*.95)],planned300msGatePassed:samples[Math.floor((samples.length-1)*.95)]<=300,includes:'authenticated HTTP, current permissions, authorized batch tree, SQL grouping, telemetry and worker',excludes:'public network and browser',productionAccessed:false,baselineWritten:false};const file='data/refactor/search-evaluation/name-index-batch-http-'+Date.now()+'.json';await mkdir('data/refactor/search-evaluation',{recursive:true,mode:0o700});await writeFile(file,JSON.stringify(report,null,2),{mode:0o600});console.log(JSON.stringify({file,...report}));assert.ok(report.planned300msGatePassed,'20000-task authenticated batch HTTP P95 must be <=300ms at 5 concurrency');
 }finally{stopStoreNameRetrieval();if(f)await clearBatchWork(db,f);await app.close();}
});
