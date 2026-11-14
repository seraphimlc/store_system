import {readFile} from 'node:fs/promises';
import request from '../node_modules/supertest';
import 'reflect-metadata';
import test from 'node:test';import assert from 'node:assert/strict';import {hash} from '@node-rs/argon2';
import {createApp} from '../src/main';import {Database} from '../src/database';import {BatchWorkService} from '../src/batch-work';
import {seedBatchWork,clearBatchWork} from '../../../scripts/batch-work-fixture.mjs';
const url=process.env.NEXT_TEST_DATABASE_URL;
test('explicit batch search groups the entire authorized result set before pagination and rechecks feedback authorization',{skip:!url},async()=>{
 assert.equal(process.env.NEXT_TEST_ISOLATED,'1');
 const app=await createApp({databaseUrl:url!,sessionSecret:'scope-search-tests-secret-at-least-forty-characters',cookieSecure:false,port:0,host:'127.0.0.1'}),db=app.get(Database),service=app.get(BatchWorkService);let f:any;const extra:any={};
 try{
  const migration=await readFile('deploy-next/migrations/006-store-search-scope.sql','utf8');await db.query(migration);await db.query(migration);
  f=await seedBatchWork(db,'SEARCH-SCOPE-'+Date.now(),await hash('fixture-pass'));
  const grid=f.grids[0][1],other=f.grids[0][0],actor=f.users[2];
  await db.query("UPDATE bd_store_task SET snapshot=jsonb_build_object('name','范围药店') WHERE grid_task_id=ANY($1::int[])",[[grid,other,f.grids[1][0]]]);
  const foreign=(await db.query(`INSERT INTO bd_store_task(grid_task_id,store_id,snapshot,status,result,revision,updated_at) SELECT $1,$2||n,jsonb_build_object('name','范围药店 '||n),'pending','{}',0,now() FROM generate_series(1,23)n RETURNING id`,[other,f.prefix+'-group-'])).rows;f.stores[0][0].push(...foreign.map((r:any)=>r.id));
  // A second region in the same batch verifies that region grouping is not just relabelling one grid.
  extra.region=(await db.query(`INSERT INTO bd_region(release_id,source_id,name,geometry,area_km2,store_count,review,state,revision,updated_at) SELECT release_id,source_id||'-extra','第二片区',geometry,1,1,'[]','ready',0,now() FROM bd_region WHERE id=$1 RETURNING id`,[f.region])).rows[0].id;
  extra.baseGrid=(await db.query(`INSERT INTO bd_work_grid(release_id,zone_id,source_id,name,stations,keywords,geometry,area_km2,store_count,state,task_state,progress,revision,updated_at) SELECT release_id,$2,source_id||'-extra','第二片区网格','[]','[]',geometry,1,1,'ready','unassigned',0,0,now() FROM bd_work_grid WHERE id=$1 RETURNING id`,[f.baseGrids[0],extra.region])).rows[0].id;
  extra.regionTask=(await db.query(`INSERT INTO bd_region_task(main_task_id,region_id,snapshot,team_id,leader_id,status,progress,revision,created_at) VALUES($1,$2,'{"name":"第二片区"}',$3,$4,'pending',0,0,now()) RETURNING id`,[f.mains[0],extra.region,f.team,f.users[1]])).rows[0].id;
  extra.grid=(await db.query(`INSERT INTO bd_grid_task(region_task_id,grid_id,snapshot,assignee_id,status,progress,total_stores,completed_stores,revision,created_at) VALUES($1,$2,'{"name":"第二片区网格"}',$3,'assigned',0,1,0,0,now()) RETURNING id`,[extra.regionTask,extra.baseGrid,actor])).rows[0].id;
  extra.store=(await db.query(`INSERT INTO bd_store_task(grid_task_id,store_id,snapshot,status,result,revision,updated_at) VALUES($1,$2,'{"name":"范围药店 第二片区"}','pending','{}',0,now()) RETURNING id`,[extra.grid,f.prefix+'-extra'])).rows[0].id;
  const defaultResult=await service.visits(actor,grid,{q:'范围药店'});assert.equal(defaultResult.total,3,'default remains current grid');const oldWriter=(await db.query('SELECT search_scope,scope_grid_task_ids,group_grid_task_id,group_region_task_id FROM bd_store_search_log WHERE id=$1',[defaultResult.searchLogId])).rows[0];assert.deepEqual(oldWriter,{search_scope:'grid',scope_grid_task_ids:[],group_grid_task_id:null,group_region_task_id:null});
  const query={q:'范围药店',scope:'batch'};
  const client=request.agent(app.getHttpServer());assert.equal((await client.post('/api/auth/login').send({username:f.codes[2],password:'fixture-pass'})).status,201);const http=await client.get('/api/batch-work/grids/'+grid+'/visits').query(query);assert.equal(http.status,200);assert.equal(http.body.total,28);assert.equal(http.body.regionGroups.length,2);assert.equal((await client.get('/api/batch-work/grids/'+grid+'/visits').query({q:'',scope:'batch'})).status,400);
  const page1=await service.visits(actor,grid,query),page2=await service.visits(actor,grid,{...query,page:2});
  assert.equal(page1.searchScope,'batch');assert.equal(page1.total,28);assert.equal(page1.scopeMatchedTotal,28);assert.equal(page1.items.length,20);assert.equal(page2.items.length,8);assert.equal(new Set([...page1.items,...page2.items].map((r:any)=>r.id)).size,28);
  assert.deepEqual(page1.gridGroups.map((g:any)=>[g.id,g.count]),[[other,24],[grid,3],[extra.grid,1]]);
  assert.deepEqual(page1.regionGroups.map((g:any)=>[g.id,g.count]),[[f.regions[0],27],[extra.regionTask,1]]);
  assert.deepEqual(page2.gridGroups,page1.gridGroups,'groups reflect all matching rows, not just a loaded page');
  const grouped=await service.visits(actor,grid,{...query,groupGrid:other});assert.equal(grouped.total,24);assert.equal(grouped.scopeMatchedTotal,28);assert.ok(grouped.items.every((r:any)=>r.gridTaskId===other));assert.deepEqual(grouped.gridGroups,page1.gridGroups);
  const region=await service.visits(actor,grid,{...query,groupRegion:extra.regionTask});assert.equal(region.total,1);assert.equal(region.items[0].id,extra.store);assert.equal(region.items[0].regionName,'第二片区');
  const log=(await db.query('SELECT * FROM bd_store_search_log WHERE id=$1',[region.searchLogId])).rows[0];assert.equal(log.search_scope,'batch');assert.equal(log.group_region_task_id,extra.regionTask);assert.equal(log.group_grid_task_id,null);assert.ok(log.scope_grid_task_ids.includes(extra.grid));assert.equal(log.strategy_version,'batch-terms-nfkc-v2');
  await service.searchFeedback(actor,{kind:'opened',searchLogId:region.searchLogId,storeTaskId:extra.store,eventId:crypto.randomUUID()});
  await db.query('UPDATE bd_grid_task SET assignee_id=$2 WHERE id=$1',[extra.grid,f.users[3]]);
  await assert.rejects(()=>service.searchFeedback(actor,{kind:'confirmed',searchLogId:region.searchLogId,storeTaskId:extra.store,eventId:crypto.randomUUID()}),{code:'FORBIDDEN'});
  const after=await service.visits(actor,grid,query);assert.equal(after.total,27);assert.equal(after.gridGroups.some((g:any)=>g.id===extra.grid),false);
  await assert.rejects(()=>service.visits(actor,grid,{...query,groupGrid:extra.grid}),{code:'FORBIDDEN'});
  await assert.rejects(()=>service.visits(actor,grid,{...query,groupRegion:extra.regionTask}),{code:'FORBIDDEN'});
  await assert.rejects(()=>service.visits(f.users[4],grid,query),{code:'FORBIDDEN'});
  await assert.rejects(()=>service.visits(actor,grid,{q:'',scope:'batch'}),{code:'VALIDATION'});
  await assert.rejects(()=>service.visits(actor,grid,{...query,groupGrid:grid,groupRegion:f.regions[0]}),{code:'VALIDATION'});
  assert.equal((await service.visits(actor,grid,{...query,state:'completed'})).total,0);
 }finally{
  if(f){await db.query('DELETE FROM bd_store_search_log WHERE main_task_id=ANY($1::int[])',[f.mains]);if(extra.store)await db.query('DELETE FROM bd_store_task WHERE id=$1',[extra.store]);if(extra.grid)await db.query('DELETE FROM bd_grid_task WHERE id=$1',[extra.grid]);if(extra.regionTask)await db.query('DELETE FROM bd_region_task WHERE id=$1',[extra.regionTask]);if(extra.baseGrid)await db.query('DELETE FROM bd_work_grid WHERE id=$1',[extra.baseGrid]);if(extra.region)await db.query('DELETE FROM bd_region WHERE id=$1',[extra.region]);await clearBatchWork(db,f);}await app.close();
 }
});
