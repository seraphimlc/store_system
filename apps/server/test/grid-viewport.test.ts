import 'reflect-metadata';
import test from 'node:test';import assert from 'node:assert/strict';import {hash} from '@node-rs/argon2';import request from '../node_modules/supertest';
import {createApp} from '../src/main';import {Database} from '../src/database';import {ZoneAssetsService} from '../src/zone-assets';import {seedBatchWork,clearBatchWork} from '../../../scripts/batch-work-fixture.mjs';
const url=process.env.NEXT_TEST_DATABASE_URL;
test('admin viewport bounds geometry payloads, preserves holes, filters/continues without gaps and uses current work guards',{skip:!url},async()=>{
 assert.match(new URL(url!).pathname,/_refactor_p1$/);const app=await createApp({databaseUrl:url!,sessionSecret:'grid-viewport-test-at-least-40-character-secret',cookieSecure:false,host:'127.0.0.1',port:0}),db=app.get(Database),service=app.get(ZoneAssetsService);let f:any;
 const view={west:138.99,east:139.02,south:34.99,north:35.02,region:'tokyo23',q:''};
 try{
  f=await seedBatchWork(db,'P5-GV-'+Date.now().toString(36),await hash('fixture-grid-viewport'));const admin=f.users[0],worker=f.users[2];view.q=f.prefix;
  await db.query("UPDATE bd_work_grid SET task_state='unvisited' WHERE id=ANY($1::int[])",[f.baseGrids]);
  const hole={type:'Polygon',coordinates:[[[139,35],[139.01,35],[139.01,35.01],[139,35.01],[139,35]],[[139.002,35.002],[139.004,35.002],[139.004,35.004],[139.002,35.004],[139.002,35.002]]]};
  await db.query('UPDATE bd_work_grid SET geometry=$1 WHERE id=$2',[JSON.stringify(hole),f.baseGrids[0]]);
  const first=await service.viewport(admin,{...view,limit:1});assert.equal(first.features.length,1);assert.equal(first.hasMore,true);assert.equal(first.nextCursor,f.baseGrids[0]);assert.deepEqual(first.features[0].geometry,hole);assert.equal(first.features[0].properties.selectable,true);
  const second=await service.viewport(admin,{...view,limit:1,after:first.nextCursor});const third=await service.viewport(admin,{...view,limit:1,after:second.nextCursor});assert.equal(third.hasMore,false);assert.deepEqual([...first.features,...second.features,...third.features].map(g=>g.id),f.baseGrids);
  assert.equal((await service.viewport(admin,{...view,q:'not-present'})).features.length,0);assert.equal((await service.viewport(admin,{...view,west:140,east:140.01})).features.length,0);
  const insideRegion=await service.viewport(admin,{...view,zoneId:f.region});assert.deepEqual(insideRegion.features.map(row=>row.id),f.baseGrids);assert.equal((await service.viewport(admin,{...view,zoneId:2147483647})).features.length,0);await assert.rejects(()=>service.viewport(admin,{...view,zoneId:0}),{code:'VALIDATION'});
  // A viewport entirely inside a hole may match the bbox; the hole itself is never filled in returned geometry.
  assert.equal((await service.viewport(admin,{...view,west:139.0025,east:139.0035,south:35.0025,north:35.0035})).bboxOnly,true);
  await db.query('UPDATE bd_work_grid SET assignee_id=$1 WHERE id=$2',[worker,f.baseGrids[1]]);assert.equal((await service.viewport(admin,view)).features.every(g=>!g.properties.selectable),true);
  await assert.rejects(()=>service.viewport(worker,view),{code:'FORBIDDEN'});await db.query("UPDATE users SET status='leave' WHERE id=$1",[admin]);await assert.rejects(()=>service.viewport(admin,view),{code:'FORBIDDEN'});await db.query("UPDATE users SET status='active' WHERE id=$1",[admin]);
  for(const changes of [{west:140,east:139},{north:35,south:36},{west:139,east:139},{west:-180,east:180},{west:'NaN'},{limit:101},{after:-1}])await assert.rejects(()=>service.viewport(admin,{...view,...changes}),{code:'VALIDATION'});
  const agent=request.agent(app.getHttpServer());await agent.get('/api/zone-assets/grid-viewport').query(view).expect(401);await agent.post('/api/auth/login').send({username:f.codes[0],password:'fixture-grid-viewport'}).expect(201);await agent.get('/api/zone-assets/grid-viewport').query({...view,limit:1}).expect(200);
  const oldKey=process.env.GOOGLE_PUBLIC_API_KEY;try{process.env.GOOGLE_PUBLIC_API_KEY='fixture-public-key';const config=(await agent.get('/api/zone-assets/map-config').expect(200)).body;assert.deepEqual(config,{enabled:true,key:'fixture-public-key',provider:'google'});delete process.env.GOOGLE_PUBLIC_API_KEY;assert.equal((await agent.get('/api/zone-assets/map-config').expect(200)).body.enabled,false);}finally{if(oldKey===undefined)delete process.env.GOOGLE_PUBLIC_API_KEY;else process.env.GOOGLE_PUBLIC_API_KEY=oldKey;}
  const large={type:'Polygon',coordinates:[Array.from({length:150000},()=>[139.00001,35.00001])]};await db.query('UPDATE bd_work_grid SET geometry=$1 WHERE id=$2',[JSON.stringify(large),f.baseGrids[0]]);
  await assert.rejects(()=>service.viewport(admin,{...view,limit:1}),{code:'MAP_GEOMETRY_TOO_LARGE'});
 }finally{if(f)await clearBatchWork(db,f);await app.close();}
});
