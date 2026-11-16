import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,rm,stat} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {preflight,stageSupport,summarizeTimeline,checkpoint,releaseDirectory,runPhase} from './release-workflow.mjs';
import {prepareBrowserAssets,attachPinnedAssets,networkProbe,requireNetwork} from './release-browser-assets.mjs';
const release='prod-20990101-000000',hash=b=>createHash('sha256').update(b).digest('hex');
async function fixture(){
 const base=await mkdtemp(join(tmpdir(),'release-workflow-')),dir=join(base,release);await mkdir(join(dir,'overlay'),{recursive:true});await mkdir(join(dir,'assets/staff'),{recursive:true});
 const file=Buffer.from('module.exports=1'),html=Buffer.from('<script src="https://cdn.test/app.js"></script>'),js=Buffer.from('console.log("fixture")');
 const manifest={id:release,releaseTag:release,sourceCommit:'a'.repeat(40),dirty:false,platform:'linux/amd64',artifactKind:'image-overlay-v1',migrations:[],html:{'index.html':hash(html)},files:[{path:'api.js',size:file.length,sha256:hash(file)}],assets:[{app:'staff',path:'app.js',url:'https://cdn.test/app.js',size:js.length,sha256:hash(js)}]};
 await writeFile(join(dir,'manifest.json'),JSON.stringify(manifest));await writeFile(join(dir,'overlay/api.js'),file);await writeFile(join(dir,'assets/staff/app.js'),js);
 return {base,dir,manifest,html,js,async close(){await rm(base,{recursive:true,force:true});}};
}
test('local preflight catches tampered files, migrations, duplicate/traversal paths before uploading',async()=>{
 const f=await fixture();try{
  assert.equal((await preflight(release,{base:f.base,git:false})).ok,true);
  await writeFile(join(f.dir,'overlay/api.js'),'changed');await assert.rejects(preflight(release,{base:f.base,git:false}),/checksum/);
  f.manifest.files[0].path='../escape';await writeFile(join(f.dir,'manifest.json'),JSON.stringify(f.manifest));await assert.rejects(preflight(release,{base:f.base,git:false}),/Unsafe/);
  assert.throws(()=>releaseDirectory('../unsafe'));
 }finally{await f.close();}
});
test('support staging includes decrypt dependency, preserves 600 keys and fails on missing local helper',async()=>{
 const root=await mkdtemp(join(tmpdir(),'release-support-')),base=join(root,'releases'),dir=join(base,release);try{
  await mkdir(join(root,'scripts'),{recursive:true});await mkdir(dir,{recursive:true});
  for(const name of ['encrypted-release-bundle.mjs','asset-storage.mjs','deploy-attendance.py','attendance-release-smoke.cjs'])await writeFile(join(root,'scripts',name),name==='encrypted-release-bundle.mjs'?"import x from './asset-storage.mjs';":'fixture');
  await writeFile(join(dir,'encrypted-upload.json'),'{}');await writeFile(join(dir,'decrypt-key.json'),'{}',{mode:0o600});
  const result=await stageSupport(release,{base,root,git:false});assert.equal(result.files.length,6);assert.ok(result.files.some(f=>f.name==='asset-storage.mjs'));assert.equal((await stat(join(dir,'support/decrypt-key.json'))).mode&0o077,0);
  await rm(join(root,'scripts/asset-storage.mjs'));await assert.rejects(stageSupport(release,{base,root,git:false}),/ENOENT/);
 }finally{await rm(root,{recursive:true,force:true});}
});
test('timeline exposes gaps, failed/running stages and does not double count nested steps',async()=>{
 const base=await mkdtemp(join(tmpdir(),'release-timing-'));try{
  await checkpoint(release,'scope','running',{base,now:new Date(0)});await checkpoint(release,'scope','failed',{base,now:new Date(1000)});
  await checkpoint(release,'build','running',{base,now:new Date(6000)});const summary=await checkpoint(release,'build','passed',{base,now:new Date(8000)});
  assert.deepEqual(summary,{wallSeconds:8,activeSeconds:3,gapSeconds:5,failed:1,running:0});
  assert.deepEqual(summarizeTimeline([{startedAt:new Date(0).toISOString(),endedAt:new Date(10000).toISOString(),status:'passed'},{startedAt:new Date(1000).toISOString(),endedAt:new Date(2000).toISOString(),status:'passed'}]),{wallSeconds:10,activeSeconds:10,gapSeconds:0,failed:0,running:0});
  await checkpoint(release,'upload','running',{base,now:new Date(9000)});assert.equal(summarizeTimeline(JSON.parse(await readFile(join(base,release,'workflow-timeline.json'),'utf8')),10000).running,1);
 }finally{await rm(base,{recursive:true,force:true});}
});
test('network probe fails once within budget; no full-suite retry and network failure cannot pass the gate',async()=>{
 let calls=0;const started=performance.now();const result=await networkProbe('https://network.test/',{timeoutMs:25,fetcher:async(_url,{signal})=>{calls++;return new Promise((_,reject)=>{const timer=setTimeout(()=>{},100);signal.addEventListener('abort',()=>{clearTimeout(timer);reject(signal.reason);});});}});
 assert.equal(result.ok,false);assert.equal(result.error,'timeout');assert.equal(calls,1);assert.ok(performance.now()-started<1000);assert.throws(()=>requireNetwork([result]),/Network verification failed/);
});
test('browser fetches each HTML/CDN probe once, pins exact digests, keeps API routing untouched',async()=>{
 const f=await fixture(),calls=[];try{
  const pinned=await prepareBrowserAssets(release,'http://127.0.0.1:18081',{base:f.base,fetcher:async url=>{calls.push(url);return new Response(url.includes('cdn.test')?f.js:f.html,{headers:{'Content-Type':'text/plain'}});}});
  assert.deepEqual(calls,['http://127.0.0.1:18081/','https://cdn.test/app.js']);
  const routes=[];const context={async route(pattern,handler){routes.push({pattern,handler});}};await attachPinnedAssets(context,pinned,'http://127.0.0.1:18081');await attachPinnedAssets(context,pinned,'http://127.0.0.1:18081');assert.equal(calls.length,2,'contexts must not re-fetch network assets');
  let fallback=0;await routes[0].handler({request:()=>({resourceType:()=> 'fetch'}),fallback:()=>fallback++});assert.equal(fallback,1);
  await assert.rejects(prepareBrowserAssets(release,'http://127.0.0.1:18081',{base:f.base,fetcher:async()=>new Response('wrong bytes')}),/Deployed HTML differs/);
 }finally{await f.close();}
});
test('remote helper preflight, smoke ownership, failed/nested timing, bounded download use mocks only',()=>{
 const code=`import importlib.util,tempfile,pathlib,json,io,hashlib\ns=importlib.util.spec_from_file_location('deploy','scripts/deploy-attendance.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)\nwith tempfile.TemporaryDirectory() as t:\n d=m.Deployment.__new__(m.Deployment);d.d=pathlib.Path(t);d.m=None\n def fail():raise RuntimeError('fixture')\n try:d.phase('failed-stage',fail)\n except RuntimeError:pass\n assert json.load(open(d.d/'timings.json'))[0]['status']=='failed'\n d.phase('parent',lambda:d.phase('nested',lambda:None))\n items=json.load(open(d.d/'timings.json'));assert len(items)==3 and all(x['status']=='passed' for x in items[1:])\n try:d.preflight()\n except RuntimeError as e:assert 'Missing release helper' in str(e)\n else:raise AssertionError('missing dependency accepted')\n calls=[]\n def run(*args,**kw):calls.append(args)\n m.run=run;d.smoke('fixture-container','candidate')\n assert any('chown' in a and 'node:node' in a for a in calls)\n assert any('chmod' in a and '400' in a for a in calls)\n data=b'encrypted';receipt={'url':'https://example.test/payload','file':'payload.zip','size':len(data),'sha256':hashlib.sha256(data).hexdigest()}\n def response(url,timeout):assert timeout==20;return io.BytesIO(data)\n m.urllib.request.urlopen=response;m.download_artifact(receipt,d.d/'payload.zip');assert (d.d/'payload.zip').read_bytes()==data\n receipt['sha256']='0'*64\n try:m.download_artifact(receipt,d.d/'bad.zip')\n except ValueError:pass\n else:raise AssertionError('corrupt download accepted')\n assert not (d.d/'bad.zip.pending').exists()\n`;
 const result=spawnSync('python3',['-c',code],{encoding:'utf8'});assert.equal(result.status,0,result.stderr);
});

test('phase command runner records failure exactly once and does not retry',async()=>{
 const base=await mkdtemp(join(tmpdir(),'release-command-'));try{
  await runPhase(release,'success',process.execPath,['-e','process.exit(0)'],{base});
  await assert.rejects(runPhase(release,'failure',process.execPath,['-e','process.exit(7)'],{base}),/failed: 7/);
  const entries=JSON.parse(await readFile(join(base,release,'workflow-timeline.json'),'utf8'));assert.deepEqual(entries.map(e=>e.status),['passed','failed']);
 }finally{await rm(base,{recursive:true,force:true});}
});
