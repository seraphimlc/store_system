import 'reflect-metadata';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash,randomBytes} from 'node:crypto';
import {mkdtemp,rm,readdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import type {AddressInfo} from 'node:net';
import ExcelJS from 'exceljs';
import {Client} from '@modelcontextprotocol/sdk/client/index.js';
import {StreamableHTTPClientTransport} from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import request from '../node_modules/supertest';
import {createApp} from '../src/main';
import {Database} from '../src/database';
import {McpPayrollExportService} from '../src/mcp-payroll-export';
import {McpReconExportService} from '../src/mcp-recon-export';
import {PayrollExportService} from '../src/payroll-export';
import {ReconciliationInsightService} from '../src/reconciliation-insight';
import type {McpActor} from '../src/mcp';
const actor:McpActor={uid:1,tokenId:1,role:'admin',scopes:['read','write'],personCode:null,username:'fixture',displayName:'',personName:null};
test('MCP exports enforce role/scope, explicit financial confirmation and valid task selection',async()=>{
 const payroll=new McpPayrollExportService({}as any,{}as any,{}as any),recon=new McpReconExportService({}as any,{}as any),client={}as any;
 const cleanup=()=>{};
 for(const a of [{...actor,role:'staff'as const},{...actor,scopes:['read']}])await assert.rejects(payroll.run(client,a,{month:'2094-10',seq:1,dry_run:true},1,cleanup),{code:'FORBIDDEN_TOOL'});
 await assert.rejects(payroll.run(client,actor,{month:'2094-13',seq:1},1,cleanup),{code:'BAD_PARAM'});
 await assert.rejects(payroll.run(client,actor,{month:'2094-10',seq:1,confirm_text:'wrong'},1,cleanup),{code:'CONFIRM_REQUIRED'});
 await assert.rejects(payroll.run(client,actor,{month:'2094-10',seq:1,confirm_text:'确认导出发薪 2094-10 第1期'},1,cleanup),{code:'PAYROLL_PREVIEW_REQUIRED'});
 await assert.rejects(recon.run(client,{...actor,role:'leader'},{task_id:1},1,cleanup),{code:'FORBIDDEN_TOOL'});
 await assert.rejects(recon.run(client,actor,{},1,cleanup),{code:'BAD_PARAM'});
 await assert.rejects(recon.run(client,actor,{task_id:1,kind:'unknown'},1,cleanup),{code:'BAD_PARAM'});
});
const url=process.env.NEXT_TEST_DATABASE_URL;
test('schema-only SDK exports: private XLSX contents, preflight/commit/audit rollback, ledger reuse and authenticated downloads',{skip:!url},async()=>{
 assert.match(new URL(url!).pathname,/^\/store_test_[0-9]{13}_[a-f0-9]{12}_refactor_p1$/);
 const root=await mkdtemp(join(tmpdir(),'mcp-exports-')),previous=process.env.NEXT_TASK_DIRECTORY;process.env.NEXT_TASK_DIRECTORY=root;
 const app=await createApp({databaseUrl:url!,sessionSecret:'x'.repeat(40),cookieSecure:false,host:'127.0.0.1',port:3000}),db=app.get(Database),exports=app.get(PayrollExportService),insight=app.get(ReconciliationInsightService);
 const users:number[]=[],tasks:number[]=[],clients=new Map<string,Client>(),rawTokens=new Map<string,string>(),tokenIds=new Map<string,number>();
 const codes=['MCP-EXP-A','MCP-EXP-B'],month='2094-10';let failAudit=false;
 const transaction=db.transaction.bind(db);db.transaction=fn=>transaction(client=>fn({query:(sql:string,args:any[]=[])=>{if(failAudit&&sql.startsWith('UPDATE mcp_audit_log SET ok=true'))throw Error('fixture audit error with private details');return client.query(sql,args);}}as any));
 const originalBuild=exports.buildInTransaction.bind(exports);
 // Avoid calling any provider. Existing interpretation is a stored text snapshot.
 insight.start=async()=>{throw Error('No AI generation allowed');};let insightReads=0;const originalInsight=insight.readInTransaction.bind(insight);insight.readInTransaction=async(client,id)=>{insightReads++;return originalInsight(client,id);};
 const call=async(role:string,tool:string,args:Record<string,unknown>)=>{const result=await clients.get(role)!.callTool({name:tool,arguments:args});return JSON.parse((result.content as {text:string}[])[0].text);};
 const snapshot=async()=>{const hashes:Record<string,string>={};for(const table of ['payroll_payments','payroll_adjusts','payroll_settlement_links','payroll_paid_marks','payroll_period_rows','recon_tasks','recon_data_rows','recon_results','recon_day_rows'])hashes[table]=(await db.query(`SELECT md5(coalesce(string_agg(row_to_json(t)::text,'' ORDER BY ${table==='payroll_paid_marks'?'month,half':'id'}),'')) AS value FROM ${table} t`)).rows[0].value;return hashes;};
 const artifactNames=async()=>await readdir(join(root,'mcp-exports')).catch(()=>[]as string[]);
 const decode=async(data:any)=>{assert.ok(data.content_base64);const bytes=Buffer.from(data.content_base64,'base64');assert.equal(bytes.length,data.size_bytes);assert.equal(createHash('sha256').update(bytes).digest('hex'),data.sha256);assert.ok(!JSON.stringify({...data,content_base64:undefined}).includes(root));const wb=new ExcelJS.Workbook();await wb.xlsx.load(bytes as any);return {bytes,wb};};
 try{
  for(const code of codes)await db.query('INSERT INTO persons(code,display_name,created_at) VALUES($1,$1,now())',[code]);
  for(const [i,role]of ['admin','admin','staff','leader'].entries())users.push((await db.query(`INSERT INTO users(username,password_hash,display_name,role,status,is_active,must_change_password,lang,created_at) VALUES($1,'fixture-only',$1,$2,'active',true,false,'zh',now()) RETURNING id`,['mcp-export-'+i,role])).rows[0].id);
  await db.query(`INSERT INTO payroll_period_rows(month,person_code,half1_points,half2_points,half1_amount,half2_amount,half1_records,half1_p1,half1_p2,half2_records,half2_p1,half2_p2,updated_at)
    VALUES($1,$2,4,20,1000,5000,3,2,1,10,0,10,now()),($1,$3,8,4,2000,1000,5,2,3,3,2,1,now())`,[month,...codes]);
  const adjust=(await db.query("INSERT INTO payroll_adjusts(source_month,person_code,adjust_amount,settled_amount,remaining,status,updated_at) VALUES('2094-09',$1,-3000,0,-3000,'in_progress',now()) RETURNING id",[codes[0]])).rows[0].id;
  for(const [i,status]of ['done','done','pending'].entries())tasks.push((await db.query("INSERT INTO recon_tasks(kind,status,created_by,created_at,finished_at,params,summary) VALUES('monthly_v3',$1,$2,now(),$3,$4,$5) RETURNING id",[status,users[0],status==='done'?new Date():null,JSON.stringify({month,kind:'person_points',file:'fixture.xlsx',version:i+1}),JSON.stringify(i===1?{compared:2,diff_count:1,sys_only:[{code:codes[1],name:codes[1],points:8}],ai_interpret:{text:'stored interpretation',model:'fixture',at:'2094-10-31T01:02:03Z'}}:{})])).rows[0].id);
  await db.query("UPDATE recon_tasks SET params=params::jsonb||$1::jsonb WHERE id=$2",[JSON.stringify({replaced_by:tasks[1]}),tasks[0]]);
  await db.query("UPDATE recon_tasks SET params=jsonb_set(params::jsonb,'{month}','\"2094-11\"') WHERE id=$1",[tasks[2]]);
  await db.query("INSERT INTO recon_results(task_id,submitter_code,system_value,report_value,diff,status,confirmed,note) VALUES($1,$2,10,8,2,'diff',false,'fixture difference')",[tasks[1],codes[0]]);
  await db.query("INSERT INTO recon_day_rows(task_id,ref_date,person_code,sys_points,rep_points,sys_count,rep_count,diff,side,note,created_at) VALUES($1,'2094-10-01',$2,2,1,1,1,1,'both','fixture day',now())",[tasks[1],codes[0]]);
  await db.query("INSERT INTO recon_data_rows(task_id,ref_date,person_code,person_name,points,cnt,created_at) VALUES($1,'2094-10-01',$2,'fixture name',8,4,now())",[tasks[1],codes[0]]);
  await app.listen(0,'127.0.0.1');const origin=`http://127.0.0.1:${(app.getHttpServer().address()as AddressInfo).port}`,endpoint=new URL(origin+'/mcp');
  for(const [role,i,scopes]of [['admin',0,'read,write'],['same-read',0,'read'],['other',1,'read,write'],['staff',2,'read,write'],['leader',3,'read,write']]as const){const token=randomBytes(32).toString('base64url');rawTokens.set(role,token);const tokenId=(await db.query("INSERT INTO api_tokens(user_id,name,token_prefix,token_hash,scopes,created_at) VALUES($1,'fixture',$2,$3,$4,now()) RETURNING id",[users[i],token.slice(0,8),createHash('sha256').update(token).digest('hex'),scopes])).rows[0].id;tokenIds.set(role,tokenId);const client=new Client({name:'exports-fixture',version:'1'});clients.set(role,client);await client.connect(new StreamableHTTPClientTransport(endpoint,{requestInit:{headers:{Authorization:`Bearer ${token}`}}}));}
  const allTools=(await clients.get('admin')!.listTools()).tools;assert.equal(allTools.length,27);assert.equal(new Set(allTools.map(tool=>tool.name)).size,27);
  assert.equal((await clients.get('staff')!.listTools()).tools.length,6);assert.equal((await clients.get('leader')!.listTools()).tools.length,10);
  for(const tool of ['visit_payroll_export','visit_recon_export'])assert.ok(allTools.some(item=>item.name===tool));
  const base=await snapshot();
  assert.equal((await call('same-read','visit_payroll_export',{month,seq:1,dry_run:true})).error.code,'FORBIDDEN_TOOL');
  assert.equal((await call('staff','visit_payroll_export',{month,seq:1})).error.code,'FORBIDDEN_TOOL');
  assert.equal((await call('staff','visit_recon_export',{task_id:tasks[1]})).error.code,'FORBIDDEN_TOOL');
  let result=await call('admin','visit_payroll_export',{month,seq:1,dry_run:true});assert.equal(result.ok,true,JSON.stringify(result));assert.equal(result.data.ledger.registered,0);const first=result.data;let file=await decode(first);
  const sheet=file.wb.worksheets[0];assert.equal(sheet.getCell('A2').value,codes[0]);assert.equal(sheet.getCell('H2').value,1000);assert.equal(sheet.getCell('I2').value,-3000);assert.equal(sheet.getCell('J2').value,0);assert.equal(sheet.getCell('J3').value,2000);assert.deepEqual(await snapshot(),base);
  const download=async(data:any,role?:string)=>fetch(origin+data.download_url,{headers:role?{Authorization:'Bearer '+rawTokens.get(role)}:{}});
  assert.equal((await download(first)).status,401);assert.equal((await download(first,'staff')).status,403);assert.equal((await download(first,'other')).status,404);
  let response=await download(first,'same-read');assert.equal(response.status,200);assert.equal(response.headers.get('cache-control'),'no-store');assert.ok(Buffer.from(await response.arrayBuffer()).equals(file.bytes));
  const pendingDownload=request(app.getHttpServer());await pendingDownload.get('/mcp/exports/00000000-0000-4000-8000-000000000000').set('Authorization','Bearer '+rawTokens.get('admin')).expect(404);
  const args={month,seq:1,token:first.token,confirm_text:`确认导出发薪 ${month} 第1期`};
  const beforeFiles=await artifactNames();failAudit=true;result=await call('admin','visit_payroll_export',args);failAudit=false;assert.equal(result.error.code,'INTERNAL');assert.deepEqual(await snapshot(),base);assert.deepEqual(await artifactNames(),beforeFiles,'audit rollback removes private draft');
  exports.buildInTransaction=async()=>{throw Error('fixture workbook failure');};try{result=await call('admin','visit_payroll_export',args);assert.equal(result.error.code,'INTERNAL');assert.deepEqual(await snapshot(),base);}finally{exports.buildInTransaction=originalBuild;}
  await db.query('UPDATE payroll_period_rows SET half1_amount=1001 WHERE month=$1 AND person_code=$2',[month,codes[0]]);result=await call('admin','visit_payroll_export',args);assert.equal(result.error.code,'PAYROLL_PREVIEW_CHANGED');await db.query('UPDATE payroll_period_rows SET half1_amount=1000 WHERE month=$1 AND person_code=$2',[month,codes[0]]);
  result=await call('admin','visit_payroll_export',args);assert.equal(result.ok,true,JSON.stringify(result));assert.equal(result.data.ledger.registered,2);await decode(result.data);const paid=await snapshot();
  assert.equal((await db.query('SELECT remaining FROM payroll_adjusts WHERE id=$1',[adjust])).rows[0].remaining,-2000);
  const fresh=await call('admin','visit_payroll_export',{month,seq:1,dry_run:true});result=await call('admin','visit_payroll_export',{...args,token:fresh.data.token});assert.equal(result.ok,true);assert.equal(result.data.ledger.registered,0);assert.equal(result.data.ledger.reused,2);assert.deepEqual(await snapshot(),paid);
  const half2=await call('admin','visit_payroll_export',{month,seq:2,dry_run:true});file=await decode(half2.data);assert.equal(file.wb.worksheets[0].getCell('I2').value,-2000);assert.equal(file.wb.worksheets[0].getCell('J2').value,3000);assert.deepEqual(await snapshot(),paid);
  for(const kind of ['report','detail','diff']){
   result=await call('same-read','visit_recon_export',{month,kind});assert.equal(result.ok,true,JSON.stringify(result));assert.equal(result.data.task_id,tasks[1]);file=await decode(result.data);
   const names=file.wb.worksheets.map(s=>s.name);assert.deepEqual(names,kind==='report'?['月度差异','逐日问题','对账原始汇总','系统有报表无','AI解读']:kind==='detail'?['对账原始汇总']:['月度差异','逐日问题']);
   const main=file.wb.worksheets[0];if(kind==='detail'){assert.equal(main.getCell('B2').value,codes[0]);assert.equal(main.getCell('D2').value,8);}else{assert.equal(main.getCell('A2').value,codes[0]);assert.equal(main.getCell('D2').value,2);}
   if(kind==='report')assert.equal(file.wb.getWorksheet('AI解读')!.getCell('A2').value,'stored interpretation');
   response=await download(result.data,'admin');assert.equal(response.status,200);await response.arrayBuffer();assert.deepEqual(await snapshot(),paid);
  }
  assert.ok(insightReads>0,'export reads stored interpretation without generating it');
  assert.equal((await call('admin','visit_recon_export',{task_id:tasks[2]})).error.code,'RECON_NOT_DONE');assert.equal((await call('admin','visit_recon_export',{task_id:2147483647})).error.code,'NOT_FOUND');
  result=await call('admin','visit_recon_export',{task_id:tasks[0],kind:'diff'});assert.equal(result.ok,true);file=await decode(result.data);assert.equal(file.wb.worksheets[0].rowCount,1,'explicit historical task exports its own snapshot');
  const lastFiles=await artifactNames();failAudit=true;result=await call('admin','visit_recon_export',{task_id:tasks[1]});failAudit=false;assert.equal(result.error.code,'INTERNAL');assert.deepEqual(await artifactNames(),lastFiles);assert.deepEqual(await snapshot(),paid);
  await db.query('UPDATE api_tokens SET revoked_at=now() WHERE id=$1',[tokenIds.get('same-read')]);assert.equal((await download(first,'same-read')).status,401);
  await db.query("UPDATE users SET status='disabled',is_active=false WHERE id=$1",[users[0]]);assert.equal((await download(first,'admin')).status,401);
 }finally{
  exports.buildInTransaction=originalBuild;await Promise.all([...clients.values()].map(c=>c.close()));try{
   await db.query('DELETE FROM mcp_audit_log WHERE user_id=ANY($1::int[])',[users]);await db.query('DELETE FROM api_tokens WHERE user_id=ANY($1::int[])',[users]);
   await db.query('DELETE FROM payroll_settlement_links WHERE person_code=ANY($1::text[])',[codes]);await db.query('DELETE FROM payroll_payments WHERE person_code=ANY($1::text[])',[codes]);await db.query('DELETE FROM payroll_paid_marks WHERE month=$1',[month]);await db.query('DELETE FROM payroll_adjusts WHERE person_code=ANY($1::text[])',[codes]);await db.query('DELETE FROM payroll_period_rows WHERE person_code=ANY($1::text[])',[codes]);
   for(const table of ['recon_results','recon_day_rows','recon_data_rows'])await db.query(`DELETE FROM ${table} WHERE task_id=ANY($1::int[])`,[tasks]);await db.query('DELETE FROM recon_tasks WHERE id=ANY($1::int[])',[tasks]);await db.query('DELETE FROM users WHERE id=ANY($1::int[])',[users]);await db.query('DELETE FROM persons WHERE code=ANY($1::text[])',[codes]);
  }finally{await app.close();await rm(root,{recursive:true,force:true});if(previous===undefined)delete process.env.NEXT_TASK_DIRECTORY;else process.env.NEXT_TASK_DIRECTORY=previous;}
 }
});
