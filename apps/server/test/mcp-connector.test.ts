import 'reflect-metadata';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash,randomBytes} from 'node:crypto';
import {hash} from '@node-rs/argon2';
import {Client} from '@modelcontextprotocol/sdk/client/index.js';
import {StreamableHTTPClientTransport} from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import type {OAuthClientProvider} from '@modelcontextprotocol/sdk/client/auth.js';
import type {OAuthClientInformationMixed,OAuthTokens} from '@modelcontextprotocol/sdk/shared/auth.js';
import type {AddressInfo} from 'node:net';
import request from '../node_modules/supertest';
import {readFile} from 'node:fs/promises';
import {createApp} from '../src/main';
import {Database} from '../src/database';
const databaseUrl=process.env.NEXT_TEST_DATABASE_URL;
test('schema-only official OAuth client: connector discovery/registration/PKCE/refresh, role-scoped tools and representative staff write', {skip:!databaseUrl},async()=>{
 assert.match(new URL(databaseUrl!).pathname,/^\/store_test_[0-9]{13}_[a-f0-9]{12}_refactor_p1$/);
 const config={databaseUrl:databaseUrl!,sessionSecret:'x'.repeat(40),cookieSecure:false,host:'127.0.0.1',port:3000,mcpPublicUrl:'http://127.0.0.1:3000/mcp',oauthTrustedRedirectUris:['http://127.0.0.1:47211/callback']};
 const app=await createApp(config),db=app.get(Database),users:number[]=[],codes=['MCP-CONNECTOR-STAFF','MCP-CONNECTOR-LEADER'],clientIds:string[]=[],connected:Client[]=[];
 const read=async(client:Client,name:string,args:Record<string,unknown>={})=>{const result=await client.callTool({name,arguments:args});return JSON.parse((result.content as {text:string}[])[0].text);};
 try{
  const password='connector-fixture-password',pw=await hash(password);
  for(const code of codes)await db.query('INSERT INTO persons(code,display_name,created_at) VALUES($1,$1,now())',[code]);
  for(const [i,role]of ['staff','leader','admin'].entries())users.push((await db.query(`INSERT INTO users(username,password_hash,display_name,person_code,role,status,is_active,must_change_password,lang,created_at) VALUES($1,$2,$1,$3,$4,'active',true,false,'zh',now()) RETURNING id`,['connector-'+role,pw,codes[i]??null,role])).rows[0].id);
  await app.listen(0,'127.0.0.1');const origin=`http://127.0.0.1:${(app.getHttpServer().address()as AddressInfo).port}`;config.mcpPublicUrl=origin+'/mcp';
  const connector=JSON.parse((await readFile('deploy-next/connector/visit-settle/mcp.json','utf8')).replaceAll('${VISIT_BASE_URL}',origin));const endpoint=new URL(connector.mcpServers['visit-settle'].url);
  const oauthInfo=JSON.parse((await readFile('deploy-next/connector/visit-settle/oauth-info.json','utf8')).replaceAll('${VISIT_BASE_URL}',origin));
  const discovery=await (await fetch(oauthInfo.discovery.protected_resource_metadata)).json();assert.equal(discovery.resource,endpoint.href);assert.deepEqual(discovery.authorization_servers,[origin]);
  for(const [i,role]of ['staff','leader','admin'].entries()){
   let registration:OAuthClientInformationMixed|undefined,tokens:OAuthTokens|undefined,verifier='',redirect:URL|undefined;const nonce=randomBytes(16).toString('hex');
   const provider:OAuthClientProvider={redirectUrl:'http://127.0.0.1:47211/callback',clientMetadata:{client_name:'official SDK connector '+role,redirect_uris:['http://127.0.0.1:47211/callback'],grant_types:['authorization_code','refresh_token'],response_types:['code'],token_endpoint_auth_method:'none',scope:'read write'},state:()=>nonce,
    clientInformation:()=>registration,saveClientInformation:value=>{registration=value;},tokens:()=>tokens,saveTokens:value=>{tokens=value;},redirectToAuthorization:value=>{redirect=value;},saveCodeVerifier:value=>{verifier=value;},codeVerifier:()=>verifier};
   const first=new Client({name:'official-sdk-oauth-fixture',version:'1'}),transport=new StreamableHTTPClientTransport(endpoint,{authProvider:provider});
   await assert.rejects(first.connect(transport));assert.ok(redirect,'SDK must discover/register and request interactive authorization');assert.ok(registration?.client_id);clientIds.push(registration.client_id);assert.equal(redirect.origin,origin);assert.equal(redirect.pathname,'/oauth/authorize');assert.equal(redirect.searchParams.get('state'),nonce);assert.equal(redirect.searchParams.get('code_challenge_method'),'S256');assert.equal(redirect.searchParams.get('resource'),endpoint.href);
   const agent=request.agent(app.getHttpServer()),login=await agent.post('/api/auth/login').send({username:'connector-'+role,password}).expect(201);
   const decision=await agent.post('/api/mcp-oauth/authorize').set('X-CSRF-Token',login.body.csrfToken).send({request:Object.fromEntries(redirect.searchParams),approved:true}).expect(201);const callback=new URL(decision.body.redirect);assert.equal(callback.searchParams.get('state'),nonce);
   await transport.finishAuth(callback.searchParams.get('code')!);assert.ok(tokens?.access_token,'SDK stores exchanged token');await first.close();
   const client=new Client({name:'official-sdk-connected-fixture',version:'1'});connected.push(client);await client.connect(new StreamableHTTPClientTransport(endpoint,{authProvider:provider}));const toolList=await client.listTools();assert.equal(toolList.tools.length,[6,10,27][i]);assert.equal(new Set(toolList.tools.map(tool=>tool.name)).size,toolList.tools.length);
   const who=(await read(client,'visit_whoami')).data;assert.equal(who.role,role);assert.equal(who.can_write,true);assert.equal(who.role_label,role==='staff'?'员工':role==='leader'?'队长':'管理员');assert.ok(!who.capabilities.includes('不能查看他人或公司级数据，不能修改任何数据'));
   if(role==='staff'){
    assert.ok(who.capabilities.some((line:string)=>line.includes('提交本人自报')));
    const mine=await read(client,'visit_my_perf');assert.equal(mine.ok,true);assert.equal(mine.data.person_code,codes[0]);
    assert.equal((await read(client,'visit_person',{person:codes[1],month:'bad'})).error.code,'FORBIDDEN_TOOL');
    assert.equal((await read(client,'visit_my_pay',{person_code:codes[1]})).error.code,'FORBIDDEN_TOOL');
    const report=await read(client,'visit_self_report',{area:'SDK fixture',p1_cnt:2,p2_cnt:1});assert.equal(report.ok,true,JSON.stringify(report));assert.equal((await db.query('SELECT p1_cnt,p2_cnt FROM staff_daily_reports WHERE person_code=$1',[codes[0]])).rows[0].p1_cnt,2);
    const oldAccess=tokens!.access_token;await db.query("UPDATE api_tokens SET expires_at=timezone('UTC',now())-interval '1 second' WHERE token_hash=$1",[createHash('sha256').update(oldAccess).digest('hex')]);
    await client.listTools();assert.notEqual(tokens!.access_token,oldAccess,'SDK refreshes an expired access token without browser reconnection');
   }
   if(role==='leader')assert.ok(who.capabilities.some((line:string)=>line.includes('本队权限派工')));
   if(role==='admin')assert.equal((await read(client,'visit_overview',{view:'months'})).ok,true);
   // Read-only credentials keep role discovery but cannot execute writes; capability prose reflects the scope.
   await db.query("UPDATE api_tokens SET scopes='read' WHERE token_hash=$1",[createHash('sha256').update(tokens!.access_token).digest('hex')]);
   const readonly=await read(client,'visit_whoami');assert.equal(readonly.data.can_write,false);assert.ok(readonly.data.hint.includes('凭据只读'));assert.ok(!readonly.data.capabilities.some((line:string)=>line.includes('提交本人自报')||line.includes('按当前本队权限派工')));
   if(role==='staff')assert.equal((await read(client,'visit_self_report',{p1_cnt:1})).error.code,'FORBIDDEN_TOOL');
  }
  assert.equal((await db.query('SELECT count(*)::int AS n FROM payroll_payments')).rows[0].n,0);
 }finally{
  await Promise.all(connected.map(client=>client.close()));try{await db.query('DELETE FROM oauth_codes WHERE user_id=ANY($1::int[])',[users]);await db.query('DELETE FROM oauth_refresh_tokens WHERE user_id=ANY($1::int[])',[users]);await db.query('DELETE FROM mcp_audit_log WHERE user_id=ANY($1::int[])',[users]);await db.query('DELETE FROM api_tokens WHERE user_id=ANY($1::int[])',[users]);await db.query('DELETE FROM oauth_clients WHERE client_id=ANY($1::text[])',[clientIds]);await db.query('DELETE FROM staff_daily_reports WHERE person_code=ANY($1::text[])',[codes]);await db.query('DELETE FROM staff_date_plans WHERE person_code=ANY($1::text[])',[codes]);await db.query('DELETE FROM users WHERE id=ANY($1::int[])',[users]);await db.query('DELETE FROM persons WHERE code=ANY($1::text[])',[codes]);}finally{await app.close();}
 }
});
