import {spawnLegacyPython} from '../../../scripts/legacy-python.mjs';
import 'reflect-metadata';
import test from 'node:test';
import assert from 'node:assert/strict';
import {hash} from '@node-rs/argon2';
import {spawnSync} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import request from '../node_modules/supertest';
import {createApp} from '../src/main';
import {Database} from '../src/database';
import {ZoneAssetsService} from '../src/zone-assets';
import {seedBatchWork,clearBatchWork} from '../../../scripts/batch-work-fixture.mjs';
const url=process.env.NEXT_TEST_DATABASE_URL;
test('基础片区目录、旧巡店留痕统计和改名与旧服务口径对齐',{skip:!url},async()=>{
 assert.match(new URL(url!).pathname,/_refactor_p1$/);
 const app=await createApp({databaseUrl:url!,sessionSecret:'zone-assets-at-least-40-character-secret-value',cookieSecure:false,port:0,host:'127.0.0.1'}),db=app.get(Database),service=app.get(ZoneAssetsService);let f:any;
 try{
  const prefix='P5-ZONE-ASSETS-'+Date.now();f=await seedBatchWork(db,prefix,await hash('fixture-assets'));const region=f.region,grid=f.baseGrids[0],admin=f.users[0],worker=f.users[2];
  await db.query("UPDATE bd_zone_release SET source=$1 WHERE id=$2",[JSON.stringify({region_code:'kanagawa'}),f.release]);
  await db.query("UPDATE bd_work_grid SET name=$1,stations=$2,keywords=$3 WHERE id=$4",[f.prefix+' 横滨网格',JSON.stringify([f.prefix+' 横滨']),JSON.stringify([f.prefix+' 駅']),grid]);
  const assetStore=f.prefix+'-asset-store';await db.query("INSERT INTO bd_work_store(id,name,address,lat,lon,status,revision,updated_at) VALUES($1,'资产店','',35,139,'normal',0,now())",[assetStore]);
  await db.query("INSERT INTO bd_grid_store(grid_id,source_id,release_id) VALUES($1,$2,$3)",[grid,assetStore,f.release]);
  const visitGrid=f.baseGrids[0];await db.query("INSERT INTO bd_work_store(id,name,address,lat,lon,status,revision,updated_at) VALUES($1,'visited','',35,139,'normal',0,now()) ON CONFLICT DO NOTHING",[f.prefix+'-source-0-0']);
  await db.query("INSERT INTO bd_grid_store(grid_id,source_id,release_id) VALUES($1,$2,$3) ON CONFLICT DO NOTHING",[visitGrid,f.prefix+'-source-0-0',f.release]);
  await db.query("INSERT INTO bd_store_visit_log(request_id,store_id,grid_id,actor_id,operation,before,after,note,created_at) VALUES($1,$2,$3,$4,'complete','{}','{}','',now())",[randomUUID(),f.prefix+'-source-0-0',visitGrid,worker]);
  const catalog=await service.catalog(admin,{q:f.prefix,region:'kanagawa',page:1});assert.equal(catalog.total,1);assert.equal(catalog.items[0].id,region);assert.equal(catalog.items[0].grids,3);
  assert.equal((await service.catalog(admin,{q:f.prefix,region:'kanagawa',page:1})).total,1);assert.equal((await service.catalog(worker,{q:f.prefix,region:'kanagawa',page:1}).catch((e:any)=>e.code)), 'FORBIDDEN');
  const detail=await service.detail(admin,region);assert.equal(detail.stats.total,2);assert.equal(detail.stats.visited,1);assert.equal(detail.grids.find((row:any)=>row.id===visitGrid).progress,50);assert.equal(detail.stats.progress,16.7);assert.equal(detail.stats.statuses.normal,2);
  const oracle=`import json\nfrom app.db import SessionLocal\nfrom app.models import User\nfrom app.services.bd_zone_operations import management_detail\ns=SessionLocal()\ntry:\n d=management_detail(s,s.get(User,${admin}),${region})\n print(json.dumps({"total":d["merchant_total"],"visited":d["visited"],"progress":d["progress"],"statuses":d["status_counts"]}))\nfinally:\n s.close()`;
  const legacy=spawnLegacyPython(['-c',oracle],{encoding:'utf8',env:{...process.env,DATABASE_URL:url!.replace(/^postgres(?:ql)?:/,'postgresql+psycopg2:')}});assert.equal(legacy.status,0,legacy.stderr);assert.deepEqual(detail.stats,JSON.parse(legacy.stdout));
  const before=(await db.query('SELECT snapshot FROM bd_region_task WHERE region_id=$1',[region])).rows[0].snapshot;
  assert.deepEqual(await service.rename(admin,region,{name:'神奈川横滨片区',revision:0}),{id:region,revision:1,reused:false});assert.equal((await service.detail(admin,region)).region.name,'神奈川横滨片区');assert.deepEqual((await db.query('SELECT snapshot FROM bd_region_task WHERE region_id=$1',[region])).rows[0].snapshot,before);
  assert.deepEqual(await service.rename(admin,region,{name:'神奈川横滨片区',revision:1}),{id:region,revision:1,reused:true});await assert.rejects(()=>service.rename(admin,region,{name:'stale',revision:0}),{code:'REVISION_CONFLICT'});await db.query("UPDATE bd_region SET state='archived' WHERE id=$1",[region]);await assert.rejects(()=>service.rename(admin,region,{name:'archived',revision:1}),{code:'REGION_ARCHIVED'});
  const agent=request.agent(app.getHttpServer());const login=(await agent.post('/api/auth/login').send({username:f.codes[0],password:'fixture-assets'})).body;await agent.get('/api/zone-assets/regions').set('X-CSRF-Token',login.csrfToken).expect(200);await agent.post('/api/zone-assets/regions/'+region+'/rename').set('X-CSRF-Token',login.csrfToken).send({name:'x',revision:1}).expect(409);

  await db.query("UPDATE bd_region SET state='ready' WHERE id=$1",[f.region]);
  const originalGeometry=(await db.query('SELECT geometry FROM bd_region WHERE id=$1',[f.region])).rows[0].geometry;
  const boundary=await service.regionMap(admin,{region:'kanagawa',limit:1});assert.ok(boundary.features.some(row=>row.id===f.region));assert.deepEqual(boundary.features.find(row=>row.id===f.region)?.geometry,originalGeometry);
  assert.equal((await service.regionMap(admin,{region:'tokyo23'})).features.length,0);assert.equal((await service.regionMap(admin,{region:'kanagawa',after:f.region})).features.length,0);
  await assert.rejects(service.regionMap(worker,{region:'kanagawa'}),(error:any)=>error.code==='FORBIDDEN');
  await db.query("UPDATE bd_region SET state='archived' WHERE id=$1",[f.region]);assert.equal((await service.regionMap(admin,{region:'kanagawa'})).features.length,0);
 }finally{if(f){await db.query("DELETE FROM bd_store_visit_log WHERE store_id LIKE $1",[f.prefix+'%']);await db.query("DELETE FROM bd_grid_store WHERE release_id=$1",[f.release]);await db.query("DELETE FROM bd_work_store WHERE id LIKE $1",[f.prefix+'%']);await db.query("DELETE FROM bd_log WHERE domain='zone' AND ref_id=$1 AND actor=$2",[f.region,f.codes[0]]);await clearBatchWork(db,f);}await app.close();}
});
