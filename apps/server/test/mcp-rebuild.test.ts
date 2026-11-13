import 'reflect-metadata';
import test from 'node:test';import assert from 'node:assert/strict';
import {createHash,randomBytes} from 'node:crypto';import type {AddressInfo} from 'node:net';
import {Client} from '@modelcontextprotocol/sdk/client/index.js';import {StreamableHTTPClientTransport} from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import {createApp} from '../src/main';import {Database} from '../src/database';import {SettlementService} from '../src/settlement';import {McpRebuildService} from '../src/mcp-rebuild';import type {McpActor} from '../src/mcp';
const actor:McpActor={uid:1,tokenId:1,role:'admin',scopes:['read','write'],personCode:null,username:'fixture',displayName:'',personName:null};
test('rebuild checks role, scope, month and confirmation before business queries',async()=>{const service=new McpRebuildService({}as any),client={}as any;
 await assert.rejects(service.run(client,{...actor,role:'staff'},{month:'2026-10'},1),{code:'FORBIDDEN_TOOL'});
 await assert.rejects(service.run(client,{...actor,scopes:['read']},{month:'2026-10',action:'run'},1),{code:'FORBIDDEN_TOOL'});
 await assert.rejects(service.run(client,actor,{month:'2026-13'},1),{code:'BAD_PARAM'});
 await assert.rejects(service.run(client,actor,{month:'2026-10',action:'run'},1),{code:'CONFIRM_REQUIRED'});
});
const url=process.env.NEXT_TEST_DATABASE_URL;
test('schema-only rebuild SDK: preview integrity, token/month/expiry/source/seal guards, atomic recompute and one-use preview',{skip:!url},async()=>{
 assert.match(new URL(url!).pathname,/^\/store_test_[0-9]{13}_[a-f0-9]{12}_refactor_p1$/);
 const app=await createApp({databaseUrl:url!,sessionSecret:'x'.repeat(40),cookieSecure:false,host:'127.0.0.1',port:3000}),db=app.get(Database),settlement=app.get(SettlementService),users:number[]=[],clients=new Map<string,Client>();let imp:number|undefined;
 const month='2093-10',code='009301';const transaction=db.transaction.bind(db);let failAudit=false;
 db.transaction=fn=>transaction(client=>fn({query:(sql:string,args:any[]=[])=>{if(failAudit&&sql.startsWith('UPDATE mcp_audit_log SET ok=true'))throw Error('fixture audit fail');return client.query(sql,args);}}as any));
 const state=async()=>{const hashes:Record<string,string>={};for(const t of ['raw_records','formal_records','month_perf_records','payroll_period_rows','payroll_payments','rebuild_snapshots'])hashes[t]=(await db.query(`SELECT md5(coalesce(string_agg(row_to_json(r)::text,'' ORDER BY id),'')) AS hash FROM ${t} r`)).rows[0].hash;return hashes;};
 const call=async(role:string,args:Record<string,unknown>)=>{const r=await clients.get(role)!.callTool({name:'visit_rebuild',arguments:args});return JSON.parse((r.content as {text:string}[])[0].text);};
 try{
  await db.query('INSERT INTO persons(code,display_name,created_at) VALUES($1,\'fixture\',now())',[code]);
  for(const role of ['admin','staff'])users.push((await db.query(`INSERT INTO users(username,password_hash,display_name,role,status,is_active,must_change_password,lang,created_at) VALUES($1,'fixture-only',$1,$2,'active',true,false,'zh',now()) RETURNING id`,['mcp-rebuild-'+role,role])).rows[0].id);
  imp=(await db.query(`INSERT INTO imports(file_name,file_sha256,file_size,stored_path,uploaded_by,uploaded_at,parsed_sheets,ignored_sheets,total_rows,parsed_rows,status,warnings,errors,created_at) VALUES('mcp-rebuild','mcp-rebuild',0,'fixture',$1,now(),'[]','[]',1,1,'parsed','[]','[]',now()) RETURNING id`,[users[0]])).rows[0].id;
  const raw=(await db.query(`INSERT INTO raw_records(import_id,sheet_name,excel_row,store_id_raw,store_name_local_raw,store_name_en_raw,modified_raw,submitter_raw,submitter_code,record_id_raw,original_row,visible_raw,deploy_raw,clean_status,created_at) VALUES($1,'fixture',1,'MCP-RB-S','fixture store','',$2,'fixture',$3,'MCP-RB-S','{}','YES','YES','valid',now()) RETURNING id`,[imp,month+'-01 10:00:00',code])).rows[0].id;
  await app.listen(0,'127.0.0.1');const endpoint=new URL(`http://127.0.0.1:${(app.getHttpServer().address()as AddressInfo).port}/mcp`);
  for(const [name,index,scope]of [['admin',0,'read,write'],['other',0,'read,write'],['read',0,'read'],['staff',1,'read,write']]as const){const token=randomBytes(32).toString('base64url');await db.query("INSERT INTO api_tokens(user_id,name,token_prefix,token_hash,scopes,created_at) VALUES($1,'fixture',$2,$3,$4,now())",[users[index],token.slice(0,8),createHash('sha256').update(token).digest('hex'),scope]);const c=new Client({name:'rebuild-fixture',version:'1'});clients.set(name,c);await c.connect(new StreamableHTTPClientTransport(endpoint,{requestInit:{headers:{Authorization:`Bearer ${token}`}}}));}
  const run=(preview:number)=>({month,action:'run',preview_id:preview,confirm_text:`确认重算 ${month}`});
  const before=await state();let preview=await call('admin',{month});assert.equal(preview.ok,true,JSON.stringify(preview));assert.equal(preview.data.raw_total,1);assert.equal(preview.data.raw_by_status.valid,1);assert.equal(preview.data.raw_by_points['2'],1);assert.equal(preview.data.estimated_insert_rows,1);assert.deepEqual(await state(),before);
  assert.equal((await call('staff',{month})).error.code,'FORBIDDEN_TOOL');assert.equal((await call('other',run(preview.data.preview_id))).error.code,'PREVIEW_INVALID');assert.equal((await call('read',run(preview.data.preview_id))).error.code,'FORBIDDEN_TOOL');
  await db.query("UPDATE mcp_audit_log SET created_at=timezone('UTC',now())-interval '31 minutes' WHERE id=$1",[preview.data.preview_id]);assert.equal((await call('admin',run(preview.data.preview_id))).error.code,'PREVIEW_EXPIRED');
  preview=await call('admin',{month});await db.query("UPDATE raw_records SET deploy_raw='NO' WHERE id=$1",[raw]);assert.equal((await call('admin',run(preview.data.preview_id))).error.code,'PREVIEW_CHANGED');
  await db.query("INSERT INTO sealed_months(month,created_at) VALUES($1,now())",[month]);assert.equal((await call('admin',{month})).error.code,'MONTH_SEALED');await db.query('DELETE FROM sealed_months WHERE month=$1',[month]);
  const empty=await call('admin',{month:'2093-11'});assert.equal(empty.ok,true);assert.equal((await call('admin',{month:'2093-11',action:'run',preview_id:empty.data.preview_id,confirm_text:'确认重算 2093-11'})).error.code,'REBUILD_SOURCE_MISSING');
  preview=await call('admin',{month});const unchanged=await state();failAudit=true;let result=await call('admin',run(preview.data.preview_id));assert.equal(result.error.code,'INTERNAL');failAudit=false;assert.deepEqual(await state(),unchanged,'audit failure rolls back formal/statistics/snapshots');
  result=await call('admin',run(preview.data.preview_id));assert.equal(result.ok,true,JSON.stringify(result));assert.equal(result.data.formal_after,1);assert.equal(result.data.points_after,1);assert.equal((await call('admin',run(preview.data.preview_id))).error.code,'PREVIEW_USED');
  assert.equal((await db.query('SELECT count(*)::int AS n FROM payroll_payments')).rows[0].n,0);
 }finally{
  await Promise.all([...clients.values()].map(c=>c.close()));try{await db.query('DELETE FROM mcp_audit_log WHERE user_id=ANY($1::int[])',[users]);await db.query('DELETE FROM api_tokens WHERE user_id=ANY($1::int[])',[users]);await db.query('DELETE FROM sealed_months WHERE month=$1',[month]);for(const table of ['payroll_settlement_links','payroll_payments','payroll_adjusts','payroll_period_rows','month_perf_records','person_daily_stats'])await db.query(`DELETE FROM ${table} WHERE person_code=$1`,[code]);await db.query('DELETE FROM person_stats WHERE submitter_code=$1',[code]);await db.query('DELETE FROM dash_metrics WHERE month=$1',[month]);await db.query('DELETE FROM rebuild_snapshots WHERE month=$1',[month]);if(imp){await db.query('DELETE FROM formal_records WHERE import_id=$1',[imp]);await db.query('DELETE FROM raw_records WHERE import_id=$1',[imp]);await db.query('DELETE FROM imports WHERE id=$1',[imp]);}await db.query('DELETE FROM users WHERE id=ANY($1::int[])',[users]);await db.query('DELETE FROM persons WHERE code=$1',[code]);}finally{await app.close();}
 }
});
