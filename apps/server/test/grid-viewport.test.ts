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
  const exact=await service.viewport(admin,{...view,q:'',gridId:f.baseGrids[0]});assert.deepEqual(exact.features.map(g=>g.id),[f.baseGrids[0]]);assert.equal(exact.hasMore,false);
  const offscreen=await service.viewport(admin,{...view,q:'',gridId:f.baseGrids[0],west:140,east:140.01});assert.deepEqual(offscreen.features.map(g=>g.id),[f.baseGrids[0]],'Exact grid selection must resolve outside current viewport');
  const listed=await service.grids(admin,{region:'tokyo23',gridId:f.baseGrids[0]});assert.equal(listed.total,1);assert.deepEqual(listed.items.map((g:{id:number})=>g.id),[f.baseGrids[0]]);
  await assert.rejects(()=>service.viewport(admin,{...view,gridId:0}),{code:'VALIDATION'});await assert.rejects(()=>service.grids(admin,{gridId:0}),{code:'VALIDATION'});
  const insideRegion=await service.viewport(admin,{...view,zoneId:f.region});assert.deepEqual(insideRegion.features.map(row=>row.id),f.baseGrids);assert.equal((await service.viewport(admin,{...view,zoneId:2147483647})).features.length,0);await assert.rejects(()=>service.viewport(admin,{...view,zoneId:0}),{code:'VALIDATION'});
  // A viewport entirely inside a hole may match the bbox; the hole itself is never filled in returned geometry.
  assert.equal((await service.viewport(admin,{...view,west:139.0025,east:139.0035,south:35.0025,north:35.0035})).bboxOnly,true);
  await db.query('UPDATE bd_work_grid SET assignee_id=$1 WHERE id=$2',[worker,f.baseGrids[1]]);assert.equal((await service.viewport(admin,view)).features.every(g=>!g.properties.selectable),true);
  await assert.rejects(()=>service.viewport(worker,view),{code:'FORBIDDEN'});await db.query("UPDATE users SET status='leave' WHERE id=$1",[admin]);await assert.rejects(()=>service.viewport(admin,view),{code:'FORBIDDEN'});await db.query("UPDATE users SET status='active' WHERE id=$1",[admin]);
  for(const changes of [{west:140,east:139},{north:35,south:36},{west:139,east:139},{west:-180,east:180},{west:'NaN'},{limit:101},{after:-1}])await assert.rejects(()=>service.viewport(admin,{...view,...changes}),{code:'VALIDATION'});
  const agent=request.agent(app.getHttpServer());await agent.get('/api/zone-assets/grid-viewport').query(view).expect(401);await agent.post('/api/auth/login').send({username:f.codes[0],password:'fixture-grid-viewport'}).expect(201);await agent.get('/api/zone-assets/grid-viewport').query({...view,limit:1}).expect(200);
  const cachedManifest=(await agent.get('/api/zone-assets/grid-viewport').query({...view,limit:1}).set('X-Grid-Geometry-Cache','manifest').expect(200)).body;assert.equal(cachedManifest.features[0].geometry,null);assert.match(cachedManifest.geometryRefs[0].key,/^current:/);const geometryKey=cachedManifest.geometryRefs[0].key;const cachedGeometry=(await agent.get('/api/zone-assets/grid-viewport').query({...view,limit:1}).set('X-Grid-Geometry-Cache','load:'+geometryKey).expect(200)).body;assert.deepEqual(cachedGeometry.geometries[geometryKey],hole);await request(app.getHttpServer()).get('/api/zone-assets/grid-viewport').query(view).set('X-Grid-Geometry-Cache','load:'+geometryKey).expect(401);
  const oldKey=process.env.GOOGLE_PUBLIC_API_KEY;try{process.env.GOOGLE_PUBLIC_API_KEY='fixture-public-key';const config=(await agent.get('/api/zone-assets/map-config').expect(200)).body;assert.deepEqual(config,{enabled:true,key:'fixture-public-key',provider:'google'});delete process.env.GOOGLE_PUBLIC_API_KEY;assert.equal((await agent.get('/api/zone-assets/map-config').expect(200)).body.enabled,false);}finally{if(oldKey===undefined)delete process.env.GOOGLE_PUBLIC_API_KEY;else process.env.GOOGLE_PUBLIC_API_KEY=oldKey;}
  const large={type:'Polygon',coordinates:[Array.from({length:150000},()=>[139.00001,35.00001])]};await db.query('UPDATE bd_work_grid SET geometry=$1 WHERE id=$2',[JSON.stringify(large),f.baseGrids[0]]);
  await assert.rejects(()=>service.viewport(admin,{...view,limit:1}),{code:'MAP_GEOMETRY_TOO_LARGE'});
 }finally{if(f)await clearBatchWork(db,f);await app.close();}
});

test('viewport warm manifest is one metadata page, reads no geometry and invalidates content without trusting revisions',{skip:!url},async()=>{
 const app=await createApp({databaseUrl:url!,sessionSecret:'fast-viewport-tests-at-least-40-characters',cookieSecure:false,host:'127.0.0.1',port:0}),db=app.get(Database),service=app.get(ZoneAssetsService);let f:any;
 try{
  f=await seedBatchWork(db,'FAST-GV-'+Date.now(),await hash('fast-viewport-fixture'));
  const geometry={type:'Polygon',coordinates:[[[139,35],[139.01,35],[139.01,35.01],[139,35.01],[139,35]]]};
  await db.query('UPDATE bd_work_grid SET geometry=$1 WHERE release_id=$2',[JSON.stringify(geometry),f.release]);
  await db.query(`INSERT INTO bd_work_grid(release_id,source_id,name,stations,keywords,geometry,area_km2,store_count,state,task_state,progress,revision,updated_at)
   SELECT $1,$2||n::text,$2||n::text,'[]','[]',$3,1,0,'ready','unvisited',0,0,now() FROM generate_series(1,100) n RETURNING id`,[f.release,f.prefix+' extra ',JSON.stringify(geometry)]).then(result=>f.baseGrids.push(...result.rows.map((row:{id:number})=>row.id)));
  const view={west:138.99,east:139.02,south:34.99,north:35.02,region:'tokyo23',q:f.prefix};
  const agent=request.agent(app.getHttpServer());await agent.post('/api/auth/login').send({username:f.codes[0],password:'fast-viewport-fixture'}).expect(201);
  const manifest=(await agent.get('/api/zone-assets/grid-viewport').query(view).set('X-Grid-Geometry-Cache','manifest').expect(200)).body;
  assert.equal(manifest.features.length,103);assert.equal(manifest.hasMore,false);assert.ok(manifest.features.every((g:{geometry:unknown})=>g.geometry===null));assert.equal(manifest.geometryRefs.length,103);
  const {gridViewport}=await import('../src/grid-viewport');const sql:string[]=[];
  await db.transaction(client=>gridViewport({query:async(text:string,args:unknown[])=>{sql.push(text);return client.query(text,args);}} as any,f.users[0],view,'manifest'));
  assert.equal(sql.some(text=>text.startsWith('SELECT id,geometry')),false,'Warm manifest must not read full geometry');assert.equal(sql.some(text=>text.includes('jsonb_array_elements')),false);
  const key=manifest.geometryRefs[0].key;
  const loaded=(await agent.get('/api/zone-assets/grid-viewport').query(view).set('X-Grid-Geometry-Cache','load:'+key).expect(200)).body;
  assert.deepEqual(loaded.geometries[key],geometry);assert.equal(Object.keys(loaded.geometries).length,1);
  assert.deepEqual((await service.viewport(f.users[0],{...view,state:'disabled'},'load:'+key)).geometries,{},'Geometry loads must enforce live filters');
  await assert.rejects(()=>service.viewport(f.users[2],view,'load:'+key),{code:'FORBIDDEN'});
  const changed={type:'Polygon',coordinates:[[[139,35],[139.005,35],[139.005,35.005],[139,35.005],[139,35]]]};
  await db.query('UPDATE bd_work_grid SET geometry=$1 WHERE id=$2',[JSON.stringify(changed),f.baseGrids[0]]);
  const fresh=(await agent.get('/api/zone-assets/grid-viewport').query(view).set('X-Grid-Geometry-Cache','manifest').expect(200)).body;
  assert.notEqual(fresh.geometryRefs[0].key,key,'Content edit without revision bump must invalidate metadata');
  assert.deepEqual((await service.viewport(f.users[0],view,'load:'+key)).geometries,{},'Stale content keys must never load');
  await agent.get('/api/zone-assets/grid-viewport').query(view).expect(200).then(response=>assert.equal(response.body.features.length,100,'Legacy clients keep bounded geometry pages'));
 }finally{if(f)await clearBatchWork(db,f);await app.close();}
});
