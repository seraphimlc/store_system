import 'reflect-metadata';
import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash, randomBytes } from 'node:crypto';
import type { AddressInfo } from 'node:net';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { createApp } from '../src/main';
import { Database } from '../src/database';
import { McpManagementWriteService } from '../src/mcp-management-write';
import type { McpActor } from '../src/mcp';
const actor: McpActor = { uid: 1, tokenId: 1, role: 'admin', scopes: ['read','write'], personCode: null, username: 'fixture', displayName: '', personName: null };
test('management writes enforce role/scope and confirmation before business reads', async () => {
  const service = new McpManagementWriteService({} as any, {} as any), client = { query: async () => { throw Error('Business read unexpected'); } } as any;
  for (const role of ['staff','leader'] as const) await assert.rejects(service.run(client, {...actor,role},'visit_config',{action:'set'}),{code:'FORBIDDEN_TOOL'});
  await assert.rejects(service.run(client,{...actor,scopes:['read']},'visit_staff',{action:'set_status'}),{code:'FORBIDDEN_TOOL'});
  await assert.rejects(service.run(client,actor,'visit_config',{action:'set'}),{code:'CONFIRM_REQUIRED'});
  await assert.rejects(service.run(client,actor,'visit_staff',{action:'set_status',username:'fixture',status:'disabled'}),{code:'CONFIRM_REQUIRED'});
  await assert.rejects(service.run(client,actor,'visit_staff',{action:'set_status',username:'fixture',status:'deleted',confirm_text:'确认改状态 fixture deleted'}),{code:'BAD_PARAM'});
});
const url=process.env.NEXT_TEST_DATABASE_URL;
test('schema-only SDK management writes: shared status/config guards, token invalidation, conflicts and atomic audit rollback', {skip:!url}, async()=>{
  assert.match(new URL(url!).pathname,/^\/store_test_[0-9]{13}_[a-f0-9]{12}_refactor_p1$/);
  const app=await createApp({databaseUrl:url!,sessionSecret:'x'.repeat(40),cookieSecure:false,host:'127.0.0.1',port:3000});
  const db=app.get(Database),users:number[]=[],tokens=new Map<string,number>(),clients=new Map<string,Client>();let configId:number|undefined;
  const transaction=db.transaction.bind(db);let failAudit=false,revokeBefore=false;
  db.transaction=fn=>transaction(async client=>{
    if(revokeBefore){revokeBefore=false;await client.query('UPDATE api_tokens SET revoked_at=now() WHERE id=$1',[tokens.get('admin-write')]);}
    return fn({query:(sql:string,args:any[]=[])=>{if(failAudit&&sql.startsWith('UPDATE mcp_audit_log SET ok=true'))throw Error('fixture private audit failure');return client.query(sql,args);}} as any);
  });
  const read=async(role:string,tool:string,args:Record<string,unknown>={})=>{const result=await clients.get(role)!.callTool({name:tool,arguments:args});return JSON.parse((result.content as {text:string}[])[0].text);};
  const userState=async()=> (await db.query('SELECT status,is_active,username,display_name,role,password_hash,must_change_password FROM users WHERE id=$1',[users[3]])).rows[0];
  const financial=async()=>{const hashes:Record<string,string>={};for(const t of ['payroll_payments','payroll_period_rows','month_perf_records','payroll_adjusts','payroll_settlement_links'])hashes[t]=(await db.query(`SELECT md5(coalesce(string_agg(row_to_json(r)::text,'' ORDER BY id),'')) AS h FROM ${t} r`)).rows[0].h;return hashes;};
  try{
    for(const [role,status] of [['admin','active'],['leader','active'],['staff','active'],['staff','active'],['staff','deleted']]as const)users.push((await db.query(`INSERT INTO users(username,password_hash,display_name,role,status,is_active,must_change_password,lang,created_at) VALUES($1,'private-fixture-hash','fixture name',$2,$3,$4,false,'ja',now()) RETURNING id`,['mcp-write-'+users.length,role,status,status!=='deleted'])).rows[0].id);
    configId=(await db.query("INSERT INTO sys_configs(config_month,per_point,bonus_group,bonus_amount,staff_visible_from,updated_at) VALUES('2026-10',250,68,3000,'2026-10',now()) RETURNING id")).rows[0].id;
    await app.listen(0,'127.0.0.1');const endpoint=new URL(`http://127.0.0.1:${(app.getHttpServer().address()as AddressInfo).port}/mcp`);
    for(const [name,index,scopes]of [['admin-write',0,'read,write'],['admin-read',0,'read'],['leader',1,'read,write'],['staff',2,'read,write'],['target',3,'read']]as const){
      const raw=randomBytes(32).toString('base64url');const id=(await db.query("INSERT INTO api_tokens(user_id,name,token_prefix,token_hash,scopes,created_at) VALUES($1,'fixture',$2,$3,$4,now()) RETURNING id",[users[index],raw.slice(0,8),createHash('sha256').update(raw).digest('hex'),scopes])).rows[0].id;tokens.set(name,id);
      const client=new Client({name:'mcp-write-fixture',version:'1'});clients.set(name,client);await client.connect(new StreamableHTTPClientTransport(endpoint,{requestInit:{headers:{Authorization:`Bearer ${raw}`}}}));
    }
    const before=await financial(),initial=await userState();
    const statusArgs={action:'set_status',username:'mcp-write-3',status:'disabled',confirm_text:'确认改状态 mcp-write-3 disabled'};
    for(const role of ['admin-read','leader','staff']){assert.equal((await read(role,'visit_staff',statusArgs)).error.code,'FORBIDDEN_TOOL');assert.equal((await read(role,'visit_config',{action:'set',confirm_text:'确认修改配置',per_point:333})).error.code,'FORBIDDEN_TOOL');}
    assert.deepEqual(await userState(),initial);
    assert.equal((await read('admin-write','visit_staff',{...statusArgs,confirm_text:'wrong'})).error.code,'CONFIRM_REQUIRED');
    assert.equal((await read('admin-write','visit_config',{action:'set',per_point:333})).error.code,'CONFIRM_REQUIRED');
    assert.equal((await read('admin-write','visit_config',{action:'set',per_point:10001,confirm_text:'确认修改配置'})).error.code,'BAD_PARAM');
    assert.equal((await read('admin-write','visit_config',{action:'set',staff_visible_from:'2026-13',confirm_text:'确认修改配置'})).error.code,'BAD_PARAM');
    let result=await read('admin-write','visit_staff',{...statusArgs,expected_status:'leave'});assert.equal(result.error.code,'CONFLICT');
    for(const status of ['disabled','resigned','leave','active']){
      result=await read('admin-write','visit_staff',{action:'set_status',username:'mcp-write-3',status,confirm_text:`确认改状态 mcp-write-3 ${status}`});assert.equal(result.ok,true);assert.equal(result.data.new_status,status);
      const current=await userState();assert.equal(current.status,status);assert.equal(current.is_active,['active','leave'].includes(status));assert.equal(current.password_hash,initial.password_hash);assert.equal(current.role,initial.role);assert.equal(current.display_name,initial.display_name);
      if(status==='active')assert.equal((await read('target','visit_whoami')).ok,true);else await assert.rejects(clients.get('target')!.listTools(),{code:401});
    }
    assert.equal((await read('admin-write','visit_staff',{action:'set_status',username:'mcp-write-4',status:'active',confirm_text:'确认改状态 mcp-write-4 active'})).error.code,'NOT_FOUND');
    const config=await read('admin-write','visit_config',{include_version:true});assert.equal(config.ok,true);const oldVersion=config.data.expected;
    result=await read('admin-write','visit_config',{action:'set',per_point:333,bonus_amount:0,staff_visible_from:'',expected:oldVersion,confirm_text:'确认修改配置'});assert.equal(result.ok,true);
    assert.equal(result.data.per_point,333);assert.equal(result.data.bonus_group,68);assert.equal(result.data.bonus_amount,0);assert.equal(result.data.staff_visible_from,'');
    result=await read('admin-write','visit_config',{action:'set',per_point:444,expected:oldVersion,confirm_text:'确认修改配置'});assert.equal(result.error.code,'CONFIG_CHANGED');
    assert.equal((await db.query('SELECT per_point FROM sys_configs WHERE id=$1',[configId])).rows[0].per_point,333);
    // Audit completion and financial write are one transaction: an audit failure must not commit either config or status.
    failAudit=true;
    result=await read('admin-write','visit_config',{action:'set',per_point:444,confirm_text:'确认修改配置'});assert.equal(result.error.code,'INTERNAL');assert.ok(!JSON.stringify(result).includes('private'));
    result=await read('admin-write','visit_staff',statusArgs);assert.equal(result.error.code,'INTERNAL');failAudit=false;
    assert.equal((await userState()).status,'active');assert.equal((await db.query('SELECT per_point FROM sys_configs WHERE id=$1',[configId])).rows[0].per_point,333);
    revokeBefore=true;result=await read('admin-write','visit_config',{action:'set',per_point:555,confirm_text:'确认修改配置'});assert.equal(result.error.code,'UNAUTHORIZED');
    assert.equal((await db.query('SELECT per_point FROM sys_configs WHERE id=$1',[configId])).rows[0].per_point,333);
    assert.deepEqual(await financial(),before,'configuration/status writes must not recalculate wages or alter paid facts');
    const audits=(await db.query('SELECT ok,params_json FROM mcp_audit_log WHERE user_id=ANY($1::int[])',[users])).rows;assert.ok(audits.some(row=>row.ok===true));assert.ok(audits.every(row=>row.ok!==null&&row.params_json==='{}'));
    const defs=(await clients.get('admin-read')!.listTools()).tools;for(const name of ['visit_staff','visit_config'])assert.equal(defs.find(tool=>tool.name===name)!.annotations?.readOnlyHint,false);
  }finally{
    await Promise.all([...clients.values()].map(client=>client.close()));await db.query('DELETE FROM mcp_audit_log WHERE user_id=ANY($1::int[])',[users]);await db.query('DELETE FROM api_tokens WHERE user_id=ANY($1::int[])',[users]);if(configId)await db.query('DELETE FROM sys_configs WHERE id=$1',[configId]);await db.query('DELETE FROM users WHERE id=ANY($1::int[])',[users]);await app.close();
  }
});
