import 'reflect-metadata';import test from 'node:test';import assert from 'node:assert/strict';import {createRequire} from 'node:module';import {randomUUID} from 'node:crypto';
import {retainSearchTelemetry,retentionTarget} from '../../../scripts/search-log-retention.mjs';
const {Pool}=createRequire(import.meta.url)('pg'),url=process.env.NEXT_TEST_DATABASE_URL;
test('telemetry retention previews without writes, protects recent feedback, deletes one bounded batch and cascades only its old events',{skip:!url},async()=>{
 const target=retentionTarget(url!,process.env.NEXT_TEST_ISOLATED),pool=new Pool({connectionString:target.url.href,max:1}),client=await pool.connect();
 try{
  const baseline=(await client.query('SELECT (SELECT count(*) FROM users) AS users,(SELECT count(*) FROM bd_store_task) AS tasks,(SELECT count(*) FROM formal_records) AS formal')).rows[0];
  const cutoff='2026-10-01T00:00:00.000Z',ids:number[]=[];
  for(const created of ['2026-09-01','2026-09-02','2026-09-03','2026-10-02','2026-10-01'])ids.push((await client.query("INSERT INTO bd_store_search_log(main_task_id,region_task_id,grid_task_id,query_text,created_at) VALUES(1,1,1,'fixture-retention',$1) RETURNING id",[created])).rows[0].id);
  for(const [log,created]of [[ids[0],'2026-09-10'],[ids[1],'2026-10-02'],[ids[3],'2026-10-02']])await client.query("INSERT INTO bd_store_search_feedback(event_id,search_log_id,actor_id,kind,store_task_id,strategy_version,created_at) VALUES($1,$2,1,'opened',1,'fixture',$3)",[randomUUID(),log,created]);
  const preview=await retainSearchTelemetry(client,{apply:false,before:cutoff,limit:1},target.database);assert.equal(preview.previewReadOnly,true);assert.equal(preview.eligible_logs,2);assert.equal(preview.old_logs_protected_by_recent_feedback,1);assert.equal(preview.selectedLogs,1);assert.equal(preview.selectedFeedback,1);assert.equal(preview.deletedLogs,0);
  assert.equal((await client.query('SELECT count(*)::int AS n FROM bd_store_search_log')).rows[0].n,5);
  const apply=await retainSearchTelemetry(client,{apply:true,before:cutoff,limit:1,confirmDatabase:target.database},target.database);assert.equal(apply.deletedLogs,1);assert.equal(apply.deletedFeedback,1);assert.equal(apply.remainingEligibleEstimate,1);
  assert.deepEqual((await client.query('SELECT id FROM bd_store_search_log ORDER BY id')).rows.map((r:any)=>r.id),ids.slice(1));assert.equal((await client.query('SELECT count(*)::int AS n FROM bd_store_search_feedback')).rows[0].n,2);
  const second=await retainSearchTelemetry(client,{apply:true,before:cutoff,limit:1,confirmDatabase:target.database},target.database);assert.equal(second.deletedLogs,1);assert.equal(second.deletedFeedback,0);
  const done=await retainSearchTelemetry(client,{apply:true,before:cutoff,limit:1,confirmDatabase:target.database},target.database);assert.equal(done.deletedLogs,0);assert.equal(done.eligible_logs,0);
  assert.deepEqual((await client.query('SELECT (SELECT count(*) FROM users) AS users,(SELECT count(*) FROM bd_store_task) AS tasks,(SELECT count(*) FROM formal_records) AS formal')).rows[0],baseline);
 }finally{client.release();await pool.end();}
});
