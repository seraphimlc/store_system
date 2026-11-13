import 'reflect-metadata';
import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash, randomBytes } from 'node:crypto';
import type { AddressInfo } from 'node:net';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { createApp } from '../src/main';
import { Database } from '../src/database';
import { StationTasksService } from '../src/station-tasks';
import { McpTaskWriteService } from '../src/mcp-task-write';
import type { McpActor } from '../src/mcp';
import { jstToday } from '../src/business-dates';
import { spawnLegacyPython } from '../../../scripts/legacy-python.mjs';
const actor:McpActor={uid:1,tokenId:1,role:'staff',scopes:['read','write'],personCode:'fixture',username:'fixture',displayName:'',personName:null};
test('task writes enforce scope, administrator day-report boundary and personal identity before queries',async()=>{
 const service=new McpTaskWriteService({}as any,{}as any),client={query:async()=>{throw Error('Unexpected business query');}}as any;
 for(const tool of ['visit_self_report','visit_task_report'])await assert.rejects(service.run(client,{...actor,scopes:['read']},tool,{}),{code:'FORBIDDEN_TOOL'});
 await assert.rejects(service.run(client,actor,'visit_day_report',{action:'save'}),{code:'FORBIDDEN_TOOL'});
 await assert.rejects(service.run(client,actor,'visit_self_report',{person_code:'OTHER'}),{code:'FORBIDDEN_TOOL'});
 await assert.rejects(service.run(client,actor,'visit_task_report',{task_id:1,pct:101}),{code:'BAD_PARAM'});
 await assert.rejects(service.run(client,{...actor,role:'admin'},'visit_day_report',{person:'fixture',date:'2026-02-30'}),{code:'BAD_PARAM'});
});
const url=process.env.NEXT_TEST_DATABASE_URL;
test('schema-only task-write SDK: atomic counts/progress, versions, completion count, review locks and admin backfill', {skip:!url},async()=>{
 assert.match(new URL(url!).pathname,/^\/store_test_[0-9]{13}_[a-f0-9]{12}_refactor_p1$/);
 const app=await createApp({databaseUrl:url!,sessionSecret:'x'.repeat(40),cookieSecure:false,host:'127.0.0.1',port:3000});
 const db=app.get(Database),tasks=app.get(StationTasksService),users:number[]=[],teams:number[]=[],taskIds:number[]=[],clients=new Map<string,Client>();
 const today=jstToday(),codes=['MCP-WORK-L','MCP-WORK-S','MCP-WORK-X'];
 const transaction=db.transaction.bind(db);let failAudit=false;
 db.transaction=fn=>transaction(client=>fn({query:(sql:string,args:any[]=[])=>{if(failAudit&&sql.startsWith('UPDATE mcp_audit_log SET ok=true'))throw Error('synthetic audit failure');return client.query(sql,args);}}as any));
 const call=async(role:string,tool:string,args:Record<string,unknown>={})=>{const r=await clients.get(role)!.callTool({name:tool,arguments:args});return JSON.parse((r.content as {text:string}[])[0].text);};
 const fingerprint=async()=>{const output:Record<string,string>={};for(const table of ['staff_daily_reports','staff_date_plans','bd_task','bd_task_progress','bd_log'])output[table]=(await db.query(`SELECT md5(coalesce(string_agg(row_to_json(r)::text,'' ORDER BY id),'')) AS h FROM ${table} r`)).rows[0].h;return output;};
 try{
  for(const code of codes)await db.query('INSERT INTO persons(code,display_name,created_at) VALUES($1,$1,now())',[code]);
  for(const [index,role]of ['admin','leader','staff','staff'].entries())users.push((await db.query(`INSERT INTO users(username,password_hash,display_name,person_code,role,status,is_active,must_change_password,lang,created_at) VALUES($1,'fixture-hash',$1,$2,$3,'active',true,false,'zh',now()) RETURNING id`,['mcp-work-'+index,index?codes[index-1]:null,role])).rows[0].id);
  for(const name of ['MCP work A','MCP work B'])teams.push((await db.query('INSERT INTO bd_team(name,status,created_at,updated_at) VALUES($1,\'active\',now(),now()) RETURNING id',[name])).rows[0].id);
  for(const [i,code]of codes.entries())await db.query('INSERT INTO bd_team_member(team_id,person_code,role,start_date,created_at) VALUES($1,$2,$3,\'2020-01-01\',now())',[i===2?teams[1]:teams[0],code,i===0?'leader':'member']);
  for(let i=0;i<3;i++){const id=(await db.query("INSERT INTO bd_task(team_id,state,pct,created_at,updated_at) VALUES($1,'doing',0,now(),now()) RETURNING id",[i===2?teams[1]:teams[0]])).rows[0].id;taskIds.push(id);await db.query('INSERT INTO bd_task_assign(task_id,person_code,dispatch_date,assigned_at) VALUES($1,$2,$3,now())',[id,i===2?codes[2]:codes[1],today]);}
  await app.listen(0,'127.0.0.1');const endpoint=new URL(`http://127.0.0.1:${(app.getHttpServer().address()as AddressInfo).port}/mcp`);
  for(const [name,index,scopes]of [['admin',0,'read,write'],['admin-read',0,'read'],['leader',1,'read,write'],['staff',2,'read,write'],['staff-read',2,'read'],['other',3,'read,write']]as const){const raw=randomBytes(32).toString('base64url');await db.query("INSERT INTO api_tokens(user_id,name,token_prefix,token_hash,scopes,created_at) VALUES($1,'fixture',$2,$3,$4,now())",[users[index],raw.slice(0,8),createHash('sha256').update(raw).digest('hex'),scopes]);const client=new Client({name:'work-fixture',version:'1'});clients.set(name,client);await client.connect(new StreamableHTTPClientTransport(endpoint,{requestInit:{headers:{Authorization:`Bearer ${raw}`}}}));}
  assert.equal((await call('staff-read','visit_self_report',{p1_cnt:1})).error.code,'FORBIDDEN_TOOL');
  assert.equal((await call('staff','visit_day_report',{person:codes[1]})).error.code,'FORBIDDEN_TOOL');
  let before=await fingerprint();
  let result=await call('staff','visit_self_report',{area:'fixture',p1_cnt:2,p2_cnt:1,items:[{task_id:taskIds[0],pct:40},{task_id:taskIds[1],pct:100}]});assert.equal(result.error.code,'STORE_COUNT_REQUIRED');assert.deepEqual(await fingerprint(),before);
  result=await call('staff','visit_self_report',{area:'fixture',p1_cnt:2,p2_cnt:1,items:[{task_id:taskIds[0],pct:40},{task_id:taskIds[1],pct:0}]});assert.equal(result.ok,true,JSON.stringify(result));assert.equal(result.data.tasks_reported,1);assert.equal(result.data.tasks_skipped,1);
  result=await call('staff','visit_self_report',{area:'fixture',p1_cnt:2,p2_cnt:1,items:[{task_id:taskIds[0],pct:40}]});assert.equal(result.data.tasks_reported,0);assert.equal(result.data.tasks_skipped,1);assert.equal((await db.query('SELECT count(*)::int AS n FROM staff_daily_reports WHERE person_code=$1',[codes[1]])).rows[0].n,1);assert.equal((await db.query('SELECT count(*)::int AS n FROM bd_task_progress WHERE task_id=$1',[taskIds[0]])).rows[0].n,1);
  const view=await call('admin-read','visit_day_report',{person:codes[1],include_version:true});assert.equal(view.ok,true);assert.equal(view.data.report.p1_cnt,2);assert.equal(view.data.progress.length,1);assert.equal(view.data.progress[0].editable,false);
  result=await call('staff','visit_task_report',{task_id:taskIds[2],pct:50});assert.equal(result.error.code,'FORBIDDEN');
  const snapshot=await tasks.detail(taskIds[1],users[2]);
  result=await call('staff','visit_task_report',{task_id:taskIds[1],pct:100});assert.equal(result.error.code,'STORE_COUNT_REQUIRED');
  result=await call('staff','visit_task_report',{task_id:taskIds[1],pct:100,store_count:0,token:snapshot.token});assert.equal(result.ok,true,JSON.stringify(result));assert.equal(result.data.store_count,0);
  result=await call('staff','visit_task_report',{task_id:taskIds[1],pct:80,token:snapshot.token});assert.equal(result.error.code,'TASK_CHANGED');
  result=await call('leader','visit_task_report',{task_id:taskIds[0],pct:40,note:'leader review'});assert.equal(result.ok,true,JSON.stringify(result));
  before=await fingerprint();result=await call('staff','visit_task_report',{task_id:taskIds[0],pct:60});assert.ok(['FORBIDDEN','TASK_REVIEW_LOCKED'].includes(result.error.code));assert.deepEqual(await fingerprint(),before);
  const adminView=await call('admin-read','visit_day_report',{person:codes[1],include_version:true});const row=adminView.data.progress.find((r:any)=>r.task_id===taskIds[0]);assert.equal(row.editable,true);
  result=await call('admin','visit_day_report',{person:codes[1],action:'save',area:'admin',p1_cnt:4,p2_cnt:2,token:adminView.data.token,items:[{task_id:taskIds[0],pct:65,token:row.token}]});assert.equal(result.ok,true,JSON.stringify(result));assert.equal(result.data.tasks_changed,1);
  result=await call('admin','visit_day_report',{person:codes[1],action:'save',p1_cnt:9,p2_cnt:0,token:adminView.data.token});assert.equal(result.error.code,'SELF_REPORT_CHANGED');
  const past='2026-01-02';before=await fingerprint();result=await call('admin','visit_day_report',{person:codes[2],date:past,action:'save',p1_cnt:1,p2_cnt:0,items:[{task_id:taskIds[2],pct:10}]});assert.equal(result.error.code,'BACKFILL_POINTS_ONLY');assert.deepEqual(await fingerprint(),before);
  result=await call('admin','visit_day_report',{person:codes[2],date:past,action:'save',p1_cnt:1,p2_cnt:0});assert.equal(result.ok,true,JSON.stringify(result));assert.equal(result.data.created,true);
  failAudit=true;before=await fingerprint();result=await call('staff','visit_self_report',{p1_cnt:7,p2_cnt:0});assert.equal(result.error.code,'INTERNAL');assert.deepEqual(await fingerprint(),before);failAudit=false;
  await db.query('UPDATE bd_team_member SET end_date=$1 WHERE person_code=$2',[today,codes[1]]);result=await call('staff','visit_task_report',{task_id:taskIds[1],pct:20});assert.equal(result.error.code,'FORBIDDEN');
  await db.query("UPDATE users SET status='disabled',is_active=false WHERE id=$1",[users[2]]);await assert.rejects(clients.get('staff')!.listTools(),{code:401});
  const parityCases=[{tool:'visit_day_report',personCode:codes[1],args:{date:today}},{tool:'visit_day_report',personCode:codes[2],args:{date:past}},{tool:'visit_day_report',personCode:codes[0],args:{date:past}}];
  const oracle=spawnLegacyPython(['scripts/mcp-task-read-oracle.py'],{env:{DATABASE_URL:url!,NEXT_TEST_DATABASE_URL:url!},input:JSON.stringify({today,cases:parityCases}),encoding:'utf8',timeout:30000});assert.equal(oracle.status,0,oracle.stderr);const old=JSON.parse(oracle.stdout);
  for(let i=0;i<parityCases.length;i++){const c=parityCases[i];const current=await call('admin-read','visit_day_report',{person:c.personCode,date:c.args.date});assert.deepEqual(current.data,old[i]);}
  assert.equal((await db.query('SELECT count(*)::int AS n FROM payroll_payments')).rows[0].n,0);assert.equal((await db.query('SELECT count(*)::int AS n FROM month_perf_records')).rows[0].n,0);
 }finally{
  await Promise.all([...clients.values()].map(c=>c.close()));await db.query('DELETE FROM mcp_audit_log WHERE user_id=ANY($1::int[])',[users]);await db.query('DELETE FROM api_tokens WHERE user_id=ANY($1::int[])',[users]);
  await db.query('DELETE FROM bd_task_progress WHERE task_id=ANY($1::int[])',[taskIds]);await db.query('DELETE FROM bd_task_assign WHERE task_id=ANY($1::int[])',[taskIds]);await db.query('DELETE FROM bd_log WHERE domain=\'task\' AND ref_id=ANY($1::int[])',[taskIds]);await db.query('DELETE FROM bd_task WHERE id=ANY($1::int[])',[taskIds]);await db.query('DELETE FROM staff_daily_reports WHERE person_code=ANY($1::text[])',[codes]);await db.query('DELETE FROM staff_date_plans WHERE person_code=ANY($1::text[])',[codes]);await db.query('DELETE FROM bd_team_member WHERE team_id=ANY($1::int[])',[teams]);await db.query('DELETE FROM bd_team WHERE id=ANY($1::int[])',[teams]);await db.query('DELETE FROM users WHERE id=ANY($1::int[])',[users]);await db.query('DELETE FROM persons WHERE code=ANY($1::text[])',[codes]);await app.close();
 }
});
