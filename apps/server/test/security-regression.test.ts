import 'reflect-metadata';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {hash,verify} from '@node-rs/argon2';
import request from '../node_modules/supertest';
import ExcelJS from 'exceljs';
import {readFile} from 'node:fs/promises';
import {createApp} from '../src/main';
import {Database} from '../src/database';
import {withExportGuard} from '../src/export-security';
import {safeUploadName} from '../src/private-files';
const url=process.env.NEXT_TEST_DATABASE_URL;
test('private upload names normalize traversal and control characters',()=>{
 assert.equal(safeUploadName('../../../../tmp/a.xlsx'),'a.xlsx');assert.equal(safeUploadName('..\\..\\tmp\\a.xlsx'),'a.xlsx');assert.equal(safeUploadName('bad\0\n.xlsx'),'bad.xlsx');assert.equal(safeUploadName('../../..'),'upload.xlsx');
});
test('security migration, isolated credentials, OAuth admission/scope, export audit and shared throttling',{skip:!url},async()=>{
 const app=await createApp({databaseUrl:url!,sessionSecret:'security-regression-secret-at-least-40-characters',cookieSecure:true,host:'127.0.0.1',port:0});
 const db=app.get(Database),users:number[]=[],codes=['SECURITY-ONE','SECURITY-TWO'],clients:string[]=[];
 // Cookies marked Secure are copied explicitly by this HTTP-only test, never weakened in production configuration.
 const agent=request(app.getHttpServer()),password='fixture-security-password';let firstCredential='';
 try{
  await db.query(await readFile('deploy-next/migrations/001-auth-security.sql','utf8'));
  users.push((await db.query("INSERT INTO users(username,password_hash,display_name,role,status,is_active,must_change_password,lang,created_at) VALUES('security-admin',$1,'security admin','admin','active',true,false,'zh',now()) RETURNING id",[await hash(password)])).rows[0].id);
  const login=await agent.post('/api/auth/login').send({username:'security-admin',password}).expect(201),cookie=login.headers['set-cookie'].map((c:string)=>c.split(';')[0]).join(';');
  const post=(path:string,body:unknown)=>agent.post(path).set('Cookie',cookie).set('X-CSRF-Token',login.body.csrfToken).send(body);
  const get=(path:string)=>agent.get(path).set('Cookie',cookie);
  for(const [i,code]of codes.entries()){
   const created=await post('/api/employees/create',{code,name:i?'normal':"=cmd|' /C calc'!A1"}).expect(201);users.push(created.body.id);
   assert.ok(created.body.temporaryPassword.length>=20);assert.equal(created.body.password_hash,undefined);
   const row=(await db.query('SELECT password_hash,must_change_password FROM users WHERE id=$1',[created.body.id])).rows[0];assert.equal(await verify(row.password_hash,created.body.temporaryPassword),true);assert.equal(row.must_change_password,true);
   const userLogin=await agent.post('/api/auth/login').send({username:created.body.username,password:created.body.temporaryPassword}).expect(201);assert.equal(userLogin.body.user.mustChangePassword,true);
   if(i===0)firstCredential=created.body.temporaryPassword;else assert.notEqual(created.body.temporaryPassword,firstCredential);
  }
  const exported=await get('/api/employees/accounts-export').buffer(true).parse((res,done)=>{const chunks:Buffer[]=[];res.on('data',chunk=>chunks.push(chunk));res.on('end',()=>done(null,Buffer.concat(chunks)));}).expect(200);
  assert.equal(exported.headers['x-frame-options'],'DENY');assert.match(exported.headers['strict-transport-security'],/max-age/);
  const book=new ExcelJS.Workbook();await book.xlsx.load(exported.body);assert.doesNotMatch(JSON.stringify(book.worksheets.map(s=>s.getSheetValues())),/初始口令|demo123|temporaryPassword|\$argon2/);
  assert.equal(book.worksheets[0].getCell('B2').type,ExcelJS.ValueType.String);
  assert.equal((await db.query("SELECT count(*)::int AS n FROM security_export_events WHERE user_id=$1 AND kind='employee-accounts' AND ok=true",[users[0]])).rows[0].n,1);
  const reset=await post(`/api/employees/${users[1]}/reset`,{}).expect(201);assert.ok(reset.body.temporaryPassword);assert.notEqual(reset.body.temporaryPassword,'demo123');
  await post('/api/employees/reset-all',{password:'shared-password',confirm:'RESET_ALL_STAFF'}).expect(410);
  await post(`/api/employees/${users[1]}/reset`,{password:'demo123'}).expect(400);
  await db.query("CREATE FUNCTION security_fail_revocation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'fixture revocation failure'; END $$");
  await db.query('CREATE TRIGGER security_fail_revocation BEFORE UPDATE ON api_tokens FOR EACH STATEMENT EXECUTE FUNCTION security_fail_revocation()');
  try{await post('/api/auth/password',{currentPassword:password,newPassword:'replacement-fixture-password'}).expect(500);}
  finally{await db.query('DROP TRIGGER security_fail_revocation ON api_tokens');await db.query('DROP FUNCTION security_fail_revocation()');}
  assert.equal(await verify((await db.query('SELECT password_hash FROM users WHERE id=$1',[users[0]])).rows[0].password_hash,password),true,'failed credential revocation must roll back the password change');
  await get('/api/auth/session').expect(200);
  const v='x'.repeat(43),challenge=createHash('sha256').update(v).digest('base64url'),callback='https://untrusted.fixture.test/callback';
  const reg=await agent.post('/oauth/register').send({redirect_uris:[callback],scope:'read'}).expect(201);clients.push(reg.body.client_id);assert.equal(reg.body.approval_required,true);
  const payload={client_id:reg.body.client_id,redirect_uri:callback,response_type:'code',code_challenge:challenge,code_challenge_method:'S256',scope:'read'};
  await get('/api/mcp-oauth/context').query(payload).expect(403);
  await agent.post('/api/mcp-oauth/clients/'+reg.body.client_id).set('Cookie',cookie).send({approved:true,scope:'read'}).expect(403);
  await post('/api/mcp-oauth/clients/'+reg.body.client_id,{approved:true,scope:'read'}).expect(201);
  await get('/api/mcp-oauth/context').query({...payload,scope:'read write'}).expect(400);
  await get('/api/mcp-oauth/context').query({...payload,redirect_uri:callback+'?changed=1'}).expect(400);
  const approved=await post('/api/mcp-oauth/authorize',{request:payload,approved:true}).expect(201);
  const code=new URL(approved.body.redirect).searchParams.get('code');
  const token=await agent.post('/oauth/token').send({grant_type:'authorization_code',client_id:reg.body.client_id,redirect_uri:callback,code,code_verifier:v}).expect(200);assert.equal(token.body.scope,'read');
  await post('/api/mcp-oauth/clients/'+reg.body.client_id,{approved:false,scope:'read'}).expect(201);
  assert.ok((await db.query('SELECT revoked_at FROM api_tokens WHERE token_hash=$1',[createHash('sha256').update(token.body.access_token).digest('hex')])).rows[0].revoked_at);
  await agent.post('/oauth/token').send({grant_type:'refresh_token',client_id:reg.body.client_id,refresh_token:token.body.refresh_token}).expect(403);
  // The migration is idempotent and does not change approved policy decisions.
  await db.query(await readFile('deploy-next/migrations/001-auth-security.sql','utf8'));
  let release!:()=>void,started!:()=>void;const held=new Promise<void>(resolve=>{release=resolve;}),entered=new Promise<void>(resolve=>{started=resolve;});
  const first=withExportGuard(db,users[0],'fixture','',async()=>{started();await held;return 'done';});await entered;
  try{await assert.rejects(withExportGuard(db,users[0],'fixture','',async()=>''),{status:429});}finally{release();}await first;
  await assert.rejects(withExportGuard(db,users[0],'fixture','',async()=>{throw Error('fixture');}));
  assert.equal((await db.query("SELECT ok FROM security_export_events WHERE user_id=$1 ORDER BY id DESC LIMIT 1",[users[0]])).rows[0].ok,false);
  const before=(await db.query('SELECT count(*)::int AS n FROM security_export_events WHERE user_id=$1',[users[0]])).rows[0].n;
  for(let i=before;i<10;i++)await withExportGuard(db,users[0],'fixture','',async()=>{});
  await assert.rejects(withExportGuard(db,users[0],'fixture','',async()=>{}),{status:429});
 }finally{
  await db.query('DELETE FROM oauth_codes WHERE user_id=ANY($1::int[])',[users]);await db.query('DELETE FROM oauth_refresh_tokens WHERE user_id=ANY($1::int[])',[users]);await db.query('DELETE FROM api_tokens WHERE user_id=ANY($1::int[])',[users]);await db.query('DELETE FROM oauth_clients WHERE client_id=ANY($1::text[])',[clients]);await db.query('DELETE FROM users WHERE id=ANY($1::int[])',[users]);await db.query('DELETE FROM persons WHERE code=ANY($1::text[])',[codes]);await app.close();
 }
});
