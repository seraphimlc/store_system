import 'reflect-metadata';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash,randomBytes} from 'node:crypto';
import type {AddressInfo} from 'node:net';
import {Client} from '@modelcontextprotocol/sdk/client/index.js';
import {StreamableHTTPClientTransport} from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import {createApp} from '../src/main';
import {Database} from '../src/database';
import {StationTasksService} from '../src/station-tasks';
import {McpTaskWriteService} from '../src/mcp-task-write';
import type {McpActor} from '../src/mcp';
import {jstToday} from '../src/business-dates';
const actor:McpActor={uid:1,tokenId:1,role:'staff',scopes:['read','write'],personCode:'fixture',username:'fixture',displayName:'',personName:null};
test('task management rejects staff, leader pool return and read-only credentials before business operations',async()=>{
 const service=new McpTaskWriteService({}as any,{}as any),client={query:async()=>{throw Error('Unexpected query');}}as any;
 for(const tool of ['visit_task_assign','visit_task_transfer','visit_task_confirm','visit_task_return']){
  await assert.rejects(service.run(client,actor,tool,{}),{code:'FORBIDDEN_TOOL'});
  await assert.rejects(service.run(client,{...actor,role:'admin',scopes:['read']},tool,{}),{code:'FORBIDDEN_TOOL'});
 }
 await assert.rejects(service.run(client,{...actor,role:'leader'},'visit_task_return',{}),{code:'FORBIDDEN_TOOL'});
});
const url=process.env.NEXT_TEST_DATABASE_URL;
test('schema-only management SDK: assignment, atomic transfer/messages, confirm/reject, pool return and live team guards',{skip:!url},async()=>{
 assert.match(new URL(url!).pathname,/^\/store_test_[0-9]{13}_[a-f0-9]{12}_refactor_p1$/);
 const app=await createApp({databaseUrl:url!,sessionSecret:'x'.repeat(40),cookieSecure:false,host:'127.0.0.1',port:3000}),db=app.get(Database),service=app.get(StationTasksService);
 const users:number[]=[],teams:number[]=[],tasks:number[]=[],clients=new Map<string,Client>(),codes=['MCP-MOVE-LA','MCP-MOVE-SA','MCP-MOVE-LB','MCP-MOVE-SB'];
 const today=jstToday();const transaction=db.transaction.bind(db);let failAudit=false;
 db.transaction=fn=>transaction(client=>fn({query:(sql:string,values:any[]=[])=>{if(failAudit&&sql.startsWith('UPDATE mcp_audit_log SET ok=true'))throw Error('audit fixture failure');return client.query(sql,values);}}as any));
 const call=async(name:string,tool:string,args:Record<string,unknown>)=>{const r=await clients.get(name)!.callTool({name:tool,arguments:args});return JSON.parse((r.content as {text:string}[])[0].text);};
 const snapshot=async()=>{const out:Record<string,string>={};for(const table of ['bd_task','bd_task_assign','bd_task_progress','bd_log','bd_message','bd_message_recipient'])out[table]=(await db.query(`SELECT md5(coalesce(string_agg(row_to_json(r)::text,'' ORDER BY id),'')) AS hash FROM ${table} r`)).rows[0].hash;return out;};
 try{
  for(const code of codes)await db.query('INSERT INTO persons(code,display_name,created_at) VALUES($1,$1,now())',[code]);
  for(const [i,role]of ['admin','leader','staff','leader','staff'].entries())users.push((await db.query(`INSERT INTO users(username,password_hash,display_name,person_code,role,status,is_active,must_change_password,lang,created_at) VALUES($1,'fixture-only',$1,$2,$3,'active',true,false,'zh',now()) RETURNING id`,['mcp-move-'+i,i?codes[i-1]:null,role])).rows[0].id);
  for(const [i,name]of ['move A','move B','closed'].entries())teams.push((await db.query('INSERT INTO bd_team(name,status,created_at,updated_at) VALUES($1,$2,now(),now()) RETURNING id',[name,i===2?'closed':'active'])).rows[0].id);
  for(const [i,code]of codes.entries())await db.query("INSERT INTO bd_team_member(team_id,person_code,role,start_date,created_at) VALUES($1,$2,$3,'2020-01-01',now())",[teams[i<2?0:1],code,i%2===0?'leader':'member']);
  for(let i=0;i<8;i++){
   const pct=i===4||i===6?100:i===0?40:0;const id=(await db.query("INSERT INTO bd_task(team_id,state,pct,assign_date,created_at,updated_at) VALUES($1,$2,$3,$4,now(),now()) RETURNING id",[teams[i===3?1:0],pct===100?'done':i===2||i===5?'unassigned':'doing',pct,today])).rows[0].id;tasks.push(id);
   if(![2,5].includes(i))await db.query('INSERT INTO bd_task_assign(task_id,person_code,dispatch_date,assigned_at) VALUES($1,$2,$3,now())',[id,codes[i===3?3:1],today]);
   if([0,4,6].includes(i))await db.query("INSERT INTO bd_task_progress(task_id,progress_date,pct,reported_pct,reported_by,review_status,created_at,updated_at) VALUES($1,$2,$3,$3,'mcp-move-2',$4,now(),now())",[id,today,pct,i===4?'confirmed':'pending']);
  }
  await app.listen(0,'127.0.0.1');const endpoint=new URL(`http://127.0.0.1:${(app.getHttpServer().address()as AddressInfo).port}/mcp`);
  for(const [name,index,scopes]of [['admin',0,'read,write'],['leaderA',1,'read,write'],['read',1,'read'],['staffA',2,'read,write'],['leaderB',3,'read,write']]as const){const raw=randomBytes(32).toString('base64url');await db.query("INSERT INTO api_tokens(user_id,name,token_prefix,token_hash,scopes,created_at) VALUES($1,'fixture',$2,$3,$4,now())",[users[index],raw.slice(0,8),createHash('sha256').update(raw).digest('hex'),scopes]);const c=new Client({name:'movement-fixture',version:'1'});clients.set(name,c);await c.connect(new StreamableHTTPClientTransport(endpoint,{requestInit:{headers:{Authorization:`Bearer ${raw}`}}}));}
  let before=await snapshot(),result=await call('read','visit_task_assign',{task_id:tasks[0],person_codes:[]});assert.equal(result.error.code,'FORBIDDEN_TOOL');assert.deepEqual(await snapshot(),before);
  result=await call('leaderA','visit_task_assign',{task_id:tasks[3],person_codes:[]});assert.equal(result.error.code,'FORBIDDEN');
  result=await call('leaderA','visit_task_assign',{task_id:tasks[0],person_codes:[codes[3]]});assert.equal(result.error.code,'ASSIGNEE_UNAVAILABLE');
  const old=(await service.detail(tasks[0],users[1])).token;
  result=await call('leaderA','visit_task_assign',{task_id:tasks[0],person_codes:[],token:old});assert.equal(result.ok,true,JSON.stringify(result));assert.equal(result.data.n_assignees,0);assert.equal((await db.query('SELECT pct FROM bd_task WHERE id=$1',[tasks[0]])).rows[0].pct,40);
  result=await call('leaderA','visit_task_assign',{task_id:tasks[0],person_codes:[codes[1]],token:old});assert.equal(result.error.code,'TASK_CHANGED');
  result=await call('leaderA','visit_task_assign',{task_id:tasks[0],person_codes:[codes[1]]});assert.equal(result.ok,true);
  result=await call('leaderA','visit_task_confirm',{task_id:tasks[0]});assert.equal(result.ok,true,JSON.stringify(result));assert.equal(result.data.pct,40);assert.equal((await db.query('SELECT review_status FROM bd_task_progress WHERE task_id=$1',[tasks[0]])).rows[0].review_status,'confirmed');
  result=await call('leaderA','visit_task_confirm',{task_id:tasks[6],reject:true,pct:80,note:'fixture rejection'});assert.equal(result.ok,true,JSON.stringify(result));let progress=(await db.query('SELECT pct,reported_pct,review_status FROM bd_task_progress WHERE task_id=$1',[tasks[6]])).rows[0];assert.deepEqual(progress,{pct:80,reported_pct:100,review_status:'rejected'});
  await db.query("INSERT INTO bd_task_progress(task_id,progress_date,pct,reported_pct,review_status,created_at,updated_at) VALUES($1,$2,10,10,'pending',now(),now()),($3,$2,20,20,'pending',now(),now())",[tasks[1],today,tasks[3]]);
  result=await call('leaderA','visit_task_confirm',{all_today:true});assert.equal(result.ok,true,JSON.stringify(result));assert.equal(result.data.confirmed,1);assert.equal((await db.query('SELECT review_status FROM bd_task_progress WHERE task_id=$1',[tasks[3]])).rows[0].review_status,'pending');
  before=await snapshot();result=await call('leaderA','visit_task_transfer',{task_ids:[tasks[1],tasks[7]],to_team_id:teams[1],tokens:{[tasks[7]]:'0'.repeat(64)}});assert.equal(result.error.code,'TASK_CHANGED');assert.deepEqual(await snapshot(),before,'later batch failure must roll back earlier task move, removed assignees and notifications');
  result=await call('leaderA','visit_task_transfer',{task_ids:[tasks[1]],to_team_id:teams[2]});assert.equal(result.error.code,'TEAM_UNAVAILABLE');
  before=await snapshot();failAudit=true;result=await call('leaderA','visit_task_transfer',{task_ids:[tasks[7]],to_team_id:teams[1]});assert.equal(result.error.code,'INTERNAL');failAudit=false;assert.deepEqual(await snapshot(),before);
  const originalProgress=(await db.query('SELECT pct,reported_pct,review_status FROM bd_task_progress WHERE task_id=$1',[tasks[1]])).rows;
  result=await call('leaderA','visit_task_transfer',{task_ids:[tasks[1],tasks[4]],to_team_id:teams[1]});assert.equal(result.ok,true,JSON.stringify(result));assert.equal(result.data.transferred,1);assert.deepEqual(result.data.labels,['']);assert.equal(result.data.cleared,1);assert.deepEqual(result.data.removed_people,[codes[1]]);assert.equal((await db.query('SELECT team_id FROM bd_task WHERE id=$1',[tasks[4]])).rows[0].team_id,teams[0]);assert.deepEqual((await db.query('SELECT pct,reported_pct,review_status FROM bd_task_progress WHERE task_id=$1',[tasks[1]])).rows,originalProgress);
  const recipients=(await db.query('SELECT DISTINCT person_code FROM bd_message_recipient ORDER BY person_code')).rows.map(r=>r.person_code);assert.ok(recipients.includes(codes[1]));assert.ok(recipients.includes(codes[2]));
  result=await call('leaderA','visit_task_assign',{task_id:tasks[1],person_codes:[]});assert.equal(result.error.code,'FORBIDDEN');
  result=await call('leaderB','visit_task_assign',{task_id:tasks[1],person_codes:[codes[3]]});assert.equal(result.ok,true,JSON.stringify(result));
  result=await call('admin','visit_task_return',{task_ids:[tasks[0],tasks[2],tasks[5]]});assert.equal(result.ok,true,JSON.stringify(result));assert.equal(result.data.returned,2);assert.deepEqual(result.data.skipped,[{task_id:tasks[0],why:'还有担当，请让队长先回收或改派'}]);
  result=await call('admin','visit_task_return',{task_ids:[tasks[2]]});assert.equal(result.data.returned,0);assert.equal(result.data.skipped[0].why,'还没派给队伍');
  await db.query('UPDATE bd_team_member SET end_date=$1 WHERE person_code=$2',[today,codes[0]]);result=await call('leaderA','visit_task_assign',{task_id:tasks[0],person_codes:[]});assert.equal(result.error.code,'FORBIDDEN');
  assert.equal((await db.query('SELECT count(*)::int AS n FROM payroll_payments')).rows[0].n,0);
 }finally{
  await Promise.all([...clients.values()].map(c=>c.close()));await db.query('DELETE FROM mcp_audit_log WHERE user_id=ANY($1::int[])',[users]);await db.query('DELETE FROM api_tokens WHERE user_id=ANY($1::int[])',[users]);
  await db.query("DELETE FROM bd_message_recipient WHERE message_id IN(SELECT id FROM bd_message WHERE ref_type='task' AND ref_id=ANY($1::int[]))",[tasks]);await db.query("DELETE FROM bd_message WHERE ref_type='task' AND ref_id=ANY($1::int[])",[tasks]);
  await db.query('DELETE FROM bd_task_progress WHERE task_id=ANY($1::int[])',[tasks]);await db.query('DELETE FROM bd_task_assign WHERE task_id=ANY($1::int[])',[tasks]);await db.query("DELETE FROM bd_log WHERE domain='task' AND ref_id=ANY($1::int[])",[tasks]);await db.query('DELETE FROM bd_task WHERE id=ANY($1::int[])',[tasks]);await db.query('DELETE FROM bd_team_member WHERE team_id=ANY($1::int[])',[teams]);await db.query('DELETE FROM bd_team WHERE id=ANY($1::int[])',[teams]);await db.query('DELETE FROM users WHERE id=ANY($1::int[])',[users]);await db.query('DELETE FROM persons WHERE code=ANY($1::text[])',[codes]);await app.close();
 }
});
