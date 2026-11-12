import {spawnLegacyPython} from '../../../scripts/legacy-python.mjs';
import 'reflect-metadata';
import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {hash} from '@node-rs/argon2';
import request from '../node_modules/supertest';
import {createApp} from '../src/main';
import {Database} from '../src/database';
import {BatchWorkService} from '../src/batch-work';
import {seedBatchWork,clearBatchWork} from '../../../scripts/batch-work-fixture.mjs';
const url=process.env.NEXT_TEST_DATABASE_URL;
test('batch disabling is admin-only, revision-locked, atomic with both logs and preserves read-only history',{skip:!url},async()=>{
 assert.match(new URL(url!).pathname,/_refactor_p1$/);
 const app=await createApp({databaseUrl:url!,sessionSecret:'batch-disable-at-least-40-character-secret-value',cookieSecure:false,port:0,host:'127.0.0.1'});
 const db=app.get(Database),service=app.get(BatchWorkService);let f:any;
 try{
  f=await seedBatchWork(db,'P5-DISABLE-TEST',await hash('fixture-disable'));
  const main=f.mains[0],other=f.mains[1],adminId=f.users[0],worker=f.users[2];
  const snapshot=async()=> (await db.query('SELECT row_to_json(s) AS value FROM bd_store_task s WHERE grid_task_id=ANY($1::int[]) ORDER BY id',[f.grids.flat()])).rows;
  const before=await snapshot();
  const admin=request.agent(app.getHttpServer()),staff=request.agent(app.getHttpServer());
  const login=(await admin.post('/api/auth/login').send({username:f.codes[0],password:'fixture-disable'}).expect(201)).body;
  const staffLogin=(await staff.post('/api/auth/login').send({username:f.codes[2],password:'fixture-disable'}).expect(201)).body;
  await request(app.getHttpServer()).post(`/api/batch-work/batches/${main}/disable`).send({revision:0}).expect(401);
  await admin.post(`/api/batch-work/batches/${main}/disable`).send({revision:0}).expect(403);
  await staff.post(`/api/batch-work/batches/${main}/disable`).set('X-CSRF-Token',staffLogin.csrfToken).send({revision:0}).expect(403);
  await assert.rejects(()=>service.disable(f.users[1],main,{revision:0}),{code:'FORBIDDEN'});
  await assert.rejects(()=>service.disable(adminId,main,{revision:1}),{code:'REVISION_CONFLICT'});
  await assert.rejects(()=>service.disable(adminId,2147483647,{revision:0}),{code:'NOT_FOUND'});
  const connect=db.pool.connect.bind(db.pool),mutable=db.pool as unknown as{connect:()=>Promise<unknown>};
  mutable.connect=async()=>{const client=await connect(),query=client.query.bind(client);client.query=((sql:any,...args:any[])=>{if(typeof sql==='string'&&sql.includes('INSERT INTO bd_task_event'))throw Error('synthetic audit failure');return(query as any)(sql,...args);}) as any;const release=client.release.bind(client);client.release=()=>{client.query=query;release();};return client;};
  try{await assert.rejects(()=>service.disable(adminId,main,{revision:0}),/synthetic audit failure/);}finally{mutable.connect=connect;}
  assert.deepEqual((await db.query('SELECT status,revision FROM bd_main_task WHERE id=$1',[main])).rows[0],{status:'active',revision:0});
  assert.equal((await db.query("SELECT count(*)::int AS n FROM bd_log WHERE ref_id=$1 AND action='disable_batch'",[main])).rows[0].n,0);
  const results=await Promise.allSettled([service.disable(adminId,main,{revision:0}),service.disable(adminId,main,{revision:0})]);
  assert.equal(results.filter(row=>row.status==='fulfilled').length,1);assert.equal((results.find(row=>row.status==='rejected') as PromiseRejectedResult).reason.code,'REVISION_CONFLICT');
  assert.deepEqual(await service.disable(adminId,main,{revision:1}),{id:main,revision:1,reused:true});
  await assert.rejects(()=>service.disable(adminId,main,{revision:0}),{code:'REVISION_CONFLICT'});
  await assert.rejects(()=>service.complete(worker,f.stores[0][0][0],{revision:0,requestId:randomUUID(),status:'normal',note:'blocked'}),{code:'BATCH_DISABLED'});
  await assert.rejects(()=>service.assignRegion(adminId,f.regions[0],{revision:0,teamId:f.team}),{code:'BATCH_CLOSED'});
  await assert.rejects(()=>service.assignGrid(f.users[1],f.grids[0][0],{revision:0,assigneeId:worker}),{code:'BATCH_DISABLED'});
  assert.equal((await service.stores(worker,f.grids[0][0],{})).canExecute,false);
  assert.equal((await service.tree(worker,main)).main.status,'disabled');
  const history=await service.events(worker,main,{eventType:'disable_batch'});assert.equal(history.total,1);assert.deepEqual(history.items[0].old_value,{status:'active'});assert.deepEqual(history.items[0].new_value,{status:'disabled'});
  // Run the old service against the independent second fixture batch, then compare state and event semantics.
  const oracle=`from app.db import SessionLocal\nfrom app.models import User\nfrom app.services.bd_hierarchical_tasks import disable_batch\ns=SessionLocal()\ntry:\n disable_batch(s,s.get(User,${adminId}),${other},0)\n s.commit()\nfinally:\n s.close()`;
  const legacy=spawnLegacyPython(['-c',oracle],{encoding:'utf8',env:{...process.env,DATABASE_URL:url!.replace(/^postgres(?:ql)?:/,'postgresql+psycopg2:')}});assert.equal(legacy.status,0,legacy.stderr);
  const states=(await db.query('SELECT status,revision,progress FROM bd_main_task WHERE id=ANY($1::int[]) ORDER BY id',[f.mains])).rows;assert.deepEqual(states[0],states[1]);
  const logs=(await db.query("SELECT event_type,actor_id,actor_role,old_value,new_value FROM bd_task_event WHERE main_task_id=ANY($1::int[]) AND event_type='disable_batch' ORDER BY main_task_id",[f.mains])).rows;assert.equal(logs.length,2);assert.deepEqual(logs[0],logs[1]);
  assert.deepEqual(await snapshot(),before);
  await db.query("UPDATE bd_main_task SET status='completed',progress=100 WHERE id=$1",[other]);
  await admin.post(`/api/batch-work/batches/${other}/disable`).set('X-CSRF-Token',login.csrfToken).send({revision:1}).expect(201);
  assert.equal((await service.tree(adminId,other)).main.progress,100);
 }finally{if(f)await clearBatchWork(db,f);await app.close();}
});
