import 'reflect-metadata';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash,randomBytes,randomUUID} from 'node:crypto';
import type {AddressInfo} from 'node:net';
import {Client} from '@modelcontextprotocol/sdk/client/index.js';
import {StreamableHTTPClientTransport} from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import {createApp} from '../src/main';
import {Database} from '../src/database';
import {McpStoreWriteService} from '../src/mcp-store-write';
import type {McpActor} from '../src/mcp';
const actor:McpActor={uid:1,tokenId:1,role:'admin',scopes:['read','write'],personCode:null,username:'fixture',displayName:'',personName:null};
test('MCP store writes require admin/scope, complete selection and destructive confirmation',async()=>{
 const service=new McpStoreWriteService({}as any),client={query:async()=>{throw Error('Unexpected business query');}}as any;
 await assert.rejects(service.run(client,{...actor,role:'staff'},{action:'merge',pair_id:1,keep:2},1),{code:'FORBIDDEN_TOOL'});
 await assert.rejects(service.run(client,{...actor,scopes:['read']},{action:'skip',pair_id:1},1),{code:'FORBIDDEN_TOOL'});
 await assert.rejects(service.run(client,actor,{action:'merge'},1),{code:'BAD_PARAM'});
 await assert.rejects(service.run(client,actor,{action:'split',entity_id:1},1),{code:'CONFIRM_REQUIRED'});
 await assert.rejects(service.run(client,actor,{action:'apply'},1),{code:'CONFIRM_REQUIRED'});
 await assert.rejects(service.run(client,actor,{action:'skip',pair_id:1,note:'[request:spoof]'},1),{code:'BAD_PARAM'});
});
const url=process.env.NEXT_TEST_DATABASE_URL;
test('schema-only store SDK: dry-run, stale version, merge/split/skip/apply, request reuse and audit rollback',{skip:!url},async()=>{
 assert.match(new URL(url!).pathname,/^\/store_test_[0-9]{13}_[a-f0-9]{12}_refactor_p1$/);
 const app=await createApp({databaseUrl:url!,sessionSecret:'x'.repeat(40),cookieSecure:false,host:'127.0.0.1',port:3000}),db=app.get(Database),users:number[]=[],entities:number[]=[],pairs:number[]=[],clients=new Map<string,Client>();
 const transaction=db.transaction.bind(db);let failAudit=false;
 db.transaction=fn=>transaction(client=>fn({query:(sql:string,values:any[]=[])=>{if(failAudit&&sql.startsWith('UPDATE mcp_audit_log SET ok=true'))throw Error('fixture audit failure');return client.query(sql,values);}}as any));
 const entity=async(name:string,city='Tokyo')=>{const id=(await db.query("SELECT nextval('store_entities_id_seq')::int AS id")).rows[0].id;entities.push(id);await db.query('INSERT INTO store_entities(id,store_id_raw,name_local,name_norm,city,master_id,master_store_id,created_at,updated_at) VALUES($1,$2,$3,$3,$4,$1,$2,now(),now())',[id,'MCP-STORE-'+id,name,city]);return id;};
 const pair=async(a:number,b:number,kind='exact')=>{const id=(await db.query("INSERT INTO store_pairs(entity_a,entity_b,kind,status,created_at) VALUES($1,$2,$3,'pending',now()) RETURNING id",[a,b,kind])).rows[0].id;pairs.push(id);return id;};
 const call=async(role:string,args:Record<string,unknown>)=>{const r=await clients.get(role)!.callTool({name:'visit_store',arguments:args});return JSON.parse((r.content as {text:string}[])[0].text);};
 const state=async()=>{const hashes:Record<string,string>={};for(const table of ['store_entities','store_pairs','store_merge_logs','payroll_payments','month_perf_records'])hashes[table]=(await db.query(`SELECT md5(coalesce(string_agg(row_to_json(r)::text,'' ORDER BY id),'')) AS h FROM ${table} r`)).rows[0].h;return hashes;};
 try{
  for(const role of ['admin','staff'])users.push((await db.query(`INSERT INTO users(username,password_hash,display_name,role,status,is_active,must_change_password,lang,created_at) VALUES($1,'fixture-only',$1,$2,'active',true,false,'zh',now()) RETURNING id`,['mcp-store-'+role,role])).rows[0].id);
  const a=await entity('same A'),b=await entity('same A'),c=await entity('skip C'),d=await entity('skip D'),p=await pair(a,b),skip=await pair(c,d,'fuzzy');
  await app.listen(0,'127.0.0.1');const endpoint=new URL(`http://127.0.0.1:${(app.getHttpServer().address()as AddressInfo).port}/mcp`);
  for(const [name,index,scopes]of [['admin',0,'read,write'],['read',0,'read'],['staff',1,'read,write']]as const){const raw=randomBytes(32).toString('base64url');await db.query("INSERT INTO api_tokens(user_id,name,token_prefix,token_hash,scopes,created_at) VALUES($1,'fixture',$2,$3,$4,now())",[users[index],raw.slice(0,8),createHash('sha256').update(raw).digest('hex'),scopes]);const client=new Client({name:'store-fixture',version:'1'});clients.set(name,client);await client.connect(new StreamableHTTPClientTransport(endpoint,{requestInit:{headers:{Authorization:`Bearer ${raw}`}}}));}
  let before=await state();let result=await call('read',{action:'merge',pair_id:p,keep:a});assert.equal(result.error.code,'FORBIDDEN_TOOL');result=await call('staff',{action:'merge',pair_id:p,keep:a,dry_run:true});assert.equal(result.error.code,'FORBIDDEN_TOOL');assert.deepEqual(await state(),before);
  const preview=await call('read',{action:'merge',pair_id:p,keep:a,dry_run:true});assert.equal(preview.ok,true,JSON.stringify(preview));assert.equal(preview.data.dry_run,true);assert.equal(preview.data.entityCount,2);assert.deepEqual(await state(),before);
  await db.query('UPDATE store_entities SET name_local=$1 WHERE id=$2',['changed',b]);result=await call('admin',{action:'merge',pair_id:p,keep:a,token:preview.data.token});assert.equal(result.error.code,'STORE_MASTER_CHANGED');
  let current=await call('read',{action:'merge',pair_id:p,keep:a,dry_run:true});const request=randomUUID();
  result=await call('admin',{action:'merge',pair_id:p,keep:a,token:current.data.token,request_id:request,note:'fixture user reason'});assert.equal(result.ok,true,JSON.stringify(result));assert.equal(result.data.keep_entity_id,a);assert.equal(result.data.merged_entity_id,b);assert.equal((await db.query('SELECT master_id FROM store_entities WHERE id=$1',[b])).rows[0].master_id,a);
  assert.ok((await db.query('SELECT note FROM store_merge_logs WHERE entity_id=$1',[b])).rows[0].note.includes('fixture user reason'));
  before=await state();result=await call('admin',{action:'merge',pair_id:p,keep:a,token:current.data.token,request_id:request,note:'fixture user reason'});assert.equal(result.data.reused,true);assert.deepEqual(await state(),before);
  result=await call('admin',{action:'skip',pair_id:skip,request_id:request});assert.equal(result.error.code,'REQUEST_ID_CONFLICT');assert.deepEqual(await state(),before);
  result=await call('admin',{action:'split',entity_id:b});assert.equal(result.error.code,'CONFIRM_REQUIRED');
  current=await call('read',{action:'split',entity_id:b,dry_run:true});result=await call('admin',{action:'split',entity_id:b,token:current.data.token,confirm_text:`确认拆分 ${b}`});assert.equal(result.ok,true,JSON.stringify(result));assert.equal(result.data.from_master,a);assert.equal(result.data.to_master,b);
  current=await call('read',{action:'skip',pair_id:skip,dry_run:true});before=await state();failAudit=true;result=await call('admin',{action:'skip',pair_id:skip,token:current.data.token});assert.equal(result.error.code,'INTERNAL');failAudit=false;assert.deepEqual(await state(),before);
  result=await call('admin',{action:'skip',pair_id:skip,token:current.data.token});assert.equal(result.ok,true);assert.equal(result.data.status,'skip');
  const e=await entity('recommend E'),f=await entity('recommend E');await pair(e,f);const x=await entity('cross X','Tokyo'),y=await entity('cross X','Osaka');await pair(x,y);
  current=await call('read',{action:'apply',dry_run:true});result=await call('admin',{action:'apply',token:current.data.token,confirm_text:'确认批量应用合并',request_id:randomUUID()});assert.equal(result.ok,true,JSON.stringify(result));assert.equal(result.data.groups_applied,1);assert.equal(result.data.entities_merged,1);assert.equal(result.data.groups_left_manual,1);assert.equal((await db.query('SELECT master_id FROM store_entities WHERE id=$1',[f])).rows[0].master_id,e);assert.equal((await db.query('SELECT master_id FROM store_entities WHERE id=$1',[y])).rows[0].master_id,y);
  const collisionPair=await pair(a,c,'fuzzy'),externalId=randomUUID();
  await db.query("INSERT INTO store_merge_logs(entity_id,from_master,to_master,basis,note,decided_by,created_at) VALUES($1,$1,$1,'manual',$2,$3,now())",[a,'external marker [request:'+externalId+']',users[0]]);
  before=await state();result=await call('admin',{action:'skip',pair_id:collisionPair,request_id:externalId});assert.equal(result.error.code,'REQUEST_ID_CONFLICT');assert.deepEqual(await state(),before,'unowned request markers cannot masquerade as successful MCP operations');
  const audit=(await db.query("SELECT params_json,ok FROM mcp_audit_log WHERE user_id=$1 AND tool='visit_store'",[users[0]])).rows;assert.ok(audit.every(row=>row.ok!==null));assert.ok(!JSON.stringify(audit).includes('fixture user reason'));
 }finally{
  await Promise.all([...clients.values()].map(c=>c.close()));await db.query('DELETE FROM mcp_audit_log WHERE user_id=ANY($1::int[])',[users]);await db.query('DELETE FROM api_tokens WHERE user_id=ANY($1::int[])',[users]);await db.query('DELETE FROM store_merge_logs WHERE entity_id=ANY($1::int[])',[entities]);await db.query('DELETE FROM store_pairs WHERE id=ANY($1::int[])',[pairs]);await db.query('UPDATE store_entities SET master_id=id WHERE id=ANY($1::int[])',[entities]);await db.query('DELETE FROM store_entities WHERE id=ANY($1::int[])',[entities]);await db.query('DELETE FROM users WHERE id=ANY($1::int[])',[users]);await app.close();
 }
});
