import { spawnSync } from 'node:child_process';
import { readFile, writeFile } from 'node:fs/promises';
const release=process.argv[2];
if(!/^[a-z0-9-]+$/.test(release??''))throw new Error('Expected deployed local release ID');
const manifest=JSON.parse(await readFile(`.release/${release}/manifest.json`,'utf8'));
const compose=['compose','-p','store-next-'+release,'-f',`.release/${release}/compose.json`];
function run(args){const r=spawnSync('docker',args,{encoding:'utf8',timeout:90000});if(r.status!==0)throw new Error('Worker verification command failed');return r.stdout.trim();}
run([...compose,'stop','worker']);
const id='restart-probe-'+Date.now();
const code=`const {DurableQueue}=require('/app/apps/worker/dist/apps/worker/src/queue.js');(async()=>{const q=new DurableQueue('/data/jobs');await q.enqueue('system.check',{probe:true},'${id}');const t=await q.claim();if(t.id!=='${id}')throw Error('Unexpected claim');})().catch(()=>process.exit(1));`;
run(['run','--rm','--user','1000:1000','-v','store-next-jobs:/data/jobs',manifest.images.api,'node','-e',code]);
run([...compose,'--profile','worker','up','-d','--wait','--wait-timeout','60','worker']);
const task=JSON.parse(run([...compose,'exec','-T','worker','node','-e',`process.stdout.write(require('fs').readFileSync('/data/jobs/done/${id}.json','utf8'))`]));
if(task.attempts!==2||task.result?.ok!==true)throw new Error('Interrupted task was not recovered');
// Forced process death leaves its lock behind. Container restart must recover this lock safely.
run([...compose,'kill','-s','SIGKILL','worker']);
run([...compose,'--profile','worker','up','-d','--wait','--wait-timeout','60','worker']);
const result={release,recoveredTask:true,attempts:task.attempts,forcedRestartHealthy:true};
await writeFile('data/refactor/p2-worker.json',JSON.stringify(result,null,2)+'\n');
console.log(JSON.stringify(result,null,2));
