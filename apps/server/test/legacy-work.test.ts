import 'reflect-metadata';
import test from 'node:test';import assert from 'node:assert/strict';import request from '../node_modules/supertest';import {hash} from '@node-rs/argon2';
import {createApp} from '../src/main';import {Database} from '../src/database';import {seedBatchWork,clearBatchWork} from '../../../scripts/batch-work-fixture.mjs';
const url=process.env.NEXT_TEST_DATABASE_URL;
test('retired direct task routes keep login/role redirects and exact 410 contract without resolving IDs or writing facts',{skip:!url},async()=>{
 assert.match(new URL(url!).pathname,/_refactor_p1$/);const app=await createApp({databaseUrl:url!,sessionSecret:'legacy-work-test-at-least-40-character-secret',cookieSecure:false,host:'127.0.0.1',port:0}),db=app.get(Database);let f:any;
 try{
  f=await seedBatchWork(db,'P5-LW-'+Date.now().toString(36),await hash('fixture-legacy-work'));
  const snapshot=async()=>({grids:(await db.query('SELECT * FROM bd_grid_task WHERE id=ANY($1::int[]) ORDER BY id',[f.grids.flat()])).rows,stores:(await db.query('SELECT * FROM bd_store_task WHERE id=ANY($1::int[]) ORDER BY id',[f.stores.flat(2)])).rows,events:(await db.query('SELECT * FROM bd_task_event WHERE main_task_id=ANY($1::int[])',[f.mains])).rows});const before=await snapshot();
  for(const path of ['/api/legacy-work/zones/999999999999','/api/legacy-work/grids/'+f.baseGrids[0]]){
   await request(app.getHttpServer()).get(path).expect(302).expect('Location','/login');await request(app.getHttpServer()).head(path).expect(302).expect('Location','/login');
  }
  for(const action of ['assign','visit'])await request(app.getHttpServer()).post('/api/legacy-work/grids/1/'+action).send({target_id:1}).expect(302).expect('Location','/login');
  for(const index of [0,1,2]){
   const agent=request.agent(app.getHttpServer());await agent.post('/api/auth/login').send({username:f.codes[index],password:'fixture-legacy-work'}).expect(201);
   const target=index===0?'/admin/zone-tasks':'/my/work';
   for(const kind of ['zones','grids']){await agent.get(`/api/legacy-work/${kind}/999999999999?kw=old&back=https://example.com`).expect(303).expect('Location',target);await agent.head(`/api/legacy-work/${kind}/1`).expect(303).expect('Location',target);}
   for(const action of ['assign','visit']){const response=await agent.post(`/api/legacy-work/grids/${f.baseGrids[0]}/${action}`).type('form').send({target_id:f.users[3],store_id:'not-real',_ft:'old',csrf_token:'wrong'}).expect(410);assert.deepEqual(response.body,{detail:'legacy_task_retired'});}
   await agent.post('/api/legacy-work/zones/1').expect(404);await agent.get('/api/legacy-work/grids/1/visit').expect(404);
  }
  assert.deepEqual(await snapshot(),before);
 }finally{if(f)await clearBatchWork(db,f);await app.close();}
});
