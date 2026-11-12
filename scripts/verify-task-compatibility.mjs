import {spawnSync} from 'node:child_process';import {readFile,writeFile} from 'node:fs/promises';
const state=JSON.parse(await readFile('.release/local-state.json','utf8'));const release=state.current;const manifest=JSON.parse(await readFile(`.release/${release}/manifest.json`,'utf8'));
if(!manifest.supportedTaskKinds?.includes('report.analysis'))throw new Error('Expected an updated task-aware local release');
const args=['compose','-p','store-next-'+release,'-f',`.release/${release}/compose.json`];
function run(command,values,success=true){const result=spawnSync(command,values,{encoding:'utf8',timeout:90000});if(success?result.status!==0:result.status===0)throw new Error('Unexpected verification command status');return result;}
const id='compatibility-probe-'+Date.now();
run('docker',[...args,'stop','worker']);
try{
 const code=`const {DurableQueue}=require('/app/apps/worker/dist/apps/worker/src/queue.js');new DurableQueue('/data/jobs').enqueue('future.unsupported',{},'${id}').catch(()=>process.exit(1));`;
 run('docker',['run','--rm','--user','1000:1000','-v','store-next-jobs:/data/jobs',manifest.images.api,'node','-e',code]);
 const response=await fetch('http://localhost:8080/api/health/ready');const before=response.headers.get('x-store-release');
 const rejected=run(process.execPath,['scripts/release.mjs','deploy','--env','local','--release',release],false);
 if(!rejected.stderr.includes('Unsupported pending task types: future.unsupported'))throw new Error('Wrong failure reason');
 const after=await fetch('http://localhost:8080/api/health/ready');if(after.headers.get('x-store-release')!==before)throw new Error('Incompatible pending work changed traffic');
 const result={release,unsupportedQueuedTaskRejected:true,trafficPreserved:true};await writeFile('data/refactor/p3-task-compatibility.json',JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result,null,2));
}finally{
 const remove=`const fs=require('fs');for(const path of ['/data/jobs/queued/${id}.json','/data/jobs/queued/${id}.tmp','/data/jobs/${id}.identity']){try{fs.unlinkSync(path);}catch(e){if(e.code!=='ENOENT')throw e;}}`;
 run('docker',['run','--rm','--user','1000:1000','-v','store-next-jobs:/data/jobs',manifest.images.api,'node','-e',remove]);
 run('docker',[...args,'--profile','worker','up','-d','--wait','--wait-timeout','60','worker']);
}
