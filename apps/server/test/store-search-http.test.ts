import 'reflect-metadata';
import test from 'node:test';import assert from 'node:assert/strict';
import request from '../node_modules/supertest';import {hash} from '@node-rs/argon2';
import {performance} from 'node:perf_hooks';import {mkdir,writeFile} from 'node:fs/promises';
import {createApp} from '../src/main';import {Database} from '../src/database';
import {seedBatchWork,clearBatchWork} from '../../../scripts/batch-work-fixture.mjs';
const url=process.env.NEXT_TEST_DATABASE_URL;
test('authenticated HTTP search at representative grid size preserves permissions and records strategy-specific results',{skip:!url},async()=>{
 assert.equal(process.env.NEXT_TEST_ISOLATED,'1');assert.match(new URL(url!).pathname,/^\/store_test_\d{13}_[a-f0-9]{12}_refactor_p1$/);
 const app=await createApp({databaseUrl:url!,sessionSecret:'search-http-tests-secret-at-least-forty-characters',cookieSecure:false,port:0,host:'127.0.0.1'}),db=app.get(Database);let f:any;
 try{
  f=await seedBatchWork(db,'SEARCH-HTTP-'+Date.now(),await hash('fixture-search-pass'));
  const grid=f.grids[0][1],route='/api/batch-work/grids/'+grid+'/visits';
  const created=(await db.query(`INSERT INTO bd_store_task(grid_task_id,store_id,snapshot,status,result,revision,updated_at)
   SELECT $1,$2||n::text,jsonb_build_object('name','検索店 '||n::text,'address','synthetic address','lat',35,'lon',139),'pending','{}',0,now() FROM generate_series(1,3059) AS n RETURNING id`,[grid,f.prefix+'-extra-'])).rows;
  f.stores[0][1].push(...created.map((r:any)=>r.id));
  await db.query("UPDATE bd_store_task SET snapshot=jsonb_build_object('name','ＡＢＣ　商店','address','synthetic address') WHERE id=$1",[created[0].id]);
  await db.query('UPDATE bd_grid_task SET total_stores=3062 WHERE id=$1',[grid]);
  const staff=request.agent(app.getHttpServer()),outsider=request.agent(app.getHttpServer());
  assert.equal((await staff.post('/api/auth/login').send({username:f.codes[2],password:'fixture-search-pass'})).status,201);
  assert.equal((await outsider.post('/api/auth/login').send({username:f.codes[4],password:'fixture-search-pass'})).status,201);
  assert.equal((await request(app.getHttpServer()).get(route).query({q:'ABC商店'})).status,401);
  assert.equal((await outsider.get(route).query({q:'ABC商店'})).status,403);
  const hit=await staff.get(route).query({q:'ABC商店'});assert.equal(hit.status,200);assert.equal(hit.body.total,1);assert.equal(hit.body.items[0].id,created[0].id);assert.equal(hit.body.searchVersion,'terms-nfkc-v2');
  const miss=await staff.get(route).query({q:'definitely-absent-http-probe'});assert.equal(miss.status,200);assert.equal(miss.body.total,0);
  assert.equal((await staff.get(route).query({q:'ABC商店',businessStatus:'closed'})).body.total,0);
  const combined=await staff.get(route).query({q:'synthetic 検索店'});assert.equal(combined.status,200);assert.equal(combined.body.total,3058);assert.equal((await staff.get(route).query({q:'synthetic definitely-absent'})).body.total,0);
  for(let i=0;i<10;i++){const r=await staff.get(route).query({q:'検索店'});assert.equal(r.status,200);}
  const samples:number[]=[],single:number[]=[],multiword:number[]=[];let cursor=0;
  await Promise.all(Array.from({length:5},async()=>{while(cursor<400){const n=cursor++;const start=performance.now();const r=await staff.get(route).query({q:n%2?'synthetic 検索店':'検索店'});const duration=performance.now()-start;samples.push(duration);(n%2?multiword:single).push(duration);assert.equal(r.status,200);assert.equal(r.body.total,3058);assert.equal(r.body.items.length,20);}}));
  samples.sort((a,b)=>a-b);single.sort((a,b)=>a-b);multiword.sort((a,b)=>a-b);const p95=samples[Math.floor(samples.length*.95)],singleP95=single[Math.floor(single.length*.95)],multiwordP95=multiword[Math.floor(multiword.length*.95)];
  const logs=(await db.query('SELECT strategy_version,count(*)::int AS n FROM bd_store_search_log WHERE grid_task_id=$1 GROUP BY strategy_version',[grid])).rows;assert.equal(logs.length,1);assert.equal(logs[0].strategy_version,'terms-nfkc-v2');assert.equal(logs[0].n,415);
  const report={source:'isolated synthetic fixture, not user relevance evidence',database:'disposable',gridStores:3062,httpRequests:400,concurrency:5,p50Ms:samples[200],p95Ms:p95,singleTerm:{n:single.length,p95Ms:singleP95},multiTerm:{n:multiword.length,p95Ms:multiwordP95},includes:'HTTP parsing, login-cookie authentication, service permissions, SQL and log write',excludes:'browser rendering, public network, real-user target labeling',latencyGatePassed:p95<=300&&singleP95<=300&&multiwordP95<=300,permissionChecksPassed:true,productionAccessed:false};
  await mkdir('data/refactor/search-evaluation',{recursive:true,mode:0o700});await writeFile('data/refactor/search-evaluation/http-acceptance-'+Date.now()+'.json',JSON.stringify(report,null,2),{mode:0o600,flag:'wx'});console.log(JSON.stringify({searchHttpAcceptance:report}));
  assert.ok(p95<=300&&singleP95<=300&&multiwordP95<=300,'Representative authenticated HTTP search must meet the planned 300ms p95 gate');
 }finally{if(f)await clearBatchWork(db,f);await app.close();}
});
