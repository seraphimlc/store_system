import 'reflect-metadata';
import test from 'node:test';import assert from 'node:assert/strict';
import request from '../node_modules/supertest';import {hash} from '@node-rs/argon2';
import {createApp} from '../src/main';import {Database} from '../src/database';
const url=process.env.NEXT_TEST_DATABASE_URL;
test('employee state changes preserve history, permit leave login, invalidate work access and reject stale writes',{skip:!url},async()=>{
 assert.match(new URL(url!).pathname,/_refactor_p1$/);
 const app=await createApp({databaseUrl:url!,sessionSecret:'employee-test-only-at-least-40-characters',cookieSecure:false,port:0,host:'127.0.0.1'});const db=app.get(Database);
 const password='employee-fixture';const pw=await hash(password);let adminId:number|undefined,staffId:number|undefined;
 try{
  const rows=(await db.query(`INSERT INTO users(username,password_hash,display_name,role,status,is_active,must_change_password,lang,created_at) VALUES
   ('employee-p3-admin',$1,'员工验收管理员','admin','active',true,false,'zh',now()),('employee-p3-staff',$1,'员工验收对象','leader','active',true,false,'ja',now()) RETURNING id,role`,[pw])).rows;
  adminId=rows.find(r=>r.role==='admin')!.id;staffId=rows.find(r=>r.role==='leader')!.id;
  const admin=request.agent(app.getHttpServer());const staff=request.agent(app.getHttpServer());
  const login=await admin.post('/api/auth/login').send({username:'employee-p3-admin',password}).expect(201);
  await staff.post('/api/auth/login').send({username:'employee-p3-staff',password}).expect(201);
  await staff.get('/api/employees').expect(403);
  const list=await admin.get('/api/employees?search=employee-p3').expect(200);assert.equal(list.body.total,1);assert.equal(list.body.items[0].id,staffId);assert.ok(!('password_hash' in list.body.items[0]));
  await admin.post(`/api/employees/${staffId}/status`).send({status:'leave',expectedStatus:'active'}).expect(403);
  const leave=await admin.post(`/api/employees/${staffId}/status`).set('X-CSRF-Token',login.body.csrfToken).send({status:'leave',expectedStatus:'active'}).expect(201);assert.equal(leave.body.isActive,true);assert.equal(leave.body.tokenUsable,false);
  await staff.get('/api/auth/session').expect(200);await staff.get('/api/work/batches').expect(403);
  await request.agent(app.getHttpServer()).post('/api/auth/login').send({username:'employee-p3-staff',password}).expect(201);
  await admin.post(`/api/employees/${staffId}/status`).set('X-CSRF-Token',login.body.csrfToken).send({status:'resigned',expectedStatus:'active'}).expect(409);
  await admin.post(`/api/employees/${staffId}/status`).set('X-CSRF-Token',login.body.csrfToken).send({status:'resigned',expectedStatus:'leave'}).expect(201);
  await staff.get('/api/auth/session').expect(401);
  assert.equal((await db.query('SELECT count(*)::int AS n FROM users WHERE id=$1',[staffId])).rows[0].n,1);
  await admin.post(`/api/employees/${adminId}/status`).set('X-CSRF-Token',login.body.csrfToken).send({status:'disabled',expectedStatus:'active'}).expect(404);
 }finally{await db.query('DELETE FROM users WHERE id=ANY($1::int[])',[[adminId,staffId].filter(Boolean)]);await app.close();}
});
