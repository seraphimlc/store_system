import 'reflect-metadata';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash,randomBytes,randomUUID} from 'node:crypto';
import {mkdtemp,rm,writeFile,readdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import type {AddressInfo} from 'node:net';
import ExcelJS from 'exceljs';
import {Client} from '@modelcontextprotocol/sdk/client/index.js';
import {StreamableHTTPClientTransport} from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import {createApp} from '../src/main';
import {Database} from '../src/database';
import {VisitImportsService} from '../src/visit-imports';
import {ReconciliationService} from '../src/reconciliation';
import {McpUploadService} from '../src/mcp-upload';
import type {McpActor} from '../src/mcp';
import {DurableQueue} from '../../worker/src/queue';
import {spawnLegacyPython} from '../../../scripts/legacy-python.mjs';
const actor:McpActor={uid:1,tokenId:1,role:'admin',scopes:['read','write'],personCode:null,username:'fixture',displayName:'',personName:null};
test('upload checks role and scope before content and rejects unsafe sources/invalid encoding',async()=>{
 const service=new McpUploadService({}as any,{}as any,{}as any),client={}as any;
 for(const a of [{...actor,role:'staff' as const},{...actor,scopes:['read']}])await assert.rejects(service.prepare(client,a,{path:'/etc/passwd'}),{code:'FORBIDDEN_TOOL'});
 await assert.rejects(service.prepare(client,actor,{path:'/etc/passwd'}),{code:'FORBIDDEN_TOOL'});
 await assert.rejects(service.prepare(client,actor,{content_base64:'bad'}),{code:'BAD_REQUEST'});
 await assert.rejects(service.prepare(client,actor,{month:'2026-13'}),{code:'BAD_PARAM'});
});
const url=process.env.NEXT_TEST_DATABASE_URL;
test('schema-only SDK upload: legacy dry-run, choices, post-commit Worker results, duplicate/seal guards and queue/audit failures',{skip:!url},async()=>{
 assert.match(new URL(url!).pathname,/^\/store_test_[0-9]{13}_[a-f0-9]{12}_refactor_p1$/);
 const dir=await mkdtemp(join(tmpdir(),'mcp-upload-')),saved={uploads:process.env.NEXT_UPLOAD_DIRECTORY,jobs:process.env.NEXT_TASK_DIRECTORY,merge:process.env.STORE_AUTO_MERGE_EXACT};process.env.NEXT_UPLOAD_DIRECTORY=join(dir,'uploads');process.env.NEXT_TASK_DIRECTORY=join(dir,'jobs');process.env.STORE_AUTO_MERGE_EXACT='0';
 const app=await createApp({databaseUrl:url!,sessionSecret:'x'.repeat(40),cookieSecure:false,host:'127.0.0.1',port:3000}),db=app.get(Database),visits=app.get(VisitImportsService),recon=app.get(ReconciliationService),clients=new Map<string,Client>(),users:number[]=[],month='2092-10',code='009201';
 (visits as any).queue=new DurableQueue(join(dir,'visit-jobs'));(recon as any).queue=new DurableQueue(join(dir,'recon-jobs'));
 const transaction=db.transaction.bind(db);let failAudit=false;
 db.transaction=fn=>transaction(client=>fn({query:(sql:string,args:any[]=[])=>{if(failAudit&&sql.startsWith('UPDATE mcp_audit_log SET ok=true'))throw Error('fixture audit failure');return client.query(sql,args);}}as any));
 const noModel=async()=>{throw Error('No paid model is allowed in routine fixtures');};
 const baseVisitEnqueue=visits.enqueue.bind(visits),baseReconEnqueue=recon.enqueue.bind(recon);let drain=false,queueFailure=false,committedObserved=false;
 visits.enqueue=async(task)=>{if(queueFailure)throw Error('fixture queue failure');assert.ok((await db.query('SELECT id FROM imports WHERE id=$1',[task.id])).rowCount,'queue publication must occur after commit');committedObserved=true;const result=await baseVisitEnqueue(task);if(drain){const job=await visits.queue.claim();assert.ok(job);const processed=await visits.process(job.payload,noModel as any,noModel as any);await visits.queue.finish(job,processed,'ok'in processed&&!processed.ok);}return result;};
 recon.enqueue=async(task)=>{if(queueFailure)throw Error('fixture recon queue failure');assert.ok((await db.query('SELECT id FROM recon_tasks WHERE id=$1',[task.id])).rowCount);committedObserved=true;const result=await baseReconEnqueue(task);if(drain){const job=await recon.queue.claim();assert.ok(job);const processed=await recon.process(job.payload,noModel as any);await recon.queue.finish(job,processed,!processed.ok);}return result;};
 const book=async(name:string,headers:string[],rows:unknown[][])=>{const workbook=new ExcelJS.Workbook(),sheet=workbook.addWorksheet(name);sheet.addRow(headers);sheet.addRows(rows);return Buffer.from(await workbook.xlsx.writeBuffer());};
 const call=async(role:string,args:Record<string,unknown>)=>{const r=await clients.get(role)!.callTool({name:'visit_upload',arguments:args});return JSON.parse((r.content as {text:string}[])[0].text);};
 const counts=async()=>({imports:(await db.query('SELECT count(*)::int AS n FROM imports')).rows[0].n,tasks:(await db.query('SELECT count(*)::int AS n FROM recon_tasks')).rows[0].n,formal:(await db.query('SELECT count(*)::int AS n FROM formal_records')).rows[0].n});
 try{
  await db.query('INSERT INTO persons(code,display_name,created_at) VALUES($1,\'fixture employee\',now())',[code]);
  for(const role of ['admin','staff'])users.push((await db.query(`INSERT INTO users(username,password_hash,display_name,person_code,role,status,is_active,must_change_password,lang,created_at) VALUES($1,'fixture-only',$1,$2,$3,'active',true,false,'zh',now()) RETURNING id`,['mcp-upload-'+role,role==='staff'?code:null,role])).rows[0].id);
  await app.listen(0,'127.0.0.1');const endpoint=new URL(`http://127.0.0.1:${(app.getHttpServer().address()as AddressInfo).port}/mcp`);
  for(const [name,index,scopes]of [['admin',0,'read,write'],['read',0,'read'],['staff',1,'read,write']]as const){const raw=randomBytes(32).toString('base64url');await db.query("INSERT INTO api_tokens(user_id,name,token_prefix,token_hash,scopes,created_at) VALUES($1,'fixture',$2,$3,$4,now())",[users[index],raw.slice(0,8),createHash('sha256').update(raw).digest('hex'),scopes]);const c=new Client({name:'upload-fixture',version:'1'});clients.set(name,c);await c.connect(new StreamableHTTPClientTransport(endpoint,{requestInit:{headers:{Authorization:`Bearer ${raw}`}}}));}
  const visitHeaders=['Store ID','Store Name-Local','Modified Time','Submitter','Review status','NEW A+ POSM','Store Address-Local'];
  const data=await book('STORE_TASK_EXCEL_SHEET',visitHeaders,[['MCP-UP-S1','fixture store',month+'-01 10:00:00','fixture employee('+code+')','AUDIT_SUCCESS','YES','東京都新宿区西新宿2-8-1']]);
  const rowRecon=await book('对账',['Statement Date','Store Basic ID','Agent Name','Store Total Amount'],[[month+'-01','MCP-UP-S1','fixture employee('+code+')',2]]);
  const manual=await book('月度总览',['姓名','金额'],[['fixture employee',500]]),unknown=await book('Sheet1',['a','b'],[['x','y']]),noDate=await book('对账',['Agent Name','ISO PID','点数'],[['fixture employee','x',2]]);
  const cases=[{bytes:data},{bytes:rowRecon},{bytes:manual},{bytes:unknown},{bytes:manual,kind:'visit'},{bytes:noDate,kind:'recon',month}];
  const oracleCases=[];for(const [i,c]of cases.entries()){const path=join(dir,'fixture-'+i+'.xlsx');await writeFile(path,c.bytes);oracleCases.push({path,...'kind'in c?{kind:c.kind}:{},...'month'in c?{month:c.month}:{}});}
  const oracle=spawnLegacyPython(['scripts/mcp-upload-oracle.py'],{input:JSON.stringify(oracleCases),encoding:'utf8',timeout:30000});assert.equal(oracle.status,0,oracle.stderr);const expected=JSON.parse(oracle.stdout);
  const initial=await counts();for(const [i,c]of cases.entries()){const result=await call('admin',{filename:'fixture.xlsx',content_base64:c.bytes.toString('base64'),dry_run:true,...'kind'in c?{kind:c.kind}:{},...'month'in c?{month:c.month}:{}});assert.equal(result.ok,true,JSON.stringify(result));for(const key of ['detected','month','estimated_rows','estimated_persons'])assert.deepEqual(result.data[key],expected[i][key],key);}
  assert.deepEqual(await counts(),initial,'dry_run must not create uploads/recon/formal records');assert.equal(committedObserved,false,'dry_run cannot enqueue');
  assert.equal((await call('staff',{path:'/etc/passwd'})).error.code,'FORBIDDEN_TOOL');assert.equal((await call('read',{content_base64:data.toString('base64')})).error.code,'FORBIDDEN_TOOL');
  let result=await call('admin',{filename:'unknown.xlsx',content_base64:unknown.toString('base64')});assert.equal(result.error.code,'NEED_FILE_KIND');assert.ok(Array.isArray(result.error.options));assert.deepEqual(result.error.seen.header,['a','b']);
  assert.equal((await call('admin',{filename:'manual.xlsx',content_base64:manual.toString('base64')})).error.code,'MANUAL_SETTLEMENT_FILE');assert.equal((await call('admin',{content_base64:noDate.toString('base64'),kind:'recon'})).error.code,'BAD_PARAM');
  failAudit=true;result=await call('admin',{filename:'visit.xlsx',content_base64:data.toString('base64')});assert.equal(result.error.code,'INTERNAL');failAudit=false;assert.deepEqual(await counts(),initial);assert.equal(committedObserved,false,'audit failure cannot publish any job');
  drain=true;result=await call('admin',{filename:'visit.xlsx',content_base64:data.toString('base64'),wait_seconds:2});assert.equal(result.ok,true,JSON.stringify(result));assert.equal(result.data.processing_status,'done',JSON.stringify(result));assert.equal(result.data.pipeline_ok,true,JSON.stringify(result));assert.equal(result.data.rows_parsed,1);assert.equal((await db.query('SELECT count(*)::int AS n FROM formal_records')).rows[0].n,1);assert.ok(!JSON.stringify(result).includes(dir));
  assert.equal((await call('admin',{filename:'visit.xlsx',content_base64:data.toString('base64')})).error.code,'DUPLICATE_FILE');
  result=await call('admin',{filename:'recon.xlsx',content_base64:rowRecon.toString('base64'),wait_seconds:2});assert.equal(result.ok,true,JSON.stringify(result));assert.equal(result.data.month,month);assert.equal(result.data.status,'done',JSON.stringify(result));assert.equal(result.data.detected.month_inferred,true);const firstTask=result.data.task_id;
  drain=false;const next=await book('对账',['Statement Date','Store Basic ID','Agent Name','Store Total Amount'],[[month+'-02','MCP-UP-S2','fixture employee('+code+')',1]]);result=await call('admin',{filename:'later.xlsx',content_base64:next.toString('base64'),wait_seconds:0});assert.equal(result.data.status,'pending');assert.equal(result.data.is_overwrite,false);assert.equal((await db.query('SELECT params FROM recon_tasks WHERE id=$1',[firstTask])).rows[0].params.replaced_by,undefined,'pending cannot replace successful version');
  const beforeSeal=await counts();await db.query("INSERT INTO sealed_months(month,created_at) VALUES('2092-11',now())");assert.equal((await call('admin',{filename:'sealed.xlsx',content_base64:rowRecon.toString('base64'),kind:'recon',month:'2092-11',wait_seconds:0})).error.code,'MONTH_SEALED');assert.deepEqual(await counts(),beforeSeal);
  queueFailure=true;result=await call('admin',{filename:'queue-failed.xlsx',content_base64:next.toString('base64'),kind:'recon',wait_seconds:0});assert.equal(result.ok,true);assert.equal(result.data.queue_status,'pending_retry');assert.equal((await db.query('SELECT status FROM recon_tasks WHERE id=$1',[result.data.task_id])).rows[0].status,'pending');queueFailure=false;await recon.reconcile();
  const extraVisit=await book('STORE_TASK_EXCEL_SHEET',visitHeaders,[['MCP-UP-QF','queue fixture',month+'-03 10:00:00','fixture employee('+code+')','AUDIT_SUCCESS','YES','東京都新宿区西新宿2-8-1']]);
  const enqueueQueue=visits.queue.enqueue.bind(visits.queue);visits.queue.enqueue=async()=>{throw Error('synthetic filesystem queue failure');};
  let failedId:number;try{result=await call('admin',{filename:'queue-visit.xlsx',content_base64:extraVisit.toString('base64'),wait_seconds:0});assert.equal(result.ok,true);assert.equal(result.data.queue_status,'failed');failedId=result.data.file_id;assert.equal((await db.query('SELECT status FROM imports WHERE id=$1',[failedId])).rows[0].status,'failed');}finally{visits.queue.enqueue=enqueueQueue;}
  drain=true;const retried=await visits.reparse(failedId!,users[0],undefined,randomUUID());assert.equal((await visits.status(retried.jobId)).status,'done','existing retry path recovers a committed upload after queue failure');assert.equal((await db.query('SELECT status FROM imports WHERE id=$1',[failedId!])).rows[0].status,'parsed');
  assert.ok(committedObserved);assert.equal((await db.query('SELECT count(*)::int AS n FROM payroll_payments')).rows[0].n,0);assert.ok(!(await readdir(join(dir,'uploads','mcp-staging'))).length);
 }finally{
  visits.enqueue=baseVisitEnqueue;recon.enqueue=baseReconEnqueue;await Promise.all([...clients.values()].map(c=>c.close()));
  try{
  await db.query('DELETE FROM mcp_audit_log WHERE user_id=ANY($1::int[])',[users]);await db.query('DELETE FROM api_tokens WHERE user_id=ANY($1::int[])',[users]);await db.query("DELETE FROM sealed_months WHERE month IN('2092-10','2092-11')");
  for(const t of ['payroll_settlement_links','payroll_payments','payroll_adjusts','payroll_period_rows','adjust_records','person_daily_stats','month_perf_records'])await db.query(`DELETE FROM ${t} WHERE person_code=$1`,[code]);
  await db.query('DELETE FROM recon_day_rows');await db.query('DELETE FROM recon_data_rows');await db.query('DELETE FROM recon_results');await db.query('DELETE FROM recon_tasks');await db.query('DELETE FROM staff_report_compare_day');await db.query('DELETE FROM staff_report_compare_person');await db.query('DELETE FROM staff_report_analyses');await db.query('DELETE FROM person_stats WHERE submitter_code=$1',[code]);await db.query('DELETE FROM rebuild_snapshots WHERE month=$1',[month]);await db.query('DELETE FROM dash_metrics WHERE month=$1',[month]);await db.query('DELETE FROM formal_records');await db.query('DELETE FROM appeals');await db.query('DELETE FROM store_pairs');await db.query('DELETE FROM store_merge_logs');await db.query('UPDATE store_entities SET master_id=id');await db.query('DELETE FROM store_entities');await db.query('DELETE FROM raw_records');await db.query('UPDATE persons SET first_seen_import_id=NULL WHERE code=$1',[code]);await db.query('DELETE FROM imports');await db.query('DELETE FROM users WHERE id=ANY($1::int[])',[users]);await db.query('DELETE FROM persons WHERE code=$1',[code]);}finally{await app.close();await rm(dir,{recursive:true,force:true});for(const [key,val]of Object.entries({NEXT_UPLOAD_DIRECTORY:saved.uploads,NEXT_TASK_DIRECTORY:saved.jobs,STORE_AUTO_MERGE_EXACT:saved.merge})){if(val===undefined)delete process.env[key];else process.env[key]=val;}}
 }
});
