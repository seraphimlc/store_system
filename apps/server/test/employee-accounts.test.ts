import 'reflect-metadata';
import test from 'node:test';
import assert from 'node:assert/strict';
import request from '../node_modules/supertest';
import ExcelJS from 'exceljs';
import { hash } from '@node-rs/argon2';
import { createApp } from '../src/main';
import { Database } from '../src/database';
import { cleanUsername, nameCandidate } from '../src/employee-service';
const url=process.env.NEXT_TEST_DATABASE_URL;
test('legacy login-name rules preserve pinyin, code fallback and cleanup',()=>{
 assert.equal(nameCandidate('王小明','1234'),'wangxiaoming');
 assert.equal(cleanUsername(' --New_User X-- '),'newuserx');
 assert.match(nameCandidate('あ','00001234'),/^emp\d+$/);
});
test('employee account lifecycle: atomic create/edit, role bounds, password reset, XLSX and identity preservation',{skip:!url},async()=>{
 assert.match(new URL(url!).pathname,/_refactor_p1$/);
 const app=await createApp({databaseUrl:url!,sessionSecret:'employee-accounts-test-secret-40-characters',cookieSecure:false,port:0,host:'127.0.0.1'});
 const db=app.get(Database);const password='accounts-fixture-password';const prefix='employee-accounts-p3-';
 const ids:number[]=[];const codes=['P3-ACCT-001','P3-ACCT-002','P3-ACCT-RACE'];
 try{
  const pw=await hash(password);
  const adminId=(await db.query(`INSERT INTO users(username,password_hash,display_name,role,status,is_active,must_change_password,lang,created_at) VALUES($1,$2,'员工建号验收管理员','admin','active',true,false,'zh',now()) RETURNING id`,[prefix+'admin',pw])).rows[0].id;ids.push(adminId);
  const admin=request.agent(app.getHttpServer());const login=await admin.post('/api/auth/login').send({username:prefix+'admin',password}).expect(201);
  const post=(path:string,body:unknown)=>admin.post(path).set('X-CSRF-Token',login.body.csrfToken).send(body);
  await admin.post('/api/employees/create').send({code:codes[0],name:'测试员工'}).expect(403);
  const suggestion=await post('/api/employees/suggest-username',{code:codes[0],name:'王小明'}).expect(201);assert.equal(suggestion.body.username,'wangxiaoming');
  const created=await post('/api/employees/create',{code:'Ｐ３-ＡＣＣＴ-００１',name:'验收队长',username:prefix+'leader',password,role:'leader'}).expect(201);
  ids.push(created.body.id);assert.equal(created.body.personCode,codes[0]);
  await post('/api/employees/create',{code:codes[0],name:'重复员工',username:prefix+'duplicate',password}).expect(409);
  assert.equal((await db.query('SELECT count(*)::int AS n FROM users WHERE person_code=$1',[codes[0]])).rows[0].n,1);
  const second=await post('/api/employees/create',{code:codes[1],name:'第二员工',username:prefix+'other',password}).expect(201);ids.push(second.body.id);
  const races=await Promise.all([post('/api/employees/create',{code:codes[2],name:'并发员工',username:prefix+'race-a',password}),post('/api/employees/create',{code:codes[2],name:'并发员工',username:prefix+'race-b',password})]);
  assert.deepEqual(races.map(r=>r.status).sort(),[201,409]);ids.push(races.find(r=>r.status===201)!.body.id);
  const staff=request.agent(app.getHttpServer());await staff.post('/api/auth/login').send({username:created.body.username,password}).expect(201);
  const current=await staff.get('/api/auth/session').expect(200);assert.equal(current.body.user.mustChangePassword,true);
  const old={username:created.body.username,name:'验收队长',status:'active',language:'',role:'leader'};
  await post(`/api/employees/${created.body.id}/edit`,{name:'不该保存的姓名',username:second.body.username,status:'active',language:'ja',role:'staff',expected:old}).expect(409);
  assert.equal((await db.query('SELECT display_name FROM persons WHERE code=$1',[codes[0]])).rows[0].display_name,'验收队长');
  await post(`/api/employees/${created.body.id}/edit`,{name:'已编辑姓名',username:prefix+'edited',status:'active',language:'ja',role:'leader',expected:old,code:'CHANGE-IDENTITY'}).expect(400);
  await post(`/api/employees/${created.body.id}/edit`,{name:'已编辑姓名',username:prefix+'edited',status:'active',language:'ja',role:'admin',expected:old}).expect(400);
  await post(`/api/employees/${created.body.id}/edit`,{name:'已编辑姓名',username:prefix+'edited',status:'active',language:'ja',role:'leader',expected:old}).expect(201);
  assert.equal((await db.query('SELECT display_name FROM persons WHERE code=$1',[codes[0]])).rows[0].display_name,'已编辑姓名');
  assert.equal((await db.query('SELECT person_code FROM users WHERE id=$1',[created.body.id])).rows[0].person_code,codes[0]);
  await post(`/api/employees/${created.body.id}/edit`,{name:'过期编辑',username:prefix+'edited',status:'active',language:'ja',role:'staff',expected:old}).expect(409);
  await post(`/api/employees/${created.body.id}/reset`,{password:'short'}).expect(400);
  await post(`/api/employees/${created.body.id}/reset`,{password:'reset06'}).expect(201);
  await staff.get('/api/auth/session').expect(401);
  const resetLogin=await staff.post('/api/auth/login').send({username:prefix+'edited',password:'reset06'}).expect(201);assert.equal(resetLogin.body.user.mustChangePassword,true);
  await post('/api/employees/reset-all',{password:'bulk06',confirm:'wrong'}).expect(410);
  await post('/api/employees/reset-all',{password:'bulk06',confirm:'RESET_ALL_STAFF'}).expect(410);
  await staff.get('/api/auth/session').expect(200);
  await admin.get('/api/dashboard').expect(200);
  const exported=await admin.get('/api/employees/accounts-export').buffer(true).parse((res,done)=>{
   const chunks:Buffer[]=[];res.on('data',chunk=>chunks.push(Buffer.from(chunk)));res.on('end',()=>done(null,Buffer.concat(chunks)));
  }).expect(200);
  assert.match(exported.headers['content-disposition'],/filename\*=UTF-8/);
  const workbook=new ExcelJS.Workbook();await workbook.xlsx.load(exported.body);
  const values=workbook.getWorksheet('员工登录名')!.getSheetValues();assert.equal(values.length,5);
  assert.match(JSON.stringify(values),/已编辑姓名/);assert.doesNotMatch(JSON.stringify(workbook.worksheets.map(sheet=>sheet.getSheetValues())),/\$argon2|初始口令|demo123|bulk06|reset06/);
 }finally{
  await db.query('DELETE FROM users WHERE id=ANY($1::int[]) OR person_code=ANY($2::text[])',[ids,codes]);
  await db.query('DELETE FROM persons WHERE code=ANY($1::text[])',[codes]);await app.close();
 }
});
