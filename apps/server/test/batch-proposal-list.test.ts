import 'reflect-metadata';
import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {hash} from '@node-rs/argon2';
import {createApp} from '../src/main';
import {Database} from '../src/database';
import {BatchWorkService} from '../src/batch-work';
import {seedBatchWork,clearBatchWork} from '../../../scripts/batch-work-fixture.mjs';
const url=process.env.NEXT_TEST_DATABASE_URL;
test('batch proposal list scopes applicants and exposes filtered review records without photo payloads',{skip:!url},async()=>{
 const app=await createApp({databaseUrl:url!,sessionSecret:'batch-proposal-list-test-secret-at-least-forty-characters',cookieSecure:false,port:0,host:'127.0.0.1'}),db=app.get(Database),s=app.get(BatchWorkService);let f:any;
 try{
  f=await seedBatchWork(db,'H5-PROPOSAL-LIST-'+Date.now(),await hash('fixture-pass'));
  const grid=f.grids[0][0],main=f.mains[0],leader=f.users[1],worker=f.users[2];
  for(const state of ['pending','approved','rejected'])await db.query(`INSERT INTO bd_store_proposal(grid_task_id,submitted_by,request_id,payload,state,revision,reason,created_at) VALUES($1,$2,$3,$4,$5,0,'',now())`,[grid,worker,randomUUID(),JSON.stringify({name:'申请'+state,address:'合成地址',lat:35.006,lon:139.006,photo:'private-photo'}),state]);
  const pending=await s.batchProposals(leader,main,{});assert.equal(pending.total,1);assert.equal(pending.items[0].state,'pending');assert.equal(pending.items[0].canReview,true);assert.equal(pending.canApprove,true);assert.ok(pending.items[0].submitterName);assert.equal('payload' in pending.items[0],false);assert.equal('photo' in pending.items[0],false);
  const all=await s.batchProposals(leader,main,{state:''});assert.equal(all.total,3);assert.equal(all.submitters.length,1);
  assert.equal((await s.batchProposals(leader,main,{state:'rejected',region:f.regions[0],grid,submitter:worker})).total,1);
  assert.equal((await s.batchProposals(leader,main,{grid:2147483647})).total,0);
  assert.equal((await s.batchProposals(leader,main,{page:2})).items.length,0);
  const own=await s.batchProposals(worker,main,{state:''});assert.equal(own.total,3);assert.equal(own.canReview,false);assert.ok(own.items.every((p:any)=>!p.canReview));
  const admin=await s.batchProposals(f.users[0],main,{});assert.equal(admin.canReview,false);
  await assert.rejects(()=>s.batchProposals(f.users[4],main,{}),{code:'FORBIDDEN'});
  await assert.rejects(()=>s.batchProposals(leader,main,{state:'invalid'}));
  await db.query("UPDATE bd_main_task SET status='completed' WHERE id=$1",[main]);assert.equal((await s.batchProposals(leader,main,{})).canApprove,false);
  await db.query("UPDATE bd_main_task SET status='disabled' WHERE id=$1",[main]);const disabled=await s.batchProposals(leader,main,{state:''});assert.equal(disabled.total,3);assert.equal(disabled.canReview,false);assert.ok(disabled.items.every((p:any)=>!p.canReview));
 }finally{if(f){await db.query('DELETE FROM bd_store_proposal WHERE grid_task_id=ANY($1::int[])',[f.grids.flat()]);await clearBatchWork(db,f);}await app.close();}
});
