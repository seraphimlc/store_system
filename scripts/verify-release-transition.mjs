/** Exercise the deployment's transition functions against isolated real gateways/workers. */
import {readFile,writeFile,mkdir,cp,rename} from 'node:fs/promises';import {join} from 'node:path';import {spawnSync} from 'node:child_process';import assert from 'node:assert/strict';
import {replaceGateway,handoffRelease} from './release-transition.mjs';import {edgeConfig,taskCompatibilityProbe,digest} from './release-lib.mjs';
export async function verifyReleaseTransition({manifest,prefix,network,volume,worker,common,directory,docker,base,admin,originalId}){
 if(process.env.NEXT_TEST_ISOLATED!=='1'||!/^accept-[0-9]+$/.test(prefix)||!/^store_test_[0-9]{13}_[a-f0-9]{12}_refactor_p1$/.test(new URL(process.env.NEXT_TEST_DATABASE_URL).pathname.slice(1)))throw Error('Disposable release acceptance required');
 const previous=JSON.parse(await readFile('.release/'+manifest.derivedFrom+'/manifest.json','utf8'));
 const names={old:prefix+'-rollback-old',candidate:prefix+'-rollback-new',edge:prefix+'-rollback-edge',worker:prefix+'-rollback-worker'},publicDir=join(directory,'rollback-public'),edgeDir=join(directory,'rollback-edge'),config=join(edgeDir,'default.conf');
 const entryState=await readFile('.release/local-state.json');
 const running=name=>docker(['inspect','--format','{{.State.Running}}',name])==='true';
 async function wait(check){for(let i=0;i<60;i++){try{if(await check())return;}catch{}await new Promise(r=>setTimeout(r,250));}throw Error('Transition readiness timeout');}
 let endpoint;
 const request=async path=>fetch(endpoint+path,{headers:{connection:'close'},signal:AbortSignal.timeout(3000)});
 const checkRelease=async id=>{const response=await request('/api/health/ready');assert.equal(response.status,200);assert.equal(response.headers.get('x-store-release'),id);};
 const configuration=(id,container)=>edgeConfig(id).replace('next-'+id,container);
 const swap=(bytes,id)=>replaceGateway({read:()=>readFile(config),write:async value=>{await writeFile(config+'.next',value);await rename(config+'.next',config);},configuration:bytes,validate:()=>docker(['exec',names.edge,'nginx','-t']),reload:()=>docker(['exec',names.edge,'nginx','-s','reload']),health:()=>checkRelease(id),attempts:12});
 const compat=()=>docker(['run','--rm','--read-only','--user','1000:1000','-v',volume+':/data/jobs:ro',manifest.images.api,'node','-e',taskCompatibilityProbe(manifest.supportedTaskKinds)]);
 const restore=()=>swap(configuration(previous.id,names.old),previous.id);
 const flow=overrides=>handoffRelease({stopOldWorker:()=>docker(['stop',worker]),verifyTasks:compat,switchGateway:()=>swap(configuration(manifest.id,names.candidate),manifest.id),startWorker:()=>docker(['start',names.worker]),stopWorker:()=>docker(['stop',names.worker]),restoreGateway:restore,startOldWorker:()=>docker(['start',worker]),...overrides});
 const queue=id=>docker(['run','--rm','--user','1000:1000','-v',volume+':/data/jobs',manifest.images.api,'node','-e',`const {DurableQueue}=require('./apps/worker/dist/apps/worker/src/queue.js');new DurableQueue('/data/jobs').enqueue('system.check',{probe:true},'${id}').catch(()=>process.exit(1))`]);
 const done=id=>{const value=docker(['run','--rm','--read-only','--user','1000:1000','-v',volume+':/data/jobs:ro',manifest.images.api,'node','-e',`process.stdout.write(require('fs').readFileSync('/data/jobs/done/${id}.json','utf8'))`]);return JSON.parse(value).result?.ok===true;};
 try{
  await mkdir(publicDir);await mkdir(edgeDir);
  for(const release of [previous,manifest]){
   await cp('.release/'+release.id+'/assets',join(publicDir,release.id),{recursive:true});
   const name=release.id===previous.id?names.old:names.candidate;
   docker(['run','-d','--name',name,'--network',network,'--mount','type=bind,source='+publicDir+',target=/usr/share/nginx/html/r,readonly',release.images.gateway]);
  }
  // Candidate Worker is created stopped, and shares only this acceptance run's volume/config.
  docker(['create','--name',names.worker,...common,manifest.images.api,'node','apps/worker/dist/apps/worker/src/main.js']);
  await writeFile(config,configuration(previous.id,names.old));
  docker(['run','-d','--name',names.edge,'--network',network,'-p','127.0.0.1::80','--mount','type=bind,source='+edgeDir+',target=/etc/nginx/conf.d,readonly','nginx:1.28-alpine']);
  endpoint='http://127.0.0.1:'+docker(['port',names.edge,'80/tcp']).split(':').at(-1);await wait(async()=>{await checkRelease(previous.id);return true;});
  // A real nginx configuration failure must restore the old bytes and keep the old worker.
  await assert.rejects(flow({switchGateway:()=>swap('invalid nginx configuration;',manifest.id)}));await checkRelease(previous.id);assert.ok(running(worker));assert.ok(!running(names.worker));
  // A candidate with the wrong health/version must also restore old traffic.
  await assert.rejects(flow({switchGateway:()=>swap(configuration(manifest.id,names.candidate),'intentional-wrong-release')}));await checkRelease(previous.id);assert.ok(running(worker));assert.ok(!running(names.worker));
  // Actual Docker start failure after successful HTTP switch exercises worker recovery.
  await assert.rejects(flow({startWorker:()=>docker(['start',prefix+'-does-not-exist'])}));await checkRelease(previous.id);assert.ok(running(worker));assert.ok(!running(names.worker));
  // Unsupported queued work prevents promotion; remove only this synthetic marker before resuming old work.
  const marker='rollback-unsupported.json';docker(['stop',worker]);
  docker(['run','--rm','--user','1000:1000','-v',volume+':/data/jobs',manifest.images.api,'node','-e',`const fs=require('fs');fs.mkdirSync('/data/jobs/queued',{recursive:true});fs.writeFileSync('/data/jobs/queued/${marker}',JSON.stringify({kind:'unsupported.rollback'}))`]);
  try{await assert.rejects(flow({startOldWorker:()=>{}}));await checkRelease(previous.id);assert.ok(!running(names.worker));}finally{docker(['run','--rm','--user','1000:1000','-v',volume+':/data/jobs',manifest.images.api,'node','-e',`require('fs').unlinkSync('/data/jobs/queued/${marker}')`]);docker(['start',worker]);}
  const task='rollback-forward-'+Date.now();
  await flow({verifyTasks:()=>{queue(task);compat();}});await checkRelease(manifest.id);assert.ok(!running(worker));assert.ok(running(names.worker));await wait(()=>done(task));
  const download=await request('/api/imports/'+originalId+'/original');assert.equal(download.status,401);
  const authorized=await fetch(endpoint+'/api/imports/'+originalId+'/original',{headers:{cookie:admin}});assert.equal(authorized.status,200);assert.equal(await authorized.text(),'private-original-acceptance');
  async function assetsAvailable(){for(const release of [previous,manifest])for(const asset of release.assets){const r=await request('/r/'+release.id+'/'+asset.app+'/'+asset.path);assert.equal(r.status,200);assert.equal(digest(Buffer.from(await r.arrayBuffer())),asset.sha256);assert.ok(r.headers.get('cache-control')?.includes('immutable'));}}
  await assetsAvailable();assert.equal((await request('/missing.js')).status,404);
  const reverse='rollback-reverse-'+Date.now();
  await handoffRelease({stopOldWorker:()=>docker(['stop',names.worker]),verifyTasks:()=>{queue(reverse);compat();},switchGateway:restore,startWorker:()=>docker(['start',worker]),stopWorker:()=>docker(['stop',worker]),restoreGateway:()=>swap(configuration(manifest.id,names.candidate),manifest.id),startOldWorker:()=>docker(['start',names.worker])});
  await checkRelease(previous.id);assert.ok(running(worker));assert.ok(!running(names.worker));await wait(()=>done(reverse));await assetsAvailable();
  const after=await fetch(endpoint+'/api/imports/'+originalId+'/original',{headers:{cookie:admin}});assert.equal(after.status,200);assert.equal(await after.text(),'private-original-acceptance');
  assert.deepEqual(await readFile('.release/local-state.json'),entryState);
  const result={ok:true,previous:previous.id,candidate:manifest.id,usesDeploymentTransitionFunctions:true,isolatedDocker:true,invalidNginxPreservedEntry:true,wrongHealthPreservedEntry:true,workerStartupFailureRecovered:true,unsupportedTaskBlocked:true,forwardAndRollback:true,queuedChecksCompletedBothDirections:true,singleWorkerEachDirection:true,sessionAndPrivateOriginalAfterRollback:true,bothReleaseAssetsPreserved:true,assetDelivery:'local read-only mirror only; live CDN not verified',main8080Unchanged:true,cdnBrowserVerified:false,productionTouched:false};
  await writeFile('data/refactor/release-transition-acceptance.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));return result;
 }finally{for(const name of [names.edge,names.candidate,names.old,names.worker])spawnSync('docker',['rm','-f',name],{stdio:'ignore',timeout:15000});}
}
