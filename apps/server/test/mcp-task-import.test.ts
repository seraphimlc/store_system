import 'reflect-metadata';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash,randomBytes} from 'node:crypto';
import {mkdtemp,rm,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import type {AddressInfo} from 'node:net';
import ExcelJS from 'exceljs';
import {Client} from '@modelcontextprotocol/sdk/client/index.js';
import {StreamableHTTPClientTransport} from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import {createApp} from '../src/main';
import {Database} from '../src/database';
import {McpTaskImportService} from '../src/mcp-task-import';
import type {McpActor} from '../src/mcp';
import {buildTaskPlan} from '../../../packages/domain/src/task-plan-import';
import {spawnLegacyPython} from '../../../scripts/legacy-python.mjs';
const actor:McpActor={uid:1,tokenId:1,role:'admin',scopes:['read','write'],personCode:null,username:'fixture',displayName:'',personName:null};
test('task import validates scope, safe file sources, explicit team and zero-based station column',async()=>{
 const service=new McpTaskImportService({}as any),client={}as any;
 await assert.rejects(service.run(client,{...actor,role:'staff'},{}),{code:'FORBIDDEN_TOOL'});
 await assert.rejects(service.run(client,{...actor,scopes:['read']},{}),{code:'FORBIDDEN_TOOL'});
 await assert.rejects(service.run(client,actor,{path:'/etc/passwd'}),{code:'FORBIDDEN_TOOL'});
 await assert.rejects(service.run(client,actor,{content_base64:'invalid'}),{code:'BAD_REQUEST'});
 await assert.rejects(service.run(client,actor,{blob_sha:'../bad'}),{code:'BAD_PARAM'});
 const assets={stations:[{placeId:1,name:'保土ヶ谷',line:'東海道線',operatorShort:'JR'}],teams:[{id:1,name:'A队',status:'active'}],tasks:[]};
 const plan=buildTaskPlan([['车站','负责队'],['保土ケ谷','A队']],assets);assert.equal(plan.layout.cols.station,0);assert.equal(plan.summary.create_stations,1);
 assert.throws(()=>buildTaskPlan([['车站','负责队'],['保土ケ谷','unknown']],assets),/负责队/);
 assert.equal(buildTaskPlan([['车站','负责队'],['保土ケ谷/不存在','A队']],assets).summary.bad,1,'One missing station makes the whole group bad');
});
const url=process.env.NEXT_TEST_DATABASE_URL;
test('schema-only SDK task-plan import: legacy preview parity, ownership, invalid rows, stale plan, atomic apply and existing tasks untouched',{skip:!url},async()=>{
 assert.match(new URL(url!).pathname,/^\/store_test_[0-9]{13}_[a-f0-9]{12}_refactor_p1$/);
 const dir=await mkdtemp(join(tmpdir(),'mcp-task-import-')),oldDirectory=process.env.NEXT_UPLOAD_DIRECTORY;process.env.NEXT_UPLOAD_DIRECTORY=dir;
 const app=await createApp({databaseUrl:url!,sessionSecret:'x'.repeat(40),cookieSecure:false,host:'127.0.0.1',port:3000}),db=app.get(Database),users:number[]=[],teams:number[]=[],lines:number[]=[],places:number[]=[],stations:number[]=[],clients=new Map<string,Client>();let existing:number|undefined;
 const transaction=db.transaction.bind(db);let failAudit=false;
 db.transaction=fn=>transaction(client=>fn({query:(sql:string,values:any[]=[])=>{if(failAudit&&sql.startsWith('UPDATE mcp_audit_log SET ok=true'))throw Error('fixture import audit failure');return client.query(sql,values);}}as any));
 const call=async(role:string,args:Record<string,unknown>)=>{const r=await clients.get(role)!.callTool({name:'visit_task_import',arguments:args});return JSON.parse((r.content as {text:string}[])[0].text);};
 const state=async()=>{const output:Record<string,string>={};for(const table of ['bd_task','bd_task_assign','bd_task_progress','bd_station','bd_station_place','bd_line','bd_log'])output[table]=(await db.query(`SELECT md5(coalesce(string_agg(row_to_json(r)::text,'' ORDER BY id),'')) AS hash FROM ${table} r`)).rows[0].hash;return output;};
 try{
  for(const [i,role]of ['admin','admin','staff'].entries())users.push((await db.query(`INSERT INTO users(username,password_hash,display_name,role,status,is_active,must_change_password,lang,created_at) VALUES($1,'fixture-only',$1,$2,'active',true,false,'zh',now()) RETURNING id`,['mcp-import-'+i,role])).rows[0].id);
  for(const name of ['A队','B队'])teams.push((await db.query("INSERT INTO bd_team(name,status,created_at,updated_at) VALUES($1,'active',now(),now()) RETURNING id",[name])).rows[0].id);
  for(const name of ['東海道線','別線'])lines.push((await db.query("INSERT INTO bd_line(name,name_norm,operator,operator_short,created_at,updated_at) VALUES($1,$1,'JR','JR',now(),now()) RETURNING id",[name])).rows[0].id);
  for(const [i,name]of ['保土ヶ谷','獨協大学前','既存','同名','同名','未使用'].entries()){
   const place=(await db.query('INSERT INTO bd_station_place(name,name_norm,created_at,updated_at) VALUES($1,$1,now(),now()) RETURNING id',[name])).rows[0].id;places.push(place);
   stations.push((await db.query('INSERT INTO bd_station(name,name_norm,line,line_id,place_id,created_at,updated_at) VALUES($1,$1,$2,$3,$4,now(),now()) RETURNING id',[name,i===4?'別線':'東海道線',lines[i===4?1:0],place])).rows[0].id);
  }
  existing=(await db.query("INSERT INTO bd_task(place_id,team_id,state,pct,created_at,updated_at) VALUES($1,$2,'doing',50,now(),now()) RETURNING id",[places[2],teams[1]])).rows[0].id;
  const workbook=new ExcelJS.Workbook(),sheet=workbook.addWorksheet('计划');sheet.addRow(['计划说明']);sheet.addRow(['线路','车站／站群','负责队','任务来源','状态']);sheet.addRows([
   ['東海道線','保土ケ谷／獨協大学前〈草加松原〉','A队','',''],['東海道線','既存','A队','',''],['','不存在','A队','',''],['','同名','A队','',''],['東海道線','保土ヶ谷','A队','','已完成'],['東海道線','未使用','A队','原任务保留',''],['東海道線','保土ヶ谷','unknown','','']]);
  const padding=workbook.addWorksheet('非活动页');for(let i=0;i<240;i++)padding.addRow([randomBytes(800).toString('hex')]);
  const buffer=Buffer.from(await workbook.xlsx.writeBuffer());assert.ok(buffer.toString('base64').length>100*1024,'SDK request must cover files exceeding browser JSON defaults');const file=join(dir,'synthetic-plan.xlsx');await writeFile(file,buffer);
  await app.listen(0,'127.0.0.1');const endpoint=new URL(`http://127.0.0.1:${(app.getHttpServer().address()as AddressInfo).port}/mcp`);
  for(const [name,i,scope]of [['admin',0,'read,write'],['other',1,'read,write'],['read',0,'read'],['staff',2,'read,write']]as const){const raw=randomBytes(32).toString('base64url');await db.query("INSERT INTO api_tokens(user_id,name,token_prefix,token_hash,scopes,created_at) VALUES($1,'fixture',$2,$3,$4,now())",[users[i],raw.slice(0,8),createHash('sha256').update(raw).digest('hex'),scope]);const client=new Client({name:'plan-fixture',version:'1'});clients.set(name,client);await client.connect(new StreamableHTTPClientTransport(endpoint,{requestInit:{headers:{Authorization:`Bearer ${raw}`}}}));}
  assert.equal((await call('staff',{path:'/etc/passwd'})).error.code,'FORBIDDEN_TOOL');assert.equal((await call('read',{content_base64:buffer.toString('base64')})).error.code,'FORBIDDEN_TOOL');
  const before=await state(),preview=await call('admin',{content_base64:buffer.toString('base64'),filename:'plan.xlsx'});assert.equal(preview.ok,true,JSON.stringify(preview));assert.equal(preview.data.applied,false);assert.deepEqual(await state(),before);
  const oracle=spawnLegacyPython(['scripts/mcp-task-import-oracle.py'],{env:{DATABASE_URL:url!,NEXT_TEST_DATABASE_URL:url!},input:JSON.stringify([{path:file}]),encoding:'utf8',timeout:30000});assert.equal(oracle.status,0,oracle.stderr);const old=JSON.parse(oracle.stdout)[0];for(const key of ['layout','summary','teams_in_file','rows'])assert.deepEqual(preview.data[key],old[key],key);
  const sha=preview.data.blob_sha;assert.equal((await call('other',{blob_sha:sha})).error.code,'NOT_FOUND');assert.equal((await call('admin',{blob_sha:sha,action:'apply'})).error.code,'TASK_IMPORT_REJECTED');assert.deepEqual(await state(),before);
  await db.query('UPDATE bd_team SET name=$1 WHERE id=$2',['A队 updated',teams[0]]);let result=await call('admin',{blob_sha:sha,action:'apply',skip_rows:[5,6],plan_token:preview.data.plan_token});assert.equal(result.error.code,'TASK_IMPORT_CHANGED');await db.query('UPDATE bd_team SET name=$1 WHERE id=$2',['A队',teams[0]]);
  const fresh=await call('admin',{blob_sha:sha,content_base64:null});const unchanged=await state();failAudit=true;result=await call('admin',{blob_sha:sha,action:'apply',skip_rows:['5',6],plan_token:fresh.data.plan_token});assert.equal(result.error.code,'INTERNAL');failAudit=false;assert.deepEqual(await state(),unchanged,'task creation and dispatch roll back with audit failure');
  result=await call('admin',{blob_sha:sha,action:'apply',skip_rows:[5,6],plan_token:fresh.data.plan_token});assert.equal(result.ok,true,JSON.stringify(result));assert.equal(result.data.created,2);assert.equal(result.data.existing_untouched,1);
  const oldTask=(await db.query('SELECT team_id,pct FROM bd_task WHERE id=$1',[existing])).rows[0];assert.deepEqual(oldTask,{team_id:teams[1],pct:50});assert.equal((await db.query('SELECT count(*)::int AS n FROM bd_station_place')).rows[0].n,6);
  result=await call('admin',{blob_sha:sha,action:'apply',skip_rows:[5,6]});assert.equal(result.data.created,0);assert.equal((await db.query('SELECT count(*)::int AS n FROM bd_task')).rows[0].n,3);
  const manual=new ExcelJS.Workbook();manual.addWorksheet('手动').addRows([['自定义站列','自定义队列'],['未使用','A队']]);const manualBlob=Buffer.from(await manual.xlsx.writeBuffer()).toString('base64');result=await call('admin',{content_base64:manualBlob,filename:'manual.xlsx',header_row:1,cols:{station:0,team:1}});assert.equal(result.ok,true,JSON.stringify(result));assert.equal(result.data.summary.create_stations,1,'zero-based station column must work through the SDK');
  const manualSha=result.data.blob_sha;
  const legacy=(await db.query("INSERT INTO bd_task(station_id,team_id,state,pct,created_at,updated_at) VALUES($1,$2,'doing',75,now(),now()) RETURNING id",[stations[5],teams[1]])).rows[0].id;
  result=await call('admin',{blob_sha:manualSha,header_row:1,cols:{station:0,team:1}});assert.equal(result.data.summary.existing,1);assert.equal(result.data.summary.create_stations,0,'station-linked legacy tasks also count as existing');
  const legacyPlan=result.data.plan_token;result=await call('admin',{action:'apply',blob_sha:manualSha,header_row:1,cols:{station:0,team:1},plan_token:legacyPlan});assert.equal(result.ok,true);assert.equal(result.data.created,0);assert.deepEqual((await db.query('SELECT team_id,pct FROM bd_task WHERE id=$1',[legacy])).rows[0],{team_id:teams[1],pct:75});
  assert.equal((await db.query('SELECT count(*)::int AS n FROM imports')).rows[0].n,0,'plan files never enter payroll import list');assert.equal((await db.query('SELECT count(*)::int AS n FROM payroll_payments')).rows[0].n,0);
 }finally{
  await Promise.all([...clients.values()].map(c=>c.close()));await db.query('DELETE FROM mcp_audit_log WHERE user_id=ANY($1::int[])',[users]);await db.query('DELETE FROM api_tokens WHERE user_id=ANY($1::int[])',[users]);const taskIds=(await db.query('SELECT id FROM bd_task WHERE place_id=ANY($1::int[]) OR station_id IN(SELECT id FROM bd_station WHERE place_id=ANY($1::int[]))',[places])).rows.map(r=>r.id);await db.query("DELETE FROM bd_log WHERE domain='task' AND ref_id=ANY($1::int[])",[taskIds]);await db.query('DELETE FROM bd_task WHERE id=ANY($1::int[])',[taskIds]);await db.query('DELETE FROM bd_station WHERE id=ANY($1::int[])',[stations]);await db.query('DELETE FROM bd_station_place WHERE id=ANY($1::int[])',[places]);await db.query('DELETE FROM bd_line WHERE id=ANY($1::int[])',[lines]);await db.query('DELETE FROM bd_team WHERE id=ANY($1::int[])',[teams]);await db.query('DELETE FROM users WHERE id=ANY($1::int[])',[users]);await app.close();await rm(dir,{recursive:true,force:true});if(oldDirectory===undefined)delete process.env.NEXT_UPLOAD_DIRECTORY;else process.env.NEXT_UPLOAD_DIRECTORY=oldDirectory;
 }
});
