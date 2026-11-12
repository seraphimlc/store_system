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
test('grid enable matches legacy guards, preserves geometry/mappings and batch snapshots, locks revisions and rolls back audit failure',{skip:!url},async()=>{
 assert.match(new URL(url!).pathname,/_refactor_p1$/);
 const app=await createApp({databaseUrl:url!,sessionSecret:'grid-enable-test-at-least-40-character-secret',cookieSecure:false,host:'127.0.0.1',port:0}),db=app.get(Database),service=app.get(ZoneAssetsService);let f:any;
 try{
  f=await seedBatchWork(db,'P5-GE-'+Date.now().toString(36),await hash('fixture-grid-enable'));
  const admin=f.users[0],worker=f.users[2],grid=f.baseGrids[0],second=f.baseGrids[1],merchant=f.prefix+'-merchant';
  await db.query("UPDATE bd_work_grid SET zone_id=NULL,state='disabled',task_state='unvisited',stations=$1 WHERE id=ANY($2::int[])",[JSON.stringify([f.prefix+' station']),[grid,second]]);
  await db.query("INSERT INTO bd_work_store(id,name,address,lat,lon,status,revision,updated_at) VALUES($1,'synthetic','',35,139,'normal',0,now())",[merchant]);
  await db.query('INSERT INTO bd_grid_store(release_id,grid_id,source_id) VALUES($1,$2,$3)',[f.release,grid,merchant]);
  const before=(await db.query('SELECT geometry,area_km2,store_count,zone_id FROM bd_work_grid WHERE id=$1',[grid])).rows[0];
  const batches=(await db.query('SELECT id,snapshot FROM bd_grid_task WHERE grid_id=ANY($1::int[]) ORDER BY id',[f.baseGrids])).rows;
  const mappings=(await db.query('SELECT * FROM bd_grid_store WHERE release_id=$1',[f.release])).rows;
  assert.equal((await service.grids(admin,{q:f.prefix})).items.find((g:any)=>g.id===grid).convertible,true);
  await assert.rejects(()=>service.enableGrid(worker,grid,{revision:0}),{code:'FORBIDDEN'});
  await assert.rejects(()=>service.grids(worker,{}),{code:'FORBIDDEN'});
  await assert.rejects(()=>service.enableGrid(admin,grid,{revision:1}),{code:'REVISION_CONFLICT'});
  for(const [column,value] of [['zone_id',f.region],['assignee_id',worker],['progress',1],['task_state','visited']] as const){
   await db.query(`UPDATE bd_work_grid SET ${column}=$1 WHERE id=$2`,[value,grid]);
   assert.equal((await service.grids(admin,{q:f.prefix})).items.find((g:any)=>g.id===grid).convertible,false);
   await assert.rejects(()=>service.enableGrid(admin,grid,{revision:0}),{code:'GRID_PROTECTED'});
   await db.query(`UPDATE bd_work_grid SET ${column}=$1 WHERE id=$2`,[column==='progress'?0:column==='task_state'?'unvisited':null,grid]);
  }
  await db.query("INSERT INTO bd_store_visit_log(request_id,store_id,grid_id,actor_id,operation,before,after,note,created_at) VALUES($1,$2,$3,$4,'visit','{}','{}','',now())",[randomUUID(),merchant,grid,admin]);
  await assert.rejects(()=>service.enableGrid(admin,grid,{revision:0}),{code:'GRID_PROTECTED'});
  assert.equal((await service.grids(admin,{q:f.prefix})).items.find((g:any)=>g.id===grid).convertible,false);
  await db.query('DELETE FROM bd_store_visit_log WHERE grid_id=$1',[grid]);
  const connect=db.pool.connect.bind(db.pool),mutable=db.pool as unknown as {connect:()=>Promise<unknown>};
  mutable.connect=async()=>{const client=await connect(),query=client.query.bind(client),release=client.release.bind(client);client.query=((sql:any,...args:any[])=>{if(typeof sql==='string'&&sql.includes('INSERT INTO bd_log'))throw Error('synthetic grid audit failure');return (query as any)(sql,...args);}) as any;client.release=()=>{client.query=query;release();};return client;};
  try{await assert.rejects(()=>service.enableGrid(admin,grid,{revision:0}),/synthetic grid audit failure/);}finally{mutable.connect=connect;}
  assert.deepEqual((await db.query('SELECT state,revision FROM bd_work_grid WHERE id=$1',[grid])).rows[0],{state:'disabled',revision:0});
  const agent=request.agent(app.getHttpServer());const login=(await agent.post('/api/auth/login').send({username:f.codes[0],password:'fixture-grid-enable'}).expect(201)).body;
  await agent.post(`/api/zone-assets/grids/${grid}/enable`).send({revision:0}).expect(403);
  await agent.get('/api/zone-assets/grids?q='+f.prefix).expect(200);
  const concurrent=await Promise.allSettled([service.enableGrid(admin,grid,{revision:0}),service.enableGrid(admin,grid,{revision:0})]);assert.equal(concurrent.filter(r=>r.status==='fulfilled').length,1);
  await agent.post(`/api/zone-assets/grids/${grid}/enable`).set('X-CSRF-Token',login.csrfToken).send({revision:1}).expect(409);
  assert.equal((await service.grids(admin,{q:f.prefix,state:'disabled'})).items.some((g:any)=>g.id===grid),false);
  const oracle=`from app.db import SessionLocal\nfrom app.models import User\nfrom app.services.bd_grid_regions import enable_grid\ns=SessionLocal()\ntry:\n enable_grid(s,s.get(User,${admin}),${second},0)\n s.commit()\nfinally:\n s.close()`;
  const legacy=spawnLegacyPython(['-c',oracle],{encoding:'utf8',env:{...process.env,DATABASE_URL:url!.replace(/^postgres(?:ql)?:/,'postgresql+psycopg2:')}});assert.equal(legacy.status,0,legacy.stderr);
  const states=(await db.query('SELECT state,revision FROM bd_work_grid WHERE id=ANY($1::int[]) ORDER BY id',[[grid,second]])).rows;assert.deepEqual(states,[{state:'ready',revision:1},{state:'ready',revision:1}]);
  const logs=(await db.query("SELECT old_value,new_value FROM bd_log WHERE domain='grid' AND action='enable_grid' AND ref_id=ANY($1::int[]) ORDER BY ref_id",[[grid,second]])).rows;assert.equal(logs.length,2);assert.deepEqual(logs.map(r=>({before:JSON.parse(r.old_value),after:JSON.parse(r.new_value)})),[{before:{state:'disabled',revision:0},after:{state:'ready',revision:1}},{before:{state:'disabled',revision:0},after:{state:'ready',revision:1}}]);
  assert.deepEqual((await db.query('SELECT geometry,area_km2,store_count,zone_id FROM bd_work_grid WHERE id=$1',[grid])).rows[0],before);
  assert.deepEqual((await db.query('SELECT * FROM bd_grid_store WHERE release_id=$1',[f.release])).rows,mappings);
  assert.deepEqual((await db.query('SELECT id,snapshot FROM bd_grid_task WHERE grid_id=ANY($1::int[]) ORDER BY id',[f.baseGrids])).rows,batches);
 }finally{if(f){await db.query('DELETE FROM bd_store_visit_log WHERE grid_id=ANY($1::int[])',[f.baseGrids]);await db.query('DELETE FROM bd_grid_store WHERE release_id=$1',[f.release]);await db.query('DELETE FROM bd_work_store WHERE id=$1',[f.prefix+'-merchant']);await db.query("DELETE FROM bd_log WHERE domain='grid' AND ref_id=ANY($1::int[]) AND actor=$2",[f.baseGrids,f.codes[0]]);await clearBatchWork(db,f);}await app.close();}
});
