/** Temporary source runtime for browser regression. Production Docker/CDN acceptance is separate. */
import {spawn,type ChildProcess} from 'node:child_process';
import {createServer,request as proxyRequest} from 'node:http';
import {mkdtemp,mkdir,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve,extname,sep} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {browserEnvironment,browserSuites} from './test-isolation-lib.mjs';
const temporary=await mkdtemp(join(tmpdir(),'store-browser-'));
const env=browserEnvironment(process.env,process.env.NEXT_TEST_DATABASE_URL,temporary);
const oauthSuite=process.argv[2]==='oauth'||process.argv[2]==='all';
for(const key of Object.keys(process.env))delete process.env[key];Object.assign(process.env,env);
let worker:ChildProcess|undefined,fixture:ChildProcess|undefined,workerExit:Promise<unknown>|undefined,app:any,gateway:ReturnType<typeof createServer>|undefined,interrupted=false;
const stop=()=>{interrupted=true;fixture?.kill('SIGTERM');worker?.kill('SIGTERM');};
for(const signal of ['SIGTERM','SIGINT'])process.on(signal,stop);
async function terminate(child:ChildProcess|undefined,exit:Promise<unknown>|undefined){
 if(!child||!exit||child.exitCode!==null||child.signalCode!==null)return;
 child.kill('SIGTERM');const graceful=await Promise.race([exit.then(()=>true),delay(5000).then(()=>false)]);
 if(!graceful){child.kill('SIGKILL');await exit;}
}
try{
 await mkdir('data/refactor',{recursive:true});
 const {createApp}=await import('../apps/server/src/main');
 const runtimeConfig:any={databaseUrl:env.NEXT_DATABASE_URL,sessionSecret:env.NEXT_SESSION_SECRET,cookieSecure:false,host:'127.0.0.1',port:0};app=await createApp(runtimeConfig);
 await app.listen(0,'127.0.0.1');const api=new URL(await app.getUrl());
 worker=spawn(process.execPath,['--import','tsx','apps/worker/src/main.ts'],{env,stdio:['ignore','inherit','inherit']});
 workerExit=new Promise((resolve,reject)=>{worker!.once('exit',resolve);worker!.once('error',reject);});
 for(let attempt=0;;attempt++){
  if(interrupted||worker.exitCode!==null||worker.signalCode!==null)throw Error('Disposable Worker stopped before readiness');
  try{await readFile(join(temporary,'heartbeat'));break;}catch{if(attempt>=100)throw Error('Disposable Worker readiness timeout');await delay(100);}
 }
 const roots={admin:resolve('apps/admin/dist'),staff:resolve('apps/staff/dist')};
 for(const root of Object.values(roots))await readFile(join(root,'index.html'));
 const types:Record<string,string>={'.html':'text/html; charset=utf-8','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml','.png':'image/png','.json':'application/json','.woff2':'font/woff2'};
 gateway=createServer(async(req,res)=>{
  try{
   const url=new URL(req.url!,'http://localhost');
   if(url.pathname.startsWith('/api/')||oauthSuite&&(url.pathname.startsWith('/mcp')||url.pathname.startsWith('/oauth')||url.pathname.startsWith('/.well-known/'))){
    const upstream=proxyRequest(new URL(req.url!,api),{method:req.method,headers:{...req.headers,host:api.host}},response=>{res.writeHead(response.statusCode??502,response.headers);response.pipe(res);});
    upstream.on('error',()=>{if(!res.headersSent)res.writeHead(502);res.end();});req.on('aborted',()=>upstream.destroy());req.pipe(upstream);return;
   }
   if(url.pathname.startsWith('/mcp')||url.pathname.startsWith('/oauth')||url.pathname.startsWith('/.well-known/')){res.writeHead(404);res.end();return;}
   if(req.method!=='GET'&&req.method!=='HEAD'){res.writeHead(405);res.end();return;}
   const admin=url.pathname==='/admin'||url.pathname.startsWith('/admin/'),root=admin?roots.admin:roots.staff;
   const path=resolve(root,'.'+decodeURIComponent(admin?url.pathname.slice(6)||'/':url.pathname));
   if(path!==root&&!path.startsWith(root+sep)){res.writeHead(404);res.end();return;}
   let body:Buffer,file=path;
   try{body=await readFile(file);}catch{if(extname(path)){res.writeHead(404);res.end();return;}file=join(root,'index.html');body=await readFile(file);}
   res.writeHead(200,{'content-type':types[extname(file)]??'application/octet-stream','cache-control':'no-store'});res.end(req.method==='HEAD'?undefined:body);
  }catch{res.writeHead(500);res.end();}
 });
 await new Promise<void>(resolve=>gateway!.listen(0,'127.0.0.1',resolve));
 const base=`http://127.0.0.1:${(gateway.address() as any).port}`;
 if(oauthSuite){runtimeConfig.mcpPublicUrl=base+'/mcp';runtimeConfig.mcpAuthorizationUiUrl=base+'/admin/mcp-authorize';}
 const browserEnv={...env,NEXT_BROWSER_BASE_URL:base,NEXT_BROWSER_ISOLATED_RUNTIME:'1'};
 for(const file of browserSuites(process.argv[2]??'all')){
  if(interrupted)throw Error('Disposable browser run interrupted');
  const code=await new Promise((resolve,reject)=>{fixture=spawn(process.execPath,[file],{env:browserEnv,stdio:'inherit'});fixture.once('error',reject);fixture.once('exit',(code,signal)=>{fixture=undefined;resolve(signal?130:code);});});
  if(code!==0)throw Error(`Browser regression failed: ${file}`);
  console.log('[browser] Passed '+file);
 }
 console.log('[browser] Selected browser regressions passed against the same disposable database');
}finally{
 await terminate(worker,workerExit);
 if(gateway)await new Promise<void>(resolve=>{gateway!.close(()=>resolve());gateway!.closeAllConnections();});
 await app?.close();await rm(temporary,{recursive:true,force:true});
 for(const signal of ['SIGTERM','SIGINT'])process.off(signal,stop);
}
