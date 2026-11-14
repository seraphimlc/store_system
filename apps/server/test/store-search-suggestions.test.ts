import 'reflect-metadata';import test from 'node:test';import assert from 'node:assert/strict';
import {hash} from '@node-rs/argon2';import {performance} from 'node:perf_hooks';import {mkdir,writeFile} from 'node:fs/promises';import request from '../node_modules/supertest';
import {createApp} from '../src/main';import {Database} from '../src/database';import {BatchWorkService} from '../src/batch-work';
import {storeNameSuggestions} from '../src/store-search-suggestions';
import {seedBatchWork,clearBatchWork} from '../../../scripts/batch-work-fixture.mjs';
const url=process.env.NEXT_TEST_DATABASE_URL;
test('overlarge suggestion scopes fail explicitly instead of reporting truncated candidate counts',async()=>{
 let n=0;const client={query:async()=>++n===1?{rows:[{found:false}]}:{rows:Array.from({length:5001},(_,i)=>({id:i+1,name:'ABCDEF'}))}};
 const result=await storeNameSuggestions(client as any,1,{q:'ABCDEH',state:'',businessStatus:'',page:1});assert.equal(result.status,'scope_too_large');assert.equal(result.total,null);assert.equal(result.candidateTotal,null);assert.deepEqual(result.items,[]);assert.equal(n,2);
});
test('explicit same-grid suggestions preserve live permissions, filters, grouping, pagination and no-hit semantics',{skip:!url},async()=>{
 const app=await createApp({databaseUrl:url!,sessionSecret:'suggestion-tests-secret-with-more-than-forty-characters',cookieSecure:false,port:0,host:'127.0.0.1'}),db=app.get(Database),service=app.get(BatchWorkService);let f:any;
 try{f=await seedBatchWork(db,'SUGGEST-'+Date.now(),await hash('fixture-suggest-pass'));const grid=f.grids[0][1],actor=f.users[2];
 const beforeEvents=(await db.query('SELECT count(*)::int AS n FROM bd_task_event')).rows[0].n;
 const add=async(name:string,status='pending',businessStatus='normal')=>{const id=(await db.query("INSERT INTO bd_store_task(grid_task_id,store_id,snapshot,status,result,revision,updated_at) VALUES($1,$2,$3,$4,$5,0,now()) RETURNING id",[grid,'suggest-'+name,JSON.stringify({name,address:''}),status,JSON.stringify({business_status:businessStatus})])).rows[0].id;f.stores[0][1].push(id);return id;};
 for(const c of 'ABCDEFG')await add('ABCDE'+c,c==='B'?'completed':'pending',c==='C'?'closed':'normal');await add('ＡＢＣＤＥＦ');
 const first=await service.suggestions(actor,grid,{q:'ABCDEX'}),second=await service.suggestions(actor,grid,{q:'ABCDEX',page:2});
 assert.equal(first.status,'ready');assert.equal(first.total,7);assert.equal(first.candidateTotal,8);assert.equal(first.items.length,5);assert.equal(first.hasMore,true);assert.equal(second.items.length,2);assert.equal(second.hasMore,false);
 assert.deepEqual([...first.items,...second.items].map(x=>x.name),[...'ABCDEFG'].map(c=>'ABCDE'+c));assert.equal(second.items[0].candidateCount,2);
 assert.deepEqual((await service.suggestions(actor,grid,{q:'ABCDEX'})).items,first.items);
 assert.equal((await service.suggestions(actor,grid,{q:'ABCDEX',state:'pending'})).total,6);
 assert.deepEqual((await service.suggestions(actor,grid,{q:'ABCDEX',businessStatus:'closed'})).items,[{name:'ABCDEC',candidateCount:1}]);
 assert.equal((await service.suggestions(actor,grid,{q:'ABCDEF'})).status,'literal_results');assert.equal((await service.suggestions(actor,grid,{q:'123456'})).status,'unsupported_query');
 assert.equal((await service.suggestions(actor,grid,{q:'notanearbyname'})).total,0);
 await db.query("UPDATE bd_store_task SET snapshot=jsonb_build_object('name','OUTSIDE','address','') WHERE id=$1",[f.stores[0][0][0]]);assert.equal((await service.suggestions(actor,grid,{q:'OUTSIDA'})).total,0);
 await assert.rejects(()=>service.suggestions(f.users[4],grid,{q:'ABCDEX'}),{code:'FORBIDDEN'});
 const route='/api/batch-work/grids/'+grid+'/suggestions',staff=request.agent(app.getHttpServer());assert.equal((await request(app.getHttpServer()).get(route).query({q:'ABCDEX'})).status,401);
 assert.equal((await staff.post('/api/auth/login').send({username:f.codes[2],password:'fixture-suggest-pass'})).status,201);
 const response=await staff.get(route).query({q:'ABCDEX'});assert.equal(response.status,200);assert.equal(response.body.total,7);assert.equal(response.body.strategyVersion,'name-edit-one-v1');
 assert.equal((await staff.get(route).query({q:'ABCDEX',page:0})).status,400);
 await db.query('UPDATE bd_grid_task SET assignee_id=$1 WHERE id=$2',[f.users[3],grid]);assert.equal((await staff.get(route).query({q:'ABCDEX'})).status,403,'assignment changes revoke suggestion access immediately');
 assert.equal((await db.query('SELECT count(*)::int AS n FROM bd_store_search_log WHERE grid_task_id=$1',[grid])).rows[0].n,0,'suggestions are not logged as successful ordinary searches');
 assert.equal((await db.query('SELECT count(*)::int AS n FROM bd_task_event')).rows[0].n,beforeEvents);
 }finally{if(f)await clearBatchWork(db,f);await app.close();}
});

test('suggestion HTTP budget covers a representative 3062-store grid without an extension or truncated counts',{skip:!url},async()=>{
 assert.equal(process.env.NEXT_TEST_ISOLATED,'1');const app=await createApp({databaseUrl:url!,sessionSecret:'suggestion-http-budget-secret-more-than-forty-characters',cookieSecure:false,port:0,host:'127.0.0.1'}),db=app.get(Database);let f:any;
 try{f=await seedBatchWork(db,'SUGGEST-HTTP-'+Date.now(),await hash('fixture-suggest-pass'));const grid=f.grids[0][1];
 const created=(await db.query(`INSERT INTO bd_store_task(grid_task_id,store_id,snapshot,status,result,revision,updated_at) SELECT $1,$2||n::text,jsonb_build_object('name','検索中央薬局','address','mock address'),'pending','{}',0,now() FROM generate_series(1,3059) AS n RETURNING id`,[grid,f.prefix+'-'])).rows;f.stores[0][1].push(...created.map((r:any)=>r.id));
 const client=request.agent(app.getHttpServer());assert.equal((await client.post('/api/auth/login').send({username:f.codes[2],password:'fixture-suggest-pass'})).status,201);const route='/api/batch-work/grids/'+grid+'/suggestions';
 for(let i=0;i<10;i++)assert.equal((await client.get(route).query({q:'検索中夫薬局'})).status,200);
 const times:number[]=[];let index=0;await Promise.all(Array.from({length:5},async()=>{while(index<200){index++;const start=performance.now();const r=await client.get(route).query({q:'検索中夫薬局'});times.push(performance.now()-start);assert.equal(r.status,200);assert.equal(r.body.status,'ready');assert.equal(r.body.total,1);assert.equal(r.body.candidateTotal,3059);}}));
 times.sort((a,b)=>a-b);const p95=times[190],report={gridStores:3062,requests:200,concurrency:5,p50Ms:times[100],p95Ms:p95,gatePassed:p95<=300,provider:'bounded Unicode one-edit names; no pg_trgm installed',candidateCount:3059,suggestionCount:1,database:'disposable',productionAccessed:false};
 assert.equal((await db.query("SELECT count(*)::int AS n FROM pg_extension WHERE extname IN ('pg_trgm','fuzzystrmatch')")).rows[0].n,0);assert.equal((await db.query('SELECT count(*)::int AS n FROM bd_store_search_log')).rows[0].n,0);
 await mkdir('data/refactor/search-evaluation',{recursive:true,mode:0o700});await writeFile('data/refactor/search-evaluation/suggestions-http-'+Date.now()+'.json',JSON.stringify(report,null,2),{mode:0o600,flag:'wx'});console.log(JSON.stringify({suggestionHttpAcceptance:report}));assert.ok(p95<=300,'suggestions API p95 must remain within 300ms');
 }finally{if(f)await clearBatchWork(db,f);await app.close();}
});
