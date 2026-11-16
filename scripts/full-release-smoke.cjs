/** Inside the candidate/production API image. Read-only, no session, telemetry or provider calls. */
require('reflect-metadata');
const assert=require('node:assert/strict'),{createRequire}=require('node:module');
const r=createRequire('/app/apps/server/dist/apps/server/src/main.js'),{Pool}=r('pg'),{BatchWorkService}=r('./batch-work');
(async()=>{r('@node-rs/argon2');r('sharp');const pool=new Pool({connectionString:process.env.NEXT_DATABASE_URL,max:1,statement_timeout:10000,options:'-c default_transaction_read_only=on'});let checked=0;
try{const db={transaction:async fn=>{const c=await pool.connect();try{await c.query('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY');return await fn(c);}finally{await c.query('ROLLBACK');c.release();}}};const service=new BatchWorkService(db);
const actor=(await pool.query("SELECT id FROM users WHERE role='admin' AND is_active=true AND status='active' ORDER BY id LIMIT 1")).rows[0];assert(actor);
const before=(await pool.query("SELECT count(*)::int AS n,md5(coalesce(string_agg(md5(snapshot::text),'' ORDER BY id),'')) AS snapshots FROM bd_grid_task")).rows[0];
const batches=(await pool.query('SELECT id FROM bd_main_task ORDER BY id DESC LIMIT 3')).rows;
for(const batch of batches){const map=await service.batchMap(actor.id,batch.id);const ids=map.features.map(f=>f.id);if(!ids.length)continue;const expected=(await pool.query("SELECT t.id,coalesce(w.geometry,t.snapshot->'geometry') AS geometry FROM bd_grid_task t LEFT JOIN bd_work_grid w ON w.id=t.grid_id WHERE t.id=ANY($1::int[])",[ids])).rows;for(const f of map.features){assert.deepEqual(f.geometry,expected.find(row=>row.id===f.id).geometry);checked++;}const grid=await service.map(actor.id,ids[0],{limit:1});assert.deepEqual(grid.boundary,expected.find(row=>row.id===ids[0]).geometry);}
assert(checked>0,'No batch geometry available for read-only acceptance');const after=(await pool.query("SELECT count(*)::int AS n,md5(coalesce(string_agg(md5(snapshot::text),'' ORDER BY id),'')) AS snapshots FROM bd_grid_task")).rows[0];assert.deepEqual(after,before);
assert.equal((await fetch('http://127.0.0.1:3000/api/health/ready')).status,200);assert.equal((await fetch('http://127.0.0.1:3000/api/batch-work/batches/'+batches[0].id+'/map')).status,401);
console.log(JSON.stringify({ok:true,currentGridReferences:checked,taskSnapshotsUnchanged:true,unauthorized:401,readOnly:true,nativeDependencies:true}));}finally{await pool.end();}})().catch(e=>{console.error(e.stack);process.exitCode=1;});
