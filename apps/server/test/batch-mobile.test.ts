import 'reflect-metadata';import test from 'node:test';import assert from 'node:assert/strict';import {hash} from '@node-rs/argon2';
import {createApp} from '../src/main';import {Database} from '../src/database';import {BatchWorkService} from '../src/batch-work';import {seedBatchWork,clearBatchWork} from '../../../scripts/batch-work-fixture.mjs';
const url=process.env.NEXT_TEST_DATABASE_URL;
test('mobile work reads enforce scope, page twenty, nearest order and dual filters; auto preview never writes and stale apply rejects',{skip:!url},async()=>{
 const app=await createApp({databaseUrl:url!,sessionSecret:'mobile-batch-tests-at-least-forty-characters-secret',cookieSecure:false,port:0,host:'127.0.0.1'}),db=app.get(Database),s=app.get(BatchWorkService);let f:any;
 try{f=await seedBatchWork(db,'H5-MOBILE-'+Date.now(),await hash('fixture-pass'));const grid=f.grids[0][1],staff=f.users[2],leader=f.users[1];
 const page=await s.visits(staff,grid,{});assert.equal(page.pageSize,20);assert.ok(page.canExecute);assert.equal(page.total,3);assert.equal((await s.visits(leader,grid,{})).canExecute,false,'captain view is read-only in the mobile visit flow');assert.equal((await s.regionDetail(staff,f.regions[0])).canAssign,false);await assert.rejects(()=>s.visits(f.users[4],grid,{}));
 await db.query('UPDATE bd_region_task SET progress=33 WHERE id=$1',[f.regions[0]]);assert.equal((await s.regionDetail(staff,f.regions[0])).progress,0,'mobile progress derives from merchant counters, not legacy child averages');
 const ids=[];for(let i=0;i<23;i++)ids.push((await db.query("INSERT INTO bd_store_task(grid_task_id,store_id,snapshot,status,result,revision,updated_at) VALUES($1,$2,$3,'pending',$4,0,now()) RETURNING id",[grid,'h5-extra-'+i,JSON.stringify({name:'测试'+i,lat:35+i*.0001,lon:139}),JSON.stringify({business_status:i%2?'closed':'normal'})])).rows[0].id);f.stores[0][1].push(...ids);
 assert.equal((await s.visits(staff,grid,{})).items.length,20);assert.equal((await s.visits(staff,grid,{page:2})).items.length,6);const filtered=await s.visits(staff,grid,{q:'测试',state:'pending',businessStatus:'closed'});assert.equal(filtered.total,11);const near=await s.visits(staff,grid,{q:'测试',lat:35.0022,lon:139});assert.equal(near.items[0].storeId,'h5-extra-22');
 const nonOwner=await s.visits(leader,grid,{});assert.equal(nonOwner.readOnlyReason,'not_assignee');
 await db.query('UPDATE bd_grid_task SET assignee_id=$1 WHERE id=$2',[leader,grid]);
 const captainOwn=await s.visits(leader,grid,{});assert.equal(captainOwn.canExecute,true);assert.equal(captainOwn.readOnlyReason,null);
 await db.query('UPDATE bd_grid_task SET assignee_id=$1 WHERE id=$2',[staff,grid]);
 assert.equal((await s.visits(f.users[0],grid,{})).readOnlyReason,'account_role');
 await db.query("UPDATE bd_main_task SET status='completed' WHERE id=$1",[f.mains[0]]);assert.equal((await s.visits(staff,grid,{})).readOnlyReason,'batch_completed');
 await db.query("UPDATE bd_main_task SET status='active' WHERE id=$1",[f.mains[0]]);
 const target=page.items[0];await s.location(staff,target.id,{revision:target.revision,profileRevision:target.profileRevision,accuracy:'accurate',lat:35,lon:139});const lookup=await s.placeLookup(staff,target.id);assert.equal(lookup.placeLookupState,'unconfigured');await assert.rejects(()=>s.placeLookup(leader,target.id),{code:'FORBIDDEN'});
 const before=JSON.stringify((await db.query('SELECT id,assignee_id,revision FROM bd_grid_task WHERE region_task_id=$1 ORDER BY id',[f.regions[0]])).rows);
 await assert.rejects(()=>s.autoPreview(staff,f.regions[0],{memberIds:[staff]}),{code:'FORBIDDEN'});
 const fakeModel=async()=>({data:{weights:{[staff]:1},reason:'测试负载均衡'},tokens:1,model:'mock'});
 const preview=await s.autoPreview(leader,f.regions[0],{memberIds:[staff],includeBatch:true,useEfficiency:false},fakeModel);assert.ok(preview.token);assert.equal(JSON.stringify((await db.query('SELECT id,assignee_id,revision FROM bd_grid_task WHERE region_task_id=$1 ORDER BY id',[f.regions[0]])).rows),before);
 await db.query('UPDATE bd_grid_task SET revision=revision+1 WHERE id=$1',[grid]);await assert.rejects(()=>s.autoApply(leader,f.regions[0],{token:preview.token}),{code:'TASK_CHANGED'});
 const fresh=await s.autoPreview(leader,f.regions[0],{memberIds:[staff]},fakeModel);await s.autoApply(leader,f.regions[0],{token:fresh.token});await assert.rejects(()=>s.autoApply(leader,f.regions[0],{token:fresh.token}),{code:'TASK_CHANGED'});
 await db.query("UPDATE bd_main_task SET status='disabled' WHERE id=$1",[f.mains[0]]);assert.equal((await s.regionDetail(leader,f.regions[0])).canAssign,false);assert.equal((await s.mobileTree(leader,f.mains[0])).regions[0].canAssign,false);assert.equal((await s.visits(staff,grid,{})).canExecute,false);assert.equal((await s.visits(staff,grid,{})).readOnlyReason,'batch_disabled');

 }finally{if(f)await clearBatchWork(db,f);await app.close();}
});
