import {spawnLegacyPython} from '../../../scripts/legacy-python.mjs';
import 'reflect-metadata';
import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';
import {hash} from '@node-rs/argon2';
import request from '../node_modules/supertest';
import {createApp} from '../src/main';
import {Database} from '../src/database';
import {ZoneAssetsService} from '../src/zone-assets';
import {gridToGeographic,gridCoverageDifference,gridToMetric,type GridPolygon} from '../../../packages/domain/src/grid-geometry';
import {seedBatchWork,clearBatchWork} from '../../../scripts/batch-work-fixture.mjs';
const url=process.env.NEXT_TEST_DATABASE_URL;
function box(x:number):GridPolygon{return {type:'Polygon',coordinates:[[[x,0],[x+1000,0],[x+1000,1000],[x,1000],[x,0]]]};}
test('regroup preview and merge match legacy, recompute remainders, archive empties and atomically preserve maps/batches',{skip:!url},async()=>{
 assert.match(new URL(url!).pathname,/_refactor_p1$/);
 const root=await mkdtemp(join(tmpdir(),'grid-merge-')),previous=process.env.NEXT_ZONE_DATA_DIRECTORY;process.env.NEXT_ZONE_DATA_DIRECTORY=root;
 const app=await createApp({databaseUrl:url!,sessionSecret:'region-merge-test-at-least-40-character-secret',cookieSecure:false,port:0,host:'127.0.0.1'}),db=app.get(Database),service=app.get(ZoneAssetsService);let f:any;const created:number[]=[];
 try{
  f=await seedBatchWork(db,'P5-RM-'+Date.now().toString(36),await hash('fixture-region-merge'));const admin=f.users[0],grid=f.baseGrids[0];
  for(let i=0;i<3;i++)await db.query("UPDATE bd_work_grid SET geometry=$1,task_state='unvisited' WHERE id=$2",[JSON.stringify(gridToGeographic(box(i*1000))),f.baseGrids[i]]);
  await db.query("INSERT INTO bd_grid_store(release_id,grid_id,source_id) VALUES($1,$2,'synthetic-merge-store')",[f.release,grid]);
  const maps=(await db.query('SELECT * FROM bd_grid_store WHERE release_id=$1',[f.release])).rows,snapshots=(await db.query('SELECT id,snapshot,revision FROM bd_grid_task WHERE grid_id=ANY($1::int[]) ORDER BY id',[f.baseGrids])).rows;
  async function body(ids:number[],boundaries=true){const {rows}=await db.query('SELECT g.id,g.revision,g.zone_id AS "zoneId",r.revision AS "zoneRevision" FROM bd_work_grid g LEFT JOIN bd_region r ON r.id=g.zone_id WHERE g.id=ANY($1::int[]) ORDER BY g.id',[ids]);return {name:'Merged test',mergeBoundaries:boundaries,selections:rows};}
  const input=await body([grid]),middle=await body([f.baseGrids[1]]);
  await assert.rejects(()=>service.mergePreview(f.users[2],input),{code:'FORBIDDEN'});
  await assert.rejects(()=>service.mergePreview(admin,{...input,selections:[...input.selections,...input.selections]}),{code:'VALIDATION'});
  await assert.rejects(()=>service.mergePreview(admin,middle),{code:'GRID_NOT_CONNECTED'});
  await db.query('UPDATE bd_region SET team_id=$1 WHERE id=$2',[f.team,f.region]);await assert.rejects(()=>service.mergePreview(admin,input),{code:'REGION_ASSIGNED'});await db.query('UPDATE bd_region SET team_id=NULL WHERE id=$1',[f.region]);
  await db.query('UPDATE bd_work_grid SET assignee_id=$1 WHERE id=$2',[f.users[2],f.baseGrids[2]]);await assert.rejects(()=>service.mergePreview(admin,input),{code:'GRID_PROTECTED'});await db.query('UPDATE bd_work_grid SET assignee_id=NULL WHERE id=$1',[f.baseGrids[2]]);
  await db.query("UPDATE bd_work_grid SET state='disabled' WHERE id=$1",[grid]);await assert.rejects(()=>service.mergePreview(admin,input),{code:'GRID_DISABLED'});await db.query("UPDATE bd_work_grid SET state='ready' WHERE id=$1",[grid]);
  const selections=input.selections.map((row:any)=>({id:row.id,revision:row.revision,zone_id:row.zoneId,zone_revision:row.zoneRevision}));
  const oracle=`import json\nfrom app.db import SessionLocal\nfrom app.models import User,BdRegion,BdWorkGrid\nfrom app.services.bd_grid_regions import preview,merge\ns=SessionLocal()\ntry:\n u=s.get(User,${admin})\n choices=json.loads(${JSON.stringify(JSON.stringify(selections))})\n p=preview(s,u,choices,True)\n r=merge(s,u,choices,'Merged test',True)\n s.flush()\n old=s.get(BdRegion,${f.region})\n print(json.dumps({'area':r.area_km2,'stores':r.store_count,'geometry':r.geometry,'remainder':{'state':old.state,'revision':old.revision,'stores':old.store_count,'area':old.area_km2},'preview':p['properties']}))\nfinally:\n s.rollback()\n s.close()`;
  const legacy=spawnLegacyPython(['-c',oracle],{encoding:'utf8',env:{...process.env,DATABASE_URL:url!.replace(/^postgres(?:ql)?:/,'postgresql+psycopg2:'),BD_ZONE_DATA_DIR:root}});assert.equal(legacy.status,0,legacy.stderr);const expected=JSON.parse(legacy.stdout);
  let preview=await service.mergePreview(admin,input);assert.equal(preview.gridCount,1);assert.equal(preview.storeCount,expected.stores);assert.equal(preview.areaKm2,expected.area);assert.deepEqual(preview.archivedZoneIds,[]);
  assert.equal((await db.query('SELECT count(*)::int AS n FROM bd_region WHERE release_id=$1',[f.release])).rows[0].n,1);
  const apply=()=>({...input,previewToken:preview.previewToken,confirm:'MERGE_GRIDS'});
  await writeFile(join(root,'fixture.roads-v1.source.json'),JSON.stringify({elements:[]}));await assert.rejects(()=>service.merge(admin,apply()),{code:'PREVIEW_CHANGED'});preview=await service.mergePreview(admin,input);
  const connect=db.pool.connect.bind(db.pool),mutable=db.pool as unknown as {connect:()=>Promise<unknown>};mutable.connect=async()=>{const client=await connect(),query=client.query.bind(client),release=client.release.bind(client);client.query=((sql:any,...args:any[])=>{if(typeof sql==='string'&&sql.includes('INSERT INTO bd_log'))throw Error('synthetic regroup audit failure');return(query as any)(sql,...args);}) as any;client.release=()=>{client.query=query;release();};return client;};
  try{await assert.rejects(()=>service.merge(admin,apply()),/synthetic regroup audit failure/);}finally{mutable.connect=connect;}
  assert.equal((await db.query('SELECT count(*)::int AS n FROM bd_region WHERE release_id=$1',[f.release])).rows[0].n,1);
  const agent=request.agent(app.getHttpServer()),login=(await agent.post('/api/auth/login').send({username:f.codes[0],password:'fixture-region-merge'}).expect(201)).body;
  await agent.post('/api/zone-assets/merge-preview').send(input).expect(403);await agent.post('/api/zone-assets/merge-preview').set('X-CSRF-Token',login.csrfToken).send(input).expect(201);
  const races=await Promise.allSettled([service.merge(admin,apply()),service.merge(admin,apply())]);assert.equal(races.filter(row=>row.status==='fulfilled').length,1);const result=(races.find(row=>row.status==='fulfilled') as PromiseFulfilledResult<Awaited<ReturnType<typeof service.merge>>>).value;created.push((result as any).id);assert.ok(await gridCoverageDifference(gridToMetric(result.geometry),gridToMetric(expected.geometry))<=.01);
  const remainder=(await db.query('SELECT state,revision,store_count AS stores,area_km2 AS area FROM bd_region WHERE id=$1',[f.region])).rows[0];assert.deepEqual(remainder,expected.remainder);
  await assert.rejects(()=>service.merge(admin,apply()),{code:'REVISION_CONFLICT'});
  const rest=await body(f.baseGrids.slice(1),false),restPreview=await service.mergePreview(admin,rest);assert.deepEqual(restPreview.archivedZoneIds,[f.region]);assert.equal(restPreview.displayGeometry.type,'GeometryCollection');
  const done=await service.merge(admin,{...rest,previewToken:restPreview.previewToken,confirm:'MERGE_GRIDS'});created.push((done as any).id);
  assert.equal((await db.query('SELECT state FROM bd_region WHERE id=$1',[f.region])).rows[0].state,'archived');
  const all=await body(f.baseGrids),allPreview=await service.mergePreview(admin,all);assert.deepEqual([...allPreview.archivedZoneIds].sort((a,b)=>a-b),[...created].sort((a,b)=>a-b));
  await assert.rejects(()=>service.mergePreview(admin,{...all,selections:all.selections.map((row:any,index:number)=>index?row:{...row,zoneRevision:99})}),{code:'REVISION_CONFLICT'});
  const joined=await service.merge(admin,{...all,previewToken:allPreview.previewToken,confirm:'MERGE_GRIDS'});created.push((joined as any).id);
  assert.equal(joined.gridCount,3);assert.equal(joined.storeCount,1);
  const {rows:[temporaryRelease]}=await db.query("INSERT INTO bd_zone_release(fingerprint,source,parameters,audit,reviews,created_by,created_at) VALUES($1,'{}','{}','{}','[]',$2,now()) RETURNING id",[f.prefix+'-other-release',admin]);
  try{await db.query('UPDATE bd_work_grid SET release_id=$1 WHERE id=$2',[temporaryRelease.id,f.baseGrids[1]]);const mixed=await body(f.baseGrids);await assert.rejects(()=>service.mergePreview(admin,mixed),{code:'GRID_RELEASE_MISMATCH'});}finally{await db.query('UPDATE bd_work_grid SET release_id=$1 WHERE id=$2',[f.release,f.baseGrids[1]]);await db.query('DELETE FROM bd_zone_release WHERE id=$1',[temporaryRelease.id]);}
  assert.deepEqual((await db.query('SELECT * FROM bd_grid_store WHERE release_id=$1',[f.release])).rows,maps);assert.deepEqual((await db.query('SELECT id,snapshot,revision FROM bd_grid_task WHERE grid_id=ANY($1::int[]) ORDER BY id',[f.baseGrids])).rows,snapshots);
 }finally{if(f){await db.query("DELETE FROM bd_log WHERE domain='zone' AND ref_id=ANY($1::int[]) AND actor=$2",[[f.region,...created],f.codes[0]]);await db.query('DELETE FROM bd_grid_store WHERE release_id=$1',[f.release]);await db.query('UPDATE bd_work_grid SET zone_id=NULL WHERE id=ANY($1::int[])',[f.baseGrids]);await db.query('DELETE FROM bd_region WHERE id=ANY($1::int[])',[created]);await clearBatchWork(db,f);}await app.close();if(previous===undefined)delete process.env.NEXT_ZONE_DATA_DIRECTORY;else process.env.NEXT_ZONE_DATA_DIRECTORY=previous;await rm(root,{recursive:true,force:true});}
});
