import 'reflect-metadata';import test from 'node:test';import assert from 'node:assert/strict';
import request from '../node_modules/supertest';import {hash} from '@node-rs/argon2';import {createApp} from '../src/main';import {Database} from '../src/database';
const url=process.env.NEXT_TEST_DATABASE_URL;
test('messages enforce current recipient scope, deduplicate person identities and isolate read receipts',{skip:!url},async()=>{
 assert.match(new URL(url!).pathname,/_refactor_p1$/);const app=await createApp({databaseUrl:url!,sessionSecret:'messages-fixture-secret-at-least-40-characters',cookieSecure:false,port:0,host:'127.0.0.1'});const db=app.get(Database);
 const password='message-fixture-password',pw=await hash(password);const codes=['P3-MSG-L','P3-MSG-A','P3-MSG-B','P3-MSG-C','P3-MSG-D'];const users:number[]=[],messages:number[]=[];let team:number|undefined;
 try{
  await db.query('INSERT INTO persons(code,display_name,created_at) SELECT code,code,now() FROM unnest($1::text[]) AS code',[codes]);
  for(const [name,code,role,status]of [['admin',null,'admin','active'],['leader',codes[0],'leader','active'],['a',codes[1],'staff','active'],['b',codes[2],'staff','active'],['c',codes[3],'staff','leave'],['d',codes[4],'staff','disabled'],['copy',codes[1],'staff','active']]as const){const row=(await db.query(`INSERT INTO users(username,password_hash,display_name,person_code,role,status,is_active,must_change_password,lang,created_at) VALUES($1,$2,$1,$3,$4,$5,$6,false,'zh',now()) RETURNING id`,['messages-p3-'+name,pw,code,role,status,status!=='disabled'])).rows[0];users.push(row.id);}
  team=(await db.query(`INSERT INTO bd_team(name,created_at,updated_at) VALUES('P3消息验收团队',now(),now()) RETURNING id`)).rows[0].id;
  for(const [code,role]of [[codes[0],'leader'],[codes[1],'member'],[codes[3],'member'],[codes[4],'member']])await db.query('INSERT INTO bd_team_member(team_id,person_code,role,start_date,created_at) VALUES($1,$2,$3,\'2026-01-01\',now())',[team,code,role]);
  const clients:Record<string,{agent:ReturnType<typeof request.agent>;csrf:string}>={};
  for(const name of ['admin','leader','a','b']){const agent=request.agent(app.getHttpServer());const login=await agent.post('/api/auth/login').send({username:'messages-p3-'+name,password}).expect(201);clients[name]={agent,csrf:login.body.csrfToken};}
  const post=(name:string,path:string,body:unknown)=>clients[name].agent.post(path).set('X-CSRF-Token',clients[name].csrf).send(body);
  await clients.a.agent.get('/api/messages/candidates').expect(403);await post('a','/api/messages/send',{title:'越权',mode:'pick',codes:[codes[2]]}).expect(403);
  const candidates=await clients.leader.agent.get('/api/messages/candidates').expect(200);assert.deepEqual(candidates.body.items.map((row:{code:string})=>row.code).sort(),[codes[0],codes[1],codes[3]].sort());
  const adminCandidates=await clients.admin.agent.get('/api/messages/candidates').expect(200);assert.equal(adminCandidates.body.items.filter((row:{code:string})=>row.code===codes[1]).length,1);
  await post('leader','/api/messages/send',{title:'跨队消息',mode:'pick',codes:[codes[2]]}).expect(403);
  await post('admin','/api/messages/send',{title:'停用账号',mode:'pick',codes:[codes[4]]}).expect(403);
  const one=await post('leader','/api/messages/send',{title:'单人通知',body:'日常验收内容',mode:'pick',codes:[codes[1],codes[1]]}).expect(201);messages.push(one.body.id);assert.equal(one.body.recipients,1);
  const all=await post('leader','/api/messages/send',{title:'本队通知',mode:'all',codes:[]}).expect(201);messages.push(all.body.id);assert.equal(all.body.recipients,3);
  assert.equal((await db.query('SELECT count(*)::int AS n FROM bd_message_recipient WHERE message_id=$1',[one.body.id])).rows[0].n,1);
  const inbox=await clients.a.agent.get('/api/messages').expect(200);assert.equal(inbox.body.total,2);assert.equal(inbox.body.unread,2);
  const senderDetail=await clients.leader.agent.get(`/api/messages/${one.body.id}`).expect(200);assert.equal(senderDetail.body.body,'日常验收内容');await clients.a.agent.get(`/api/messages/${one.body.id}`).expect(200);await clients.b.agent.get(`/api/messages/${one.body.id}`).expect(404);await clients.admin.agent.get(`/api/messages/${one.body.id}`).expect(404);await request(app.getHttpServer()).get(`/api/messages/${one.body.id}`).expect(401);assert.equal((await db.query('SELECT read_at FROM bd_message_recipient WHERE message_id=$1 AND person_code=$2',[one.body.id,codes[1]])).rows[0].read_at,null,'Detail GET never marks messages read');
  const outsider=await post('b',`/api/messages/${one.body.id}/read`,{}).expect(201);assert.equal(outsider.body.count,0);assert.equal((await db.query('SELECT read_at FROM bd_message_recipient WHERE message_id=$1 AND person_code=$2',[one.body.id,codes[1]])).rows[0].read_at,null);
  await clients.a.agent.post(`/api/messages/${one.body.id}/read`).send({}).expect(403);
  assert.equal((await post('a',`/api/messages/${one.body.id}/read`,{}).expect(201)).body.count,1);assert.equal((await post('a',`/api/messages/${one.body.id}/read`,{}).expect(201)).body.count,0);
  assert.equal((await post('a','/api/messages/read-all',{}).expect(201)).body.count,1);
  const unread=await clients.a.agent.get('/api/messages?unread=true').expect(200);assert.equal(unread.body.total,0);assert.equal(unread.body.unread,0);
  const sent=await clients.leader.agent.get('/api/messages/sent?page=999').expect(200);assert.equal(sent.body.total,2);assert.deepEqual(sent.body.items,[]);
  assert.equal((await clients.b.agent.get('/api/messages').expect(200)).body.total,0);
  await db.query('UPDATE bd_team_member SET end_date=\'2026-01-02\' WHERE team_id=$1 AND person_code=$2',[team,codes[1]]);
  await post('leader','/api/messages/send',{title:'离队以后',mode:'pick',codes:[codes[1]]}).expect(403);
 }finally{
  await db.query("DELETE FROM bd_log WHERE domain='message' AND ref_id=ANY($1::int[])",[messages]);await db.query('DELETE FROM bd_message_recipient WHERE message_id=ANY($1::int[])',[messages]);await db.query('DELETE FROM bd_message WHERE id=ANY($1::int[])',[messages]);await db.query('DELETE FROM bd_team_member WHERE team_id=$1',[team]);await db.query('DELETE FROM bd_team WHERE id=$1',[team]);await db.query('DELETE FROM users WHERE id=ANY($1::int[])',[users]);await db.query('DELETE FROM persons WHERE code=ANY($1::text[])',[codes]);await app.close();
 }
});
