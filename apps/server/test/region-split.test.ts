import {spawnLegacyPython} from '../../../scripts/legacy-python.mjs';
import 'reflect-metadata';
import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import {hash} from '@node-rs/argon2';
import request from '../node_modules/supertest';
import {createApp} from '../src/main';
import {Database} from '../src/database';
import {ZoneAssetsService} from '../src/zone-assets';
import {seedBatchWork,clearBatchWork} from '../../../scripts/batch-work-fixture.mjs';
const url=process.env.NEXT_TEST_DATABASE_URL;
test('region split previews without writes, matches legacy release/archive semantics and preserves batch snapshots with atomic protection',{skip:!url},async()=>{
 assert.match(new URL(url!).pathname,/_refactor_p1$/);
 const app=await createApp({databaseUrl:url!,sessionSecret:'region-split-at-least-40-character-secret-value',cookieSecure:false,port:0,host:'127.0.0.1'}),db=app.get(Database),service=app.get(ZoneAssetsService);let f:any;
 try{
  f=await seedBatchWork(db,'P5-RS-'+Date.now().toString(36),await hash('fixture-region-split'));const id=f.region,admin=f.users[0],worker=f.users[2],grid=f.baseGrids[0],merchant=f.prefix+'-merchant';
  await db.query("UPDATE bd_work_grid SET task_state='unvisited' WHERE id=ANY($1::int[])",[f.baseGrids]);
  await db.query("UPDATE bd_work_grid SET state='disabled' WHERE id=$1",[f.baseGrids[2]]); // Old split releases disabled members as well.
  await db.query("INSERT INTO bd_work_store(id,name,address,lat,lon,status,revision,updated_at) VALUES($1,'fixture','',35,139,'normal',0,now())",[merchant]);
  await db.query('INSERT INTO bd_grid_store(release_id,grid_id,source_id) VALUES($1,$2,$3)',[f.release,grid,merchant]);
  const gridData=async()=> (await db.query('SELECT id,zone_id,state,revision,geometry,area_km2,store_count FROM bd_work_grid WHERE id=ANY($1::int[]) ORDER BY id',[f.baseGrids])).rows;
  const snapshot=(await db.query('SELECT id,snapshot,revision FROM bd_grid_task WHERE grid_id=ANY($1::int[]) ORDER BY id',[f.baseGrids])).rows;
  const before=await gridData(),mapping=(await db.query('SELECT * FROM bd_grid_store WHERE release_id=$1',[f.release])).rows;
  await assert.rejects(()=>service.splitPreview(worker,id,{revision:0}),{code:'FORBIDDEN'});
  await assert.rejects(()=>service.splitPreview(admin,id,{revision:1}),{code:'REVISION_CONFLICT'});
  await assert.rejects(()=>service.splitPreview(admin,2147483647,{revision:0}),{code:'NOT_FOUND'});
  for(const field of ['team_id','leader_id'] as const){await db.query(`UPDATE bd_region SET ${field}=$1 WHERE id=$2`,[field==='team_id'?f.team:worker,id]);await assert.rejects(()=>service.splitPreview(admin,id,{revision:0}),{code:'REGION_ASSIGNED'});await db.query(`UPDATE bd_region SET ${field}=NULL WHERE id=$1`,[id]);}
  for(const [field,value] of [['assignee_id',worker],['progress',1],['task_state','visited']] as const){await db.query(`UPDATE bd_work_grid SET ${field}=$1 WHERE id=$2`,[value,grid]);await assert.rejects(()=>service.splitPreview(admin,id,{revision:0}),{code:'GRID_PROTECTED'});await db.query(`UPDATE bd_work_grid SET ${field}=$1 WHERE id=$2`,[field==='progress'?0:field==='task_state'?'unvisited':null,grid]);}
  await db.query("INSERT INTO bd_store_visit_log(request_id,store_id,grid_id,actor_id,operation,before,after,note,created_at) VALUES($1,$2,$3,$4,'visit','{}','{}','',now())",[randomUUID(),merchant,grid,worker]);await assert.rejects(()=>service.splitPreview(admin,id,{revision:0}),{code:'GRID_PROTECTED'});await db.query('DELETE FROM bd_store_visit_log WHERE grid_id=$1',[grid]);
  let preview=await service.splitPreview(admin,id,{revision:0});assert.equal(preview.gridCount,3);assert.equal(preview.storeCount,1);assert.deepEqual(await gridData(),before);
  assert.equal((await db.query("SELECT count(*)::int AS n FROM bd_log WHERE domain='zone' AND action='split_zone' AND ref_id=$1",[id])).rows[0].n,0);
  const body=()=>({revision:preview.revision,previewToken:preview.previewToken,confirm:'SPLIT_REGION'});
  await assert.rejects(()=>service.split(admin,id,{...body(),confirm:'wrong'}),{code:'VALIDATION'});
  await db.query('UPDATE bd_work_grid SET name=name||$1 WHERE id=$2',[' updated',grid]);await assert.rejects(()=>service.split(admin,id,body()),{code:'PREVIEW_CHANGED'});preview=await service.splitPreview(admin,id,{revision:0});
  const connect=db.pool.connect.bind(db.pool),mutable=db.pool as unknown as {connect:()=>Promise<unknown>};
  mutable.connect=async()=>{const client=await connect(),query=client.query.bind(client),release=client.release.bind(client);client.query=((sql:any,...args:any[])=>{if(typeof sql==='string'&&sql.includes('INSERT INTO bd_log'))throw Error('synthetic split audit failure');return(query as any)(sql,...args);}) as any;client.release=()=>{client.query=query;release();};return client;};
  try{await assert.rejects(()=>service.split(admin,id,body()),/synthetic split audit failure/);}finally{mutable.connect=connect;}
  assert.deepEqual(await gridData(),before);
  const oracle=`import json\nfrom app.db import SessionLocal\nfrom app.models import User,BdRegion,BdWorkGrid,BdLog\nfrom app.services.bd_grid_regions import split_zone\ns=SessionLocal()\ntry:\n r=split_zone(s,s.get(User,${admin}),${id},0)\n s.flush()\n g=s.query(BdWorkGrid).filter(BdWorkGrid.id.in_(${JSON.stringify(f.baseGrids)})).order_by(BdWorkGrid.id).all()\n log=s.query(BdLog).filter_by(domain='zone',action='split_zone',ref_id=${id}).one()\n print(json.dumps({'region':{'state':r.state,'revision':r.revision,'store_count':r.store_count,'workload':r.workload},'grids':[{'id':v.id,'zone_id':v.zone_id,'state':v.state,'revision':v.revision} for v in g],'log':{'old':json.loads(log.old_value),'new':json.loads(log.new_value)}}))\nfinally:\n s.rollback()\n s.close()`;
  const legacy=spawnLegacyPython(['-c',oracle],{encoding:'utf8',env:{...process.env,DATABASE_URL:url!.replace(/^postgres(?:ql)?:/,'postgresql+psycopg2:')}});assert.equal(legacy.status,0,legacy.stderr);const expected=JSON.parse(legacy.stdout);
  const agent=request.agent(app.getHttpServer()),staff=request.agent(app.getHttpServer());const login=(await agent.post('/api/auth/login').send({username:f.codes[0],password:'fixture-region-split'}).expect(201)).body;
  const staffLogin=(await staff.post('/api/auth/login').send({username:f.codes[2],password:'fixture-region-split'}).expect(201)).body;
  await request(app.getHttpServer()).post(`/api/zone-assets/regions/${id}/split-preview`).send({revision:0}).expect(401);
  await agent.post(`/api/zone-assets/regions/${id}/split-preview`).send({revision:0}).expect(403);
  await staff.post(`/api/zone-assets/regions/${id}/split`).set('X-CSRF-Token',staffLogin.csrfToken).send(body()).expect(403);
  const apiPreview=(await agent.post(`/api/zone-assets/regions/${id}/split-preview`).set('X-CSRF-Token',login.csrfToken).send({revision:0}).expect(201)).body;assert.equal(apiPreview.previewToken,preview.previewToken);
  const races=await Promise.allSettled([service.split(admin,id,body()),service.split(admin,id,body())]);assert.equal(races.filter(r=>r.status==='fulfilled').length,1);
  const region=(await db.query('SELECT state,revision,store_count,workload FROM bd_region WHERE id=$1',[id])).rows[0],grids=(await gridData()).map(({id,zone_id,state,revision})=>({id,zone_id,state,revision})),log=(await db.query("SELECT old_value,new_value FROM bd_log WHERE domain='zone' AND action='split_zone' AND ref_id=$1",[id])).rows;
  assert.equal(log.length,1);assert.deepEqual({region,grids,log:{old:JSON.parse(log[0].old_value),new:JSON.parse(log[0].new_value)}},expected);
  assert.deepEqual((await gridData()).map(g=>({...g,zone_id:id,revision:g.revision-1})),before);
  assert.deepEqual((await db.query('SELECT * FROM bd_grid_store WHERE release_id=$1',[f.release])).rows,mapping);
  assert.deepEqual((await db.query('SELECT id,snapshot,revision FROM bd_grid_task WHERE grid_id=ANY($1::int[]) ORDER BY id',[f.baseGrids])).rows,snapshot);
  await assert.rejects(()=>service.splitPreview(admin,id,{revision:1}),{code:'REGION_ARCHIVED'});
  assert.equal((await service.catalog(admin,{q:f.prefix})).items.some((r:any)=>r.id===id),false);
  assert.equal((await service.grids(admin,{q:f.prefix})).items.find((g:any)=>g.id===f.baseGrids[2]).convertible,true);
 }finally{if(f){await db.query('DELETE FROM bd_store_visit_log WHERE grid_id=ANY($1::int[])',[f.baseGrids]);await db.query('DELETE FROM bd_grid_store WHERE release_id=$1',[f.release]);await db.query('DELETE FROM bd_work_store WHERE id=$1',[f.prefix+'-merchant']);await db.query("DELETE FROM bd_log WHERE domain='zone' AND ref_id=$1 AND actor=$2",[f.region,f.codes[0]]);await clearBatchWork(db,f);}await app.close();}
});
