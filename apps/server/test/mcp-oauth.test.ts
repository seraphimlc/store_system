import 'reflect-metadata';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash,randomBytes} from 'node:crypto';
import request from '../node_modules/supertest';
import {hash} from '@node-rs/argon2';
import {createApp} from '../src/main';
import {Database} from '../src/database';
import {McpOAuthService,validOAuthRedirect} from '../src/mcp-oauth';
const digest=(raw:string)=>createHash('sha256').update(raw).digest('hex');
const verifier=()=>randomBytes(32).toString('base64url');
const challenge=(v:string)=>createHash('sha256').update(v).digest('base64url');
test('OAuth redirect validation accepts exact web/native/loopback URIs and excludes executable or remote insecure callbacks',()=>{
 for(const uri of ['https://client.test/callback?x=1','http://127.0.0.1:43121/callback','http://[::1]:8765/callback','workbuddy://oauth/callback','com.example.app:/oauth'])assert.equal(validOAuthRedirect(uri),true,uri);
 for(const uri of ['javascript:alert(1)','data:text/plain,secret','file:///tmp/callback','http://client.test/cb','https://user:pass@client.test/cb','https://client.test/cb#fragment','https://client.test/cb\n','https://client.test\\host/cb','mailto:person@example.test','intent://client','not a uri'])assert.equal(validOAuthRedirect(uri),false,uri);
});
const databaseUrl=process.env.NEXT_TEST_DATABASE_URL;
test('schema-only OAuth HTTP/PKCE: CSRF consent, rotation/replay revocation, legacy table compatibility, live account and credential management', {skip:!databaseUrl},async()=>{
 assert.match(new URL(databaseUrl!).pathname,/^\/store_test_[0-9]{13}_[a-f0-9]{12}_refactor_p1$/);
 const app=await createApp({databaseUrl:databaseUrl!,sessionSecret:'x'.repeat(40),cookieSecure:false,host:'127.0.0.1',port:3000,mcpPublicUrl:'https://store.fixture.test/mcp',mcpAuthorizationUiUrl:'http://127.0.0.1:8001/admin/mcp-authorize'}),db=app.get(Database),oauth=app.get(McpOAuthService);
 const users:number[]=[],clients:string[]=[];const agents=new Map<string,ReturnType<typeof request.agent>>(),csrf=new Map<string,string>();
 const callback='http://127.0.0.1:47111/callback';const password='oauth-fixture-password';
 const authorize=async(role:string,clientId:string,scope='read write')=>{const v=verifier(),payload={response_type:'code',client_id:clientId,redirect_uri:callback,code_challenge:challenge(v),code_challenge_method:'S256',scope,state:'fixture-state-✓',resource:'https://store.fixture.test/mcp'};
  const response=await agents.get(role)!.post('/api/mcp-oauth/authorize').set('X-CSRF-Token',csrf.get(role)!).send({request:payload,approved:true}).expect(201);
  const redirect=new URL(response.body.redirect);assert.equal(redirect.origin,new URL(callback).origin);assert.equal(redirect.searchParams.get('state'),'fixture-state-✓');return {code:redirect.searchParams.get('code')!,v,payload};};
 const exchange=(clientId:string,auth:{code:string;v:string})=>request(app.getHttpServer()).post('/oauth/token').type('form').send({grant_type:'authorization_code',client_id:clientId,redirect_uri:callback,code:auth.code,code_verifier:auth.v,resource:'https://store.fixture.test/mcp'});
 const active=async(raw:string,expected=200)=>request(app.getHttpServer()).post('/mcp').set('Authorization','Bearer '+raw).set('Accept','application/json, text/event-stream').send({jsonrpc:'2.0',id:1,method:'initialize',params:{protocolVersion:'2025-03-26',capabilities:{},clientInfo:{name:'oauth-fixture',version:'1'}}}).expect(expected);
 try{
  const pw=await hash(password);
  for(const role of ['admin','staff','leader','other']){const identity=(await db.query(`INSERT INTO users(username,password_hash,display_name,role,status,is_active,must_change_password,lang,created_at) VALUES($1,$2,$1,$3,'active',true,false,'zh',now()) RETURNING id`,['oauth-'+role,pw,role==='other'?'admin':role])).rows[0].id;users.push(identity);const agent=request.agent(app.getHttpServer());agents.set(role,agent);const login=await agent.post('/api/auth/login').send({username:'oauth-'+role,password}).expect(201);csrf.set(role,login.body.csrfToken);}
  const metadata=await request(app.getHttpServer()).get('/.well-known/oauth-authorization-server').expect(200);assert.equal(metadata.body.token_endpoint,'https://store.fixture.test/oauth/token');assert.deepEqual(metadata.body.code_challenge_methods_supported,['S256']);assert.equal(metadata.headers['cache-control'],'no-store');
  const resource=await request(app.getHttpServer()).get('/.well-known/oauth-protected-resource').expect(200);assert.deepEqual(resource.body.authorization_servers,['https://store.fixture.test']);
  await request(app.getHttpServer()).post('/oauth/register').send({redirect_uris:['javascript:alert(1)']}).expect(400);
  const reg=await request(app.getHttpServer()).post('/oauth/register').send({client_name:'fixture client <script>',redirect_uris:[callback],token_endpoint_auth_method:'none'}).expect(201);const clientId=reg.body.client_id;clients.push(clientId);assert.equal(reg.body.client_secret,undefined);
  const otherReg=await request(app.getHttpServer()).post('/oauth/register').send({redirect_uris:[callback]}).expect(201);clients.push(otherReg.body.client_id);
  const auth=await authorize('staff',clientId);assert.ok(auth.code);
  const authorizeGet=await request(app.getHttpServer()).get('/oauth/authorize').query(auth.payload).expect(303);assert.ok(authorizeGet.headers.location.startsWith('http://127.0.0.1:8001/admin/mcp-authorize?'));
  await request(app.getHttpServer()).get('/oauth/authorize').query({...auth.payload,redirect_uri:callback+'?changed=1'}).expect(400);
  await request(app.getHttpServer()).get('/oauth/authorize').query({...auth.payload,code_challenge_method:'plain'}).expect(400);
  await request(app.getHttpServer()).get('/oauth/authorize').query({...auth.payload,resource:'https://other.test/mcp'}).expect(400);
  await request(app.getHttpServer()).get('/api/mcp-oauth/context').query(auth.payload).expect(401);
  const context=await agents.get('staff')!.get('/api/mcp-oauth/context').query(auth.payload).expect(200);assert.equal(context.body.user.role,'staff');assert.deepEqual(context.body.scope,['read','write']);
  await agents.get('staff')!.post('/api/mcp-oauth/authorize').send({request:auth.payload,approved:true}).expect(403);
  const denial=await agents.get('staff')!.post('/api/mcp-oauth/authorize').set('X-CSRF-Token',csrf.get('staff')!).send({request:auth.payload,approved:false}).expect(201);assert.equal(new URL(denial.body.redirect).searchParams.get('error'),'access_denied');
  const failedPkce=await exchange(clientId,{...auth,v:verifier()}).expect(400);assert.equal(failedPkce.body.error,'invalid_grant');
  await exchange(otherReg.body.client_id,auth).expect(400);
  const issued=await exchange(clientId,auth).expect(200);assert.equal(issued.body.scope,'read write');assert.equal(issued.headers['cache-control'],'no-store');await active(issued.body.access_token);
  const stored=(await db.query('SELECT token_hash,name,token_prefix FROM api_tokens WHERE user_id=$1',[users[1]])).rows;assert.ok(stored.some(row=>row.token_hash===digest(issued.body.access_token)&&row.token_prefix===issued.body.access_token.slice(0,8)));assert.ok(!JSON.stringify(stored).includes(issued.body.access_token));
  const refresh=await request(app.getHttpServer()).post('/oauth/token').type('form').send({grant_type:'refresh_token',client_id:clientId,refresh_token:issued.body.refresh_token}).expect(200);assert.notEqual(refresh.body.refresh_token,issued.body.refresh_token);await active(refresh.body.access_token);
  const replay=await request(app.getHttpServer()).post('/oauth/token').send({grant_type:'refresh_token',client_id:clientId,refresh_token:issued.body.refresh_token}).expect(400);assert.equal(replay.body.error,'invalid_grant');await active(refresh.body.access_token,401);
  await request(app.getHttpServer()).post('/oauth/token').send({grant_type:'refresh_token',client_id:clientId,refresh_token:refresh.body.refresh_token}).expect(400);
  const codeReplay=await authorize('admin',clientId);const beforeReplay=await exchange(clientId,codeReplay).expect(200);await exchange(clientId,codeReplay).expect(400);await active(beforeReplay.body.access_token,401);await request(app.getHttpServer()).post('/oauth/token').send({grant_type:'refresh_token',client_id:clientId,refresh_token:beforeReplay.body.refresh_token}).expect(400);
  const readOnly=await authorize('staff',clientId,'read');const readTokens=await exchange(clientId,readOnly).expect(200);await request(app.getHttpServer()).post('/oauth/token').send({grant_type:'refresh_token',client_id:clientId,refresh_token:readTokens.body.refresh_token,scope:'read write'}).expect(400);
  const revoked=await authorize('leader',clientId);const revokeTokens=await exchange(clientId,revoked).expect(200);await request(app.getHttpServer()).post('/oauth/revoke').type('form').send({client_id:clientId,token:revokeTokens.body.refresh_token}).expect(200);await active(revokeTokens.body.access_token,401);
  const remains=await authorize('admin',clientId);const remainTokens=await exchange(clientId,remains).expect(200);await request(app.getHttpServer()).post('/oauth/revoke').send({client_id:otherReg.body.client_id,token:remainTokens.body.access_token}).expect(200);await active(remainTokens.body.access_token);
  await request(app.getHttpServer()).post('/oauth/revoke').send({client_id:clientId,token:'unknown-token'}).expect(200);
  const generations=await authorize('admin',clientId);let gen=(await exchange(clientId,generations).expect(200)).body;
  const rootRefresh=gen.refresh_token;
  for(let i=0;i<3;i++){gen=(await request(app.getHttpServer()).post('/oauth/token').send({grant_type:'refresh_token',client_id:clientId,refresh_token:gen.refresh_token}).expect(200)).body;await active(gen.access_token);}
  await request(app.getHttpServer()).post('/oauth/revoke').send({client_id:clientId,token:rootRefresh}).expect(200);await active(gen.access_token,401);await request(app.getHttpServer()).post('/oauth/token').send({grant_type:'refresh_token',client_id:clientId,refresh_token:gen.refresh_token}).expect(400);
  const refreshExpired=await authorize('admin',clientId);const expiredTokens=(await exchange(clientId,refreshExpired).expect(200)).body;
  await db.query("UPDATE oauth_refresh_tokens SET expires_at=timezone('UTC',now())-interval '1 second' WHERE token_hash=$1",[digest(expiredTokens.refresh_token)]);
  await request(app.getHttpServer()).post('/oauth/token').send({grant_type:'refresh_token',client_id:clientId,refresh_token:expiredTokens.refresh_token}).expect(400);
  const changePassword=await authorize('leader',clientId);await db.query('UPDATE users SET must_change_password=true WHERE id=$1',[users[2]]);await exchange(clientId,changePassword).expect(400);await db.query('UPDATE users SET must_change_password=false WHERE id=$1',[users[2]]);
  await agents.get('admin')!.get('/api/mcp-oauth/context').query({...auth.payload,redirect_uri:'https://not-registered.test/cb'}).expect(400);
  const expires=await authorize('admin',clientId);await db.query("UPDATE oauth_codes SET expires_at=timezone('UTC',now())-interval '1 second' WHERE code_hash=$1",[digest(expires.code)]);await exchange(clientId,expires).expect(400);
  const disabled=await authorize('staff',clientId);await db.query("UPDATE users SET status='deleted',is_active=false WHERE id=$1",[users[1]]);await exchange(clientId,disabled).expect(400);await active(readTokens.body.access_token,401);await db.query("UPDATE users SET status='active',is_active=true WHERE id=$1",[users[1]]);
  // Stored legacy refresh rows need no schema rewrite to rotate through the new implementation.
  const legacy=verifier(),clientDbId=(await db.query('SELECT id FROM oauth_clients WHERE client_id=$1',[clientId])).rows[0].id;
  await db.query("INSERT INTO oauth_refresh_tokens(token_hash,client_id,user_id,scope,expires_at,created_at) VALUES($1,$2,$3,'read',timezone('UTC',now())+interval '1 day',timezone('UTC',now()))",[digest(legacy),clientDbId,users[2]]);
  const oldRefresh=await request(app.getHttpServer()).post('/oauth/token').send({grant_type:'refresh_token',client_id:clientId,refresh_token:legacy}).expect(200);await active(oldRefresh.body.access_token);
  const concurrent=await authorize('admin',clientId);const exchanges=await Promise.all([exchange(clientId,concurrent),exchange(clientId,concurrent)]);assert.deepEqual(exchanges.map(row=>row.status).sort(),[200,400]);await active(exchanges.find(row=>row.status===200)!.body.access_token,401);
  // Credential APIs require administrator session, CSRF and matching account ownership; lists never expose secrets.
  await request(app.getHttpServer()).get('/api/mcp-credentials/'+users[1]).expect(401);await agents.get('staff')!.get('/api/mcp-credentials/'+users[1]).expect(403);
  await agents.get('admin')!.post('/api/mcp-credentials/'+users[1]).send({name:'fixture',scopes:'read',days:90}).expect(403);
  const created=await agents.get('admin')!.post('/api/mcp-credentials/'+users[1]).set('X-CSRF-Token',csrf.get('admin')!).send({name:'fixture manual',scopes:'read,write',days:90}).expect(201);await active(created.body.token);
  const listing=await agents.get('admin')!.get('/api/mcp-credentials/'+users[1]).expect(200);assert.ok(!JSON.stringify(listing.body).includes(created.body.token));assert.ok(!JSON.stringify(listing.body).includes('token_hash'));
  await agents.get('admin')!.post(`/api/mcp-credentials/${users[2]}/${created.body.credential.id}/revoke`).set('X-CSRF-Token',csrf.get('admin')!).send({}).expect(404);
  await agents.get('admin')!.post(`/api/mcp-credentials/${users[1]}/${created.body.credential.id}/revoke`).set('X-CSRF-Token',csrf.get('admin')!).send({}).expect(201);await active(created.body.token,401);
  const oauthAccess=(await db.query('SELECT id FROM api_tokens WHERE token_hash=$1',[digest(remainTokens.body.access_token)])).rows[0].id;
  await agents.get('admin')!.post(`/api/mcp-credentials/${users[0]}/${oauthAccess}/revoke`).set('X-CSRF-Token',csrf.get('admin')!).send({}).expect(201);await active(remainTokens.body.access_token,401);await request(app.getHttpServer()).post('/oauth/token').send({grant_type:'refresh_token',client_id:clientId,refresh_token:remainTokens.body.refresh_token}).expect(400);
  await db.query("UPDATE users SET status='leave' WHERE id=$1",[users[1]]);await agents.get('admin')!.post('/api/mcp-credentials/'+users[1]).set('X-CSRF-Token',csrf.get('admin')!).send({name:'unavailable',scopes:'read',days:null}).expect(409);
  await request(app.getHttpServer()).post('/oauth/token').type('form').send('grant_type=refresh_token&grant_type=authorization_code&client_id='+clientId).expect(400);
  assert.equal((await db.query('SELECT count(*)::int AS n FROM payroll_payments')).rows[0].n,0);
 }finally{
  try{await db.query('DELETE FROM oauth_codes WHERE user_id=ANY($1::int[])',[users]);await db.query('DELETE FROM oauth_refresh_tokens WHERE user_id=ANY($1::int[])',[users]);await db.query('DELETE FROM mcp_audit_log WHERE user_id=ANY($1::int[])',[users]);await db.query('DELETE FROM api_tokens WHERE user_id=ANY($1::int[])',[users]);await db.query('DELETE FROM oauth_clients WHERE client_id=ANY($1::text[])',[clients]);await db.query('DELETE FROM users WHERE id=ANY($1::int[])',[users]);}finally{await app.close();}
 }
});
