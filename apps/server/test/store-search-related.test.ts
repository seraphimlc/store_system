import request from '../node_modules/supertest';
import 'reflect-metadata';
import test from 'node:test';
import assert from 'node:assert/strict';
import {hash} from '@node-rs/argon2';
import {createApp} from '../src/main';
import {Database} from '../src/database';
import {BatchWorkService} from '../src/batch-work';
import {seedBatchWork,clearBatchWork} from '../../../scripts/batch-work-fixture.mjs';
const url=process.env.NEXT_TEST_DATABASE_URL;
test('related words are explicit, scoped, name-only, paginated, field-specific and separately logged',{skip:!url},async()=>{
 assert.equal(process.env.NEXT_TEST_ISOLATED,'1');assert.match(new URL(url!).pathname,/^\/store_test_\d{13}_[a-f0-9]{12}_refactor_p1$/);
 const app=await createApp({databaseUrl:url!,sessionSecret:'related-search-tests-secret-at-least-forty-characters',cookieSecure:false,port:0,host:'127.0.0.1'}),db=app.get(Database),service=app.get(BatchWorkService);let f:any;
 try{
  f=await seedBatchWork(db,'SEARCH-RELATED-'+Date.now(),await hash('fixture-pass'));
  const grid=f.grids[0][1],actor=f.users[2];
  const add=async(name:string,address='',status='pending',target=grid)=>{
   const row=(await db.query(`INSERT INTO bd_store_task(grid_task_id,store_id,snapshot,status,result,revision,updated_at) VALUES($1,$2,$3,$4,'{}',0,now()) RETURNING id`,[target,'related-'+crypto.randomUUID(),JSON.stringify({name,address}),status])).rows[0];
   const index=f.grids[0].indexOf(target);f.stores[0][index].push(row.id);return row.id;
  };
  const first=await add('xx药店','长乐路');
  await add('便利商店','药店以南');
  await add('网格外药店','', 'pending',f.grids[0][0]);
  assert.equal((await service.visits(actor,grid,{q:'长乐药店'})).total,0,'default API never silently drops query words');
  const related=await service.visits(actor,grid,{q:'长乐药店',related:'1'});
  assert.equal(related.total,1);assert.equal(related.literalTotal,0);assert.equal(related.searchMode,'related');assert.equal(related.searchVersion,'related-name-words-v1');assert.equal(related.items[0].id,first);
  assert.deepEqual(related.items[0].searchMatch,{mode:'related',nameTerms:['药店'],addressTerms:[],idTerms:[]});
  const staff=request.agent(app.getHttpServer()),route='/api/batch-work/grids/'+grid+'/visits';
  assert.equal((await staff.post('/api/auth/login').send({username:f.codes[2],password:'fixture-pass'})).status,201);
  const http=await staff.get(route).query({q:'长乐药店',related:'1'});assert.equal(http.status,200);assert.equal(http.body.searchMode,'related');assert.deepEqual(http.body.items[0].searchMatch,related.items[0].searchMatch);
  assert.equal((await staff.get(route).query({q:'长乐药店',related:'0'})).status,400);
  assert.equal((await request(app.getHttpServer()).get(route).query({q:'长乐药店',related:'1'})).status,401);

  const log=(await db.query('SELECT strategy_version,matched_count,keyword_match_count,result_task_ids FROM bd_store_search_log WHERE id=$1',[related.searchLogId])).rows[0];
  assert.equal(log.strategy_version,'related-name-words-v1');assert.equal(log.matched_count,1);assert.equal(log.keyword_match_count,0);assert.deepEqual(log.result_task_ids,[first]);
  await assert.rejects(()=>service.visits(f.users[4],grid,{q:'长乐药店',related:'1'}),{code:'FORBIDDEN'});
  assert.equal((await service.visits(actor,grid,{q:'长乐药店',related:'1',state:'completed'})).total,0);
  assert.equal((await service.visits(actor,grid,{q:'长乐药店',related:'1',businessStatus:'closed'})).total,0);
  assert.equal((await service.visits(actor,grid,{q:'12345',related:'1'})).relatedStatus,'unsupported_query');
  for(let i=0;i<24;i++)await add('样本'+i+'药店');
  const page1=await service.visits(actor,grid,{q:'长乐药店',related:'1'}),page2=await service.visits(actor,grid,{q:'长乐药店',related:'1',page:2});
  assert.equal(page1.total,25);assert.equal(page2.total,25);assert.equal(page1.items.length,20);assert.equal(page2.items.length,5);assert.equal(new Set([...page1.items,...page2.items].map((s:any)=>s.id)).size,25);
  const literal=await add('长乐药店','', 'completed');
  const full=await service.visits(actor,grid,{q:'长乐药店',related:'1'});assert.equal(full.searchMode,'literal');assert.deepEqual(full.items.map((s:any)=>s.id),[literal]);
  const filtered=await service.visits(actor,grid,{q:'长乐药店',related:'1',state:'pending'});assert.equal(filtered.total,0);assert.equal(filtered.relatedStatus,'filters_excluded','do not disguise an excluded literal result with partial alternatives');
  const bulk=(await db.query(`INSERT INTO bd_store_task(grid_task_id,store_id,snapshot,status,result,revision,updated_at) SELECT $1,'related-bulk-'||$2||'-'||n,jsonb_build_object('name','规模药店'),'pending','{}',0,now() FROM generate_series(1,5001) n RETURNING id`,[grid,crypto.randomUUID()])).rows;f.stores[0][1].push(...bulk.map((row:any)=>row.id));
  const bounded=await service.visits(actor,grid,{q:'未知药店',related:'1'});assert.equal(bounded.relatedStatus,'scope_too_large');assert.equal(bounded.total,0);

 }finally{if(f){await db.query('DELETE FROM bd_store_search_log WHERE grid_task_id=ANY($1::int[])',[f.grids.flat()]);await clearBatchWork(db,f);}await app.close();}
});
