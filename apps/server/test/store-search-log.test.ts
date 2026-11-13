import 'reflect-metadata';
import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {readFile} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import {createApp} from '../src/main';
import {Database} from '../src/database';
import {BatchWorkService} from '../src/batch-work';
import {hash} from '@node-rs/argon2';
import {seedBatchWork,clearBatchWork} from '../../../scripts/batch-work-fixture.mjs';
const url=process.env.NEXT_TEST_DATABASE_URL;
test('search log SQL creates a missing table and is repeatable without losing logs',{skip:!url},async()=>{
 const app=await createApp({databaseUrl:url!,sessionSecret:'search-log-migration-secret-at-least-forty-characters',cookieSecure:false,port:0,host:'127.0.0.1'}),db=app.get(Database);
 const schema='search_log_'+randomUUID().replaceAll('-',''),sql=await readFile('deploy-next/migrations/002-store-search-log.sql','utf8');
 try{await db.transaction(async client=>{
  await client.query(`CREATE SCHEMA "${schema}"`);await client.query(`SET LOCAL search_path TO "${schema}"`);
  await client.query(sql);
  await client.query("INSERT INTO bd_store_search_log(main_task_id,region_task_id,grid_task_id,query_text,created_at) VALUES(1,2,3,'schema-probe',timezone('UTC',now()))");
  await client.query(sql);
  const row=(await client.query('SELECT * FROM bd_store_search_log')).rows[0];assert.equal(row.query_text,'schema-probe');assert.equal(row.matched_count,0);assert.equal(row.used_location,false);assert.equal(Object.keys(row).length,21);
  assert.equal((await client.query('SELECT count(*)::int AS n FROM pg_indexes WHERE schemaname=$1',[schema])).rows[0].n,3);
  await client.query('SET LOCAL search_path TO public');await client.query(`DROP SCHEMA "${schema}" CASCADE`);
 });}finally{await app.close();}
});
test('search audit distinguishes filters and normalization evidence, ignores paging, and feeds read-only daily summary',{skip:!url},async()=>{
 const app=await createApp({databaseUrl:url!,sessionSecret:'search-log-test-secret-at-least-forty-characters',cookieSecure:false,port:0,host:'127.0.0.1'}),db=app.get(Database),service=app.get(BatchWorkService);let f:any;
 try{
  f=await seedBatchWork(db,'SEARCH-LOG-'+Date.now(),await hash('fixture-pass'));
  const grid=f.grids[0][0],actor=f.users[2],store=f.stores[0][0][0];
  await db.query("UPDATE bd_store_task SET snapshot=jsonb_build_object('name','ＡＢＣ　商店','address','テスト住所') WHERE id=$1",[store]);
  const search=async(q:string,state='')=>service.visits(actor,grid,{q,state});
  assert.equal((await search('不存在的店')).total,0);
  assert.equal((await search('ABC商店')).total,0);
  assert.equal((await search('ＡＢＣ','completed')).total,0);
  assert.equal((await search('ＡＢＣ')).total,1);
  const before=(await db.query('SELECT count(*)::int AS n FROM bd_store_search_log WHERE grid_task_id=$1',[grid])).rows[0].n;
  await service.visits(actor,grid,{q:'ＡＢＣ',page:2});await service.visits(actor,grid,{});
  assert.equal((await db.query('SELECT count(*)::int AS n FROM bd_store_search_log WHERE grid_task_id=$1',[grid])).rows[0].n,before);
  const rows=(await db.query('SELECT * FROM bd_store_search_log WHERE grid_task_id=$1 ORDER BY id',[grid])).rows;
  assert.equal(rows.length,4);assert.deepEqual(rows.map(r=>r.no_result_reason),['unresolved','normalization_candidate','filters_excluded','']);
  assert.equal(rows[1].normalized_match_count,1);assert.equal(rows[2].keyword_match_count,1);assert.equal(rows[3].matched_count,1);assert.equal(rows[0].actor_id,actor);assert.ok(rows.every(r=>r.duration_ms>=0));
  assert.equal((await db.query('SELECT count(*)::int AS n FROM bd_task_event WHERE main_task_id=$1',[f.mains[0]])).rows[0].n,0,'searches do not create business events');
  await assert.rejects(()=>service.visits(f.users[4],grid,{q:'店'}),{code:'FORBIDDEN'});
  const summary=spawnSync(process.execPath,['scripts/analyze-store-search.mjs'],{env:{...process.env,NEXT_DATABASE_URL:url!},encoding:'utf8'});assert.equal(summary.status,0,summary.stderr);const report=JSON.parse(summary.stdout);assert.equal(report.searches,4);assert.equal(report.zero_result_searches,3);assert.equal(report.result_searches,1);assert.equal(Number(report.result_rate_percent),25);
  const empty=spawnSync(process.execPath,['scripts/analyze-store-search.mjs','--date','2000-01-01'],{env:{...process.env,NEXT_DATABASE_URL:url!},encoding:'utf8'});assert.equal(empty.status,0,empty.stderr);assert.equal(JSON.parse(empty.stdout).result_rate_percent,null);
  // Logging failure must not turn a valid search into an error.
  await db.transaction(async client=>{await client.query('ALTER TABLE bd_store_search_log RENAME TO bd_store_search_log_offline');});
  try{assert.equal((await search('ＡＢＣ')).total,1);}finally{await db.query('ALTER TABLE bd_store_search_log_offline RENAME TO bd_store_search_log');}
 }finally{if(f){await db.query('DELETE FROM bd_store_search_log WHERE grid_task_id=ANY($1::int[])',[f.grids.flat()]);await clearBatchWork(db,f);}await app.close();}
});
