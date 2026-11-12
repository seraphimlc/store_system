import {spawnLegacyPython} from '../../../scripts/legacy-python.mjs';
import 'reflect-metadata';
import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import {hash} from '@node-rs/argon2';
import {createApp} from '../src/main';
import {Database} from '../src/database';
import {BatchWorkService} from '../src/batch-work';
import {seedBatchWork,clearBatchWork} from '../../../scripts/batch-work-fixture.mjs';
const url=process.env.NEXT_TEST_DATABASE_URL;
test('batch history uses current scope, JST half-open dates and distinct lifetime first completions',{skip:!url},async()=>{
 assert.match(new URL(url!).pathname,/_refactor_p1$/);
 const app=await createApp({databaseUrl:url!,sessionSecret:'batch-events-at-least-40-character-secret-value',cookieSecure:false,port:0,host:'127.0.0.1'});
 const db=app.get(Database),service=app.get(BatchWorkService);let f:any;
 try{
  f=await seedBatchWork(db,'P5-EVENT-TEST',await hash('fixture-events'));
  const main=f.mains[0],store=f.stores[0][0][0],worker=f.users[2],other=f.users[3];
  await service.complete(worker,store,{revision:0,requestId:randomUUID(),status:'normal',note:'first'});
  await service.complete(worker,store,{revision:1,requestId:randomUUID(),status:'closed',note:'revised'});
  const first=(await db.query("SELECT id FROM bd_task_event WHERE main_task_id=$1 AND event_type='complete_store_task'",[main])).rows[0].id;
  await db.query("UPDATE bd_task_event SET created_at='2026-11-01 15:00:00' WHERE id=$1",[first]);
  // An older actor completion in the same task must not become another first completion after actor filtering.
  await db.query(`INSERT INTO bd_task_event(main_task_id,main_label,region_task_id,region_label,grid_task_id,grid_label,store_task_id,store_label,store_source_id,event_type,actor_id,actor_label,actor_role,team_id,team_label,old_value,new_value,created_at)
    SELECT main_task_id,main_label,region_task_id,region_label,grid_task_id,grid_label,store_task_id,store_label,store_source_id,event_type,$2,'Other','staff',team_id,team_label,old_value,new_value,'2026-11-02 15:00:00' FROM bd_task_event WHERE id=$1`,[first,other]);
  const oracle=`import json\nfrom app.db import SessionLocal\nfrom app.models import User\nfrom app.services.bd_hierarchical_tasks import task_events\ns=SessionLocal()\ntry:\n out=[]\n for kw in ({},{'dfrom':'2026-11-02','dto':'2026-11-03'},{'actor_id':${other}},{'keyword':'no-match','event_type':'update_store_result'}):\n  result=task_events(s,s.get(User,${worker}),${main},**kw)\n  m=result[5]\n  out.append({'total':result[2],'ids':[r.id for r in result[1]],'metrics':[m['first_completed'],m['completion_events'],m['current_total'],m['current_done']]})\n print(json.dumps(out))\nfinally:\n s.close()`;
  const legacy=spawnLegacyPython(['-c',oracle],{encoding:'utf8',env:{...process.env,DATABASE_URL:url!.replace('postgresql://','postgresql+psycopg2://')}});
  assert.equal(legacy.status,0,legacy.stderr);
  const expected=JSON.parse(legacy.stdout);
  for(const [index,query] of [{},{from:'2026-11-02',to:'2026-11-03'},{actorId:other},{keyword:'no-match',eventType:'update_store_result'}].entries()){
   const actual=await service.events(worker,main,query);
   assert.deepEqual({total:actual.total,ids:actual.items.map((row:any)=>row.id),metrics:[actual.metrics.firstCompleted,actual.metrics.completionEvents,actual.metrics.currentTotal,actual.metrics.currentDone].map(Number)},expected[index]);
  }
  let value=await service.events(worker,main,{from:'2026-11-02',to:'2026-11-03'});
  assert.equal(Number(value.metrics.firstCompleted),1);assert.equal(Number(value.metrics.completionEvents),1);assert.equal(value.metrics.currentDone,1);assert.equal(value.metrics.currentTotal,4);
  value=await service.events(worker,main,{actorId:other});assert.equal(Number(value.metrics.firstCompleted),0);assert.equal(Number(value.metrics.completionEvents),1);assert.equal(value.metrics.currentTotal,0);
  value=await service.events(worker,main,{keyword:'no-match',eventType:'update_store_result'});assert.equal(value.total,0);assert.equal(Number(value.metrics.firstCompleted),1);assert.equal(Number(value.metrics.completionEvents),2);
  value=await service.events(worker,main,{limit:1});assert.equal(value.items.length,1);assert.ok(value.total>1);assert.ok(value.eventTypes.includes('update_store_result'));
  await db.query('UPDATE bd_work_grid SET stations=$1 WHERE id=$2',[JSON.stringify(['SyntheticStation']),f.baseGrids[0]]);
  value=await service.events(worker,main,{keyword:'SyntheticStation'});assert.ok(value.items.some((row:any)=>row.id===first));
  await db.query('UPDATE bd_grid_task SET assignee_id=$1 WHERE id=$2',[other,f.grids[0][0]]);
  value=await service.events(worker,main,{});assert.equal(Number(value.metrics.firstCompleted),0);assert.equal(value.metrics.currentTotal,3);assert.ok(!value.items.some((row:any)=>row.grid_label.endsWith('grid 0')));assert.ok(!value.actors.some((row:any)=>row.label==='Other'));
  value=await service.events(f.users[1],main,{});assert.equal(Number(value.metrics.firstCompleted),1);assert.equal(value.metrics.currentTotal,4);
  value=await service.events(f.users[0],main,{gridId:f.grids[1][0]});assert.equal(value.total,0);assert.equal(value.metrics.currentTotal,0);
  await assert.rejects(()=>service.events(worker,main,{from:'2026-02-30'}),{code:'VALIDATION'});
  await assert.rejects(()=>service.events(worker,main,{from:'2026-11-03',to:'2026-11-02'}),{code:'VALIDATION'});
  await db.query('UPDATE bd_team_member SET end_date=current_date WHERE person_code=$1',[f.codes[2]]);
  await assert.rejects(()=>service.events(worker,main,{}),{code:'FORBIDDEN'});
 }finally{if(f)await clearBatchWork(db,f);await app.close();}
});
