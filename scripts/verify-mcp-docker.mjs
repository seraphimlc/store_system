/** Phase-only Docker acceptance: disposable schema, isolated networks/volume, no traffic switch on 8000. */
import {createRequire} from 'node:module';
import {spawn,spawnSync} from 'node:child_process';
import {createServer} from 'node:http';
import {randomBytes,createHash} from 'node:crypto';
import {mkdtemp,mkdir,readFile,writeFile,rm} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {tmpdir} from 'node:os';
import {setTimeout as delay} from 'node:timers/promises';
import {edgeConfig} from './release-lib.mjs';
const require=createRequire(new URL('../apps/server/package.json',import.meta.url));
const {Pool}=require('pg'),{hash}=require('@node-rs/argon2'),{Client}=require('@modelcontextprotocol/sdk/client/index.js'),{StreamableHTTPClientTransport}=require('@modelcontextprotocol/sdk/client/streamableHttp.js');
const target=process.env.NEXT_TEST_DATABASE_URL,release=process.env.NEXT_MCP_ACCEPTANCE_RELEASE??'local-mcp-m6-20261113-a';
if(!target||!/^\/store_test_[0-9]{13}_[a-f0-9]{12}_refactor_p1$/.test(new URL(target).pathname)||!['localhost','127.0.0.1','[::1]'].includes(new URL(target).hostname)||process.env.NEXT_TEST_ISOLATED!=='1'||!/^[a-z0-9-]+$/.test(release))throw Error('Docker MCP acceptance requires isolation runner and a local frozen release');
const statePath='.release/local-state.json',stateBytes=await readFile(statePath),active=JSON.parse(stateBytes).current;
if(!/^[a-z0-9-]+$/.test(active))throw Error('Missing previous local release');
const manifest=JSON.parse(await readFile(join('.release',release,'manifest.json'))),previous=JSON.parse(await readFile(join('.release',active,'manifest.json')));
const prefix='store-mcp-accept-'+Date.now()+'-'+randomBytes(3).toString('hex'),temporary=await mkdtemp(join(tmpdir(),'store-mcp-docker-'));
const names={api:prefix+'-api',worker:prefix+'-worker',gateway:prefix+'-gateway',edge:prefix+'-edge',oldApi:prefix+'-old-api',oldWorker:prefix+'-old-worker',oldGateway:prefix+'-old-gateway'},containers=new Set(),networks=[],volumes=[];
const db=new Pool({connectionString:target}),clients=[],users=[],clientIds=[];let browserProcess,callback;const code='MCP-DOCKER-STAFF';
function docker(args,quiet=true){const r=spawnSync('docker',args,{encoding:'utf8',maxBuffer:4*1024*1024,timeout:120000});if(r.error||r.status!==0)throw Error('Docker '+args[0]+' failed: '+(r.stderr??'').slice(-900));return quiet?r.stdout.trim():r.stdout;}
function launch(name,args){containers.add(name);return docker(['run','-d','--name',name,...args]);}
async function waitFor(check,label){let last;for(let n=0;n<80;n++){try{const value=await check();if(value)return value;}catch(error){last=error;}await delay(250);}throw Error(label+' not ready'+(last?': '+last.message:''));}
async function json(response){const value=await response.json();if(!response.ok)throw Error('HTTP '+response.status+' '+JSON.stringify(value));return value;}
async function invoke(client,name,args={}){const result=await client.callTool({name,arguments:args});return JSON.parse(result.content[0].text);}
async function systemCheck(api,worker){const id='mcp-phase-'+randomBytes(8).toString('hex');docker(['exec',api,'node','-e',`const {DurableQueue}=require('/app/apps/worker/dist/apps/worker/src/queue.js');new DurableQueue('/data/jobs').enqueue('system.check',{phase:'mcp'},${JSON.stringify(id)}).catch(()=>process.exit(1))`]);await waitFor(()=>{try{return docker(['exec',api,'node','-e',`const fs=require('fs');const p='/data/jobs/done/'+${JSON.stringify(id)}+'.json';const x=JSON.parse(fs.readFileSync(p));if(!x.result?.ok)process.exit(1);`])===''&&docker(['inspect','--format','{{.State.Running}}',worker])==='true';}catch{return false;}},'Worker system.check');}
async function authenticate(origin,role,uid){let info,tokens,verifier,authorization;const nonce=randomBytes(16).toString('hex');
 const provider={redirectUrl:'http://127.0.0.1:47911/callback',clientMetadata:{client_name:'Docker official SDK '+role,redirect_uris:['http://127.0.0.1:47911/callback'],response_types:['code'],grant_types:['authorization_code','refresh_token'],token_endpoint_auth_method:'none',scope:'read write'},state:()=>nonce,clientInformation:()=>info,saveClientInformation:value=>{info=value;},tokens:()=>tokens,saveTokens:value=>{tokens=value;},saveCodeVerifier:value=>{verifier=value;},codeVerifier:()=>verifier,redirectToAuthorization:value=>{authorization=value;}};
 const transport=new StreamableHTTPClientTransport(new URL(origin+'/mcp'),{authProvider:provider}),first=new Client({name:'docker-oauth-fixture',version:'1'});let rejected=false;try{await first.connect(transport);}catch{rejected=true;}
 if(!rejected||!authorization||!info?.client_id)throw Error('Official SDK did not reach authorization');clientIds.push(info.client_id);
 const loginResponse=await fetch(origin+'/api/auth/login',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({username:'mcp-docker-'+role,password:'mcp-docker-fixture-password'})}),session=await json(loginResponse),cookie=loginResponse.headers.getSetCookie().map(value=>value.split(';')[0]).join('; ');
 const approval=await json(await fetch(origin+'/api/mcp-oauth/authorize',{method:'POST',headers:{'content-type':'application/json','x-csrf-token':session.csrfToken,cookie},body:JSON.stringify({request:Object.fromEntries(authorization.searchParams),approved:true})}));const redirected=new URL(approval.redirect);if(redirected.searchParams.get('state')!==nonce)throw Error('State changed');await transport.finishAuth(redirected.searchParams.get('code'));await first.close();
 const client=new Client({name:'docker-connected-fixture',version:'1'});clients.push(client);await client.connect(new StreamableHTTPClientTransport(new URL(origin+'/mcp'),{authProvider:provider}));return {client,get tokens(){return tokens;},uid};}
try{
 for(const component of ['api','gateway'])if(docker(['image','inspect','--format','{{.Id}}',manifest.images[component]])!==manifest.imageDigests[component])throw Error('Candidate image digest mismatch');
 const newNet=prefix+'-new',oldNet=prefix+'-old';for(const network of [newNet,oldNet]){docker(['network','create',network]);networks.push(network);}
 const volume=prefix+'-jobs';docker(['volume','create',volume]);volumes.push(volume);
 docker(['run','--rm','--user','0:0','-v',volume+':/data/jobs',manifest.images.api,'node','-e','require("fs").chownSync("/data/jobs",1000,1000)']);
 const edgeDir=join(temporary,'edge');await mkdir(edgeDir);const edgePath=join(edgeDir,'default.conf');await writeFile(edgePath,edgeConfig(release));
 const gatewayBaseImage='nginx:1.28-alpine';launch(names.edge,['--network',newNet,'-p','127.0.0.1::80','-v',edgeDir+':/etc/nginx/conf.d:ro',gatewayBaseImage]);docker(['network','connect',oldNet,names.edge]);
 const port=docker(['port',names.edge,'80/tcp']).split(':').at(-1),origin='http://127.0.0.1:'+port;
 const containerUrl=new URL(target);containerUrl.hostname='host.docker.internal';
 const envPath=join(temporary,'runtime.env');await writeFile(envPath,[`NEXT_DATABASE_URL=${containerUrl.href}`,'NEXT_SESSION_SECRET=mcp-docker-fixture-session-secret-40-characters','NEXT_COOKIE_SECURE=false','NEXT_HOST=0.0.0.0','NEXT_PORT=3000','NEXT_LOCAL_HTTP=true','NODE_ENV=production','NEXT_TASK_DIRECTORY=/data/jobs','NEXT_UPLOAD_DIRECTORY=/data/jobs/uploads','NEXT_WORKER_RECONCILE=false','STORE_AUTO_MERGE_EXACT=0',`NEXT_MCP_PUBLIC_URL=${origin}/mcp`,`NEXT_MCP_AUTHORIZATION_UI_URL=${origin}/admin/mcp-authorize`].join('\n')+'\n',{mode:0o600});
 const apiArgs=(net,image)=>['--network',net,'--network-alias','api','--env-file',envPath,'-v',volume+':/data/jobs',image];
 launch(names.api,apiArgs(newNet,manifest.images.api));launch(names.gateway,['--network',newNet,'--network-alias','next-'+release,manifest.images.gateway]);
 await waitFor(async()=>{const response=await fetch(origin+'/api/health/ready',{headers:{connection:'close'},signal:AbortSignal.timeout(1500)});return response.ok&&response.headers.get('x-store-release')===release;},'candidate API/gateway');
 docker(['exec',names.edge,'nginx','-t']);docker(['exec',names.gateway,'nginx','-t']);
 launch(names.worker,[...apiArgs(newNet,manifest.images.api),'node','apps/worker/dist/apps/worker/src/main.js']);await systemCheck(names.api,names.worker);
 const passwordHash=await hash('mcp-docker-fixture-password');await db.query('INSERT INTO persons(code,display_name,created_at) VALUES($1,$1,now())',[code]);
 for(const role of ['staff','leader','admin'])users.push((await db.query(`INSERT INTO users(username,password_hash,display_name,person_code,role,status,is_active,must_change_password,lang,created_at) VALUES($1,$2,$1,$3,$4,'active',true,false,'zh',now()) RETURNING id`,['mcp-docker-'+role,passwordHash,role==='staff'?code:null,role])).rows[0].id);
 const sessions=[];for(const [i,role]of ['staff','leader','admin'].entries()){const session=await authenticate(origin,role,users[i]);sessions.push(session);const list=await session.client.listTools();if(list.tools.length!==[6,10,27][i])throw Error('Role tools mismatch');}
 const staff=sessions[0],admin=sessions[2];if((await invoke(staff.client,'visit_my_perf')).data.person_code!==code)throw Error('Wrong personal binding');if((await invoke(staff.client,'visit_person',{person:'other'})).error.code!=='FORBIDDEN_TOOL')throw Error('Role escalation');
 if(!(await invoke(staff.client,'visit_self_report',{area:'Docker isolated',p1_cnt:1,p2_cnt:1})).ok)throw Error('Representative write failed');
 await db.query("INSERT INTO payroll_period_rows(month,person_code,half1_amount,half2_amount,updated_at) VALUES('2095-10',$1,1000,2000,now())",[code]);
 const exported=await invoke(admin.client,'visit_payroll_export',{month:'2095-10',seq:1,dry_run:true});if(!exported.ok||exported.data.ledger.registered!==0)throw Error('Preview registered payment');
 const download=await fetch(origin+exported.data.download_url,{headers:{authorization:'Bearer '+admin.tokens.access_token}});const bytes=Buffer.from(await download.arrayBuffer());if(download.status!==200||createHash('sha256').update(bytes).digest('hex')!==exported.data.sha256)throw Error('Private export download mismatch');
 docker(['restart',names.api]);await waitFor(async()=>{const r=await fetch(origin+'/api/health/ready',{signal:AbortSignal.timeout(1500)});return r.ok;},'restarted API');
 if((await fetch(origin+exported.data.download_url,{headers:{authorization:'Bearer '+admin.tokens.access_token}})).status!==200)throw Error('Private file did not survive API restart');
 const missing=await fetch(origin+'/assets/not-real.js');if(missing.status!==404)throw Error('Missing static asset fell into SPA');
 const callbackServer=createServer((req,res)=>{res.setHeader('content-type','text/plain');res.end('OAuth callback received');});callback=callbackServer;await new Promise(resolve=>callbackServer.listen(0,'127.0.0.1',resolve));const callbackOrigin='http://127.0.0.1:'+callbackServer.address().port;
 await new Promise((resolve,reject)=>{browserProcess=spawn(process.execPath,['scripts/verify-mcp-oauth-browser.mjs'],{env:{...process.env,NEXT_BROWSER_BASE_URL:origin,NEXT_BROWSER_ISOLATED_RUNTIME:'1',NEXT_MCP_CALLBACK_BASE_URL:callbackOrigin,NEXT_BROWSER_DOCKER_RELEASE:release},stdio:'inherit'});browserProcess.once('error',reject);browserProcess.once('exit',code=>{browserProcess=undefined;code===0?resolve():reject(Error('Docker browser flow failed'));});});
 // Revert the independent edge to the previous gateway/API, using the same disposable DB and volume.
 launch(names.oldApi,apiArgs(oldNet,previous.images.api));launch(names.oldGateway,['--network',oldNet,'--network-alias','next-'+active,previous.images.gateway]);
 docker(['stop','-t','10',names.worker]);launch(names.oldWorker,[...apiArgs(oldNet,previous.images.api),'node','apps/worker/dist/apps/worker/src/main.js']);
 await writeFile(edgePath,edgeConfig(active));docker(['exec',names.edge,'nginx','-t']);docker(['exec',names.edge,'nginx','-s','reload']);
 await waitFor(async()=>{const response=await fetch(origin+'/api/health/ready',{headers:{connection:'close'},signal:AbortSignal.timeout(1500)});return response.ok&&response.headers.get('x-store-release')===active;},'rollback gateway');
 if((await fetch(origin+'/.well-known/oauth-authorization-server',{headers:{connection:'close'}})).status!==404)throw Error('Old gateway rollback must expose explicit capability loss');await systemCheck(names.oldApi,names.oldWorker);
 docker(['stop','-t','10',names.oldWorker]);docker(['start',names.worker]);await writeFile(edgePath,edgeConfig(release));docker(['exec',names.edge,'nginx','-s','reload']);
 await waitFor(async()=>{const response=await fetch(origin+'/.well-known/oauth-authorization-server',{headers:{connection:'close'},signal:AbortSignal.timeout(1500)});return response.ok&&response.headers.get('x-store-release')===release;},'redeployed protocol gateway');await systemCheck(names.api,names.worker);
 if((await fetch(origin+exported.data.download_url,{headers:{authorization:'Bearer '+admin.tokens.access_token,connection:'close'}})).status!==200)throw Error('Redeploy lost export/session');
 if(!(await readFile(statePath)).equals(stateBytes))throw Error('Active local release pointer changed');
 const result={ok:true,release,previous:active,scope:'independent Docker candidate; 8000 unchanged',origin,images:manifest.imageDigests,actualGatewayOAuthSdk:true,roleToolCounts:[6,10,27],isolatedPersonalWrite:true,privateDownloadAndRestartPersistence:true,workerSystemCheck:true,oldGatewayRollback404:true,reapplyAndSessionPreserved:true,sharedPrivateVolume:true,staffReused:manifest.componentScope.staff,localStateUnchanged:true,workBuddyVerified:false};await writeFile('data/refactor/mcp-docker-acceptance.json',JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result));
}finally{
 await Promise.allSettled(clients.map(client=>client.close()));browserProcess?.kill('SIGTERM');if(callback)await new Promise(resolve=>{callback.close(resolve);callback.closeAllConnections();});
 // All listed objects were created by this invocation; never prune other Docker objects.
 for(const name of [...containers].reverse())spawnSync('docker',['rm','-f',name],{stdio:'ignore',timeout:20000});for(const network of networks.reverse())spawnSync('docker',['network','rm',network],{stdio:'ignore',timeout:20000});for(const volume of volumes)spawnSync('docker',['volume','rm',volume],{stdio:'ignore',timeout:20000});
 await db.end();await rm(temporary,{recursive:true,force:true});console.log('[mcp-docker] Removed this acceptance run’s containers, networks, volume and temporary files');
}
