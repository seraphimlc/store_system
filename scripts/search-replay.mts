/** Compare current/archived SQL on the same local snapshot; no service logging or writes. */
import {createRequire} from 'node:module';import {createHash} from 'node:crypto';import {createReadStream} from 'node:fs';import {createInterface} from 'node:readline';
import {readFile,writeFile,realpath,mkdir} from 'node:fs/promises';import {resolve,join,relative} from 'node:path';import {pathToFileURL} from 'node:url';import {performance} from 'node:perf_hooks';
import {mobileVisits} from '../apps/server/src/mobile-visits';import {normalizeStoreQuery,STORE_SEARCH_VERSION} from '../packages/domain/src/store-search';
const {Pool}=createRequire(new URL('../apps/server/package.json',import.meta.url))('pg');
const directory=await realpath(resolve(process.argv[2]??'')),root=await realpath('data/refactor/search-evaluation');if(relative(root,directory).startsWith('..')||directory===root)throw Error('A frozen local evaluation directory is required');
const manifest=JSON.parse(await readFile(join(directory,'manifest.json'),'utf8'));if(!manifest.complete||manifest.database!=='store_settle')throw Error('Incomplete/nonlocal snapshot');
const url=new URL(process.env.NEXT_DATABASE_URL??'');if(url.pathname!=='/store_settle'||!['localhost','127.0.0.1','[::1]'].includes(url.hostname))throw Error('Only local store_settle allowed');url.searchParams.set('options','-c default_transaction_read_only=on');
const hash=(v:string)=>createHash('sha256').update(v).digest('hex');
for(const name of ['task-snapshots.jsonl','scope.jsonl']){const digest=createHash('sha256');for await(const bytes of createReadStream(join(directory,name)))digest.update(bytes);if(manifest.files.find((f:any)=>f.file===name)?.sha256!==digest.digest('hex'))throw Error('Frozen file hash mismatch');}
const unique=new Map<string,any>();
for await(const line of createInterface({input:createReadStream(join(directory,'task-snapshots.jsonl')),crlfDelay:Infinity})){const row=JSON.parse(line),name=normalizeStoreQuery(row.snapshot?.name??'');if(name.length>=3&&name.length<=60&&!unique.has(name))unique.set(name,row);}
const rows=[...unique].sort(([a],[b])=>hash(a).localeCompare(hash(b))).slice(0,30).map(([,row])=>row);
const samples=rows.flatMap(row=>{const name=row.snapshot.name;const wide=[...name.normalize('NFKC')].map(c=>c.codePointAt(0)!>=33&&c.codePointAt(0)!<=126?String.fromCodePoint(c.codePointAt(0)!+0xfee0):c).join('　');return [name,wide].map((q,index)=>({query:q,grid:row.grid_task_id,target:row.id,kind:index?'derived-width-spacing':'snapshot-original',split:parseInt(hash(normalizeStoreQuery(name)).slice(0,8),16)%5===0?'holdout':'tuning',label:'derived from known task snapshot; not a human search/identity label',source:row}));});
const frozenTasksHash=manifest.files.find((f:any)=>f.file==='task-snapshots.jsonl')?.sha256;
for(const row of rows){const address=normalizeStoreQuery(row.snapshot?.address??'');if(address.length>=3){const name=normalizeStoreQuery(row.snapshot.name);samples.push({query:name+' '+address.slice(0,5),grid:row.grid_task_id,target:row.id,kind:'derived-name-address',split:parseInt(hash(name).slice(0,8),16)%5===0?'holdout':'tuning',label:'derived task name/address combination; not a human relevance label',source:row});}}
const scope:any[]=[];for await(const line of createInterface({input:createReadStream(join(directory,'scope.jsonl')),crlfDelay:Infinity}))scope.push(JSON.parse(line));const closed=new Map(scope.map(row=>[row.id,['completed','disabled'].includes(row.batch_status)]));
if(manifest.referenceSql&&hash(await readFile(join(directory,manifest.referenceSql.file),'utf8'))!==manifest.referenceSql.sha256)throw Error('Archived reference SQL hash mismatch');
const legacy=(await import(pathToFileURL(join(directory,'mobile-visits.legacy.ts')).href)).mobileVisits;
const negativeSamples=Array.from({length:20},(_,i)=>({query:'not-a-store-'+hash(String(i)).slice(0,24),grid:rows[i%rows.length].grid_task_id,expectedEmpty:true}));
const pool=new Pool({connectionString:url.href,max:5,application_name:'store-search-replay-readonly'}),results:any[]=[];
function metrics(items:any[]){if(!items.length)return {n:0,recallAt10:null,mrrAt10:null,firstRankRate:null};return {n:items.length,recallAt10:items.filter(x=>x.rank>0&&x.rank<=10).length/items.length,mrrAt10:items.reduce((n,x)=>n+(x.rank>0&&x.rank<=10?1/x.rank:0),0)/items.length,firstRankRate:items.filter(x=>x.rank===1).length/items.length};}
try{
 const c=await pool.connect();try{await c.query('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY');
 const liveHash=createHash('sha256');await c.query("DECLARE corpus_cursor NO SCROLL CURSOR FOR SELECT id,grid_task_id,store_id,snapshot,status,result->>'business_status' AS business_status FROM bd_store_task ORDER BY id");
 while(true){const batch=(await c.query('FETCH 2000 FROM corpus_cursor')).rows;if(!batch.length)break;for(const row of batch)liveHash.update(JSON.stringify(row)+'\n');}await c.query('CLOSE corpus_cursor');
 if(liveHash.digest('hex')!==frozenTasksHash)throw Error('Frozen candidate corpus changed; capture a fresh baseline');
 const liveScope=(await c.query('SELECT g.id,g.grid_id,g.region_task_id,g.snapshot,r.main_task_id,m.status AS batch_status FROM bd_grid_task g JOIN bd_region_task r ON r.id=g.region_task_id JOIN bd_main_task m ON m.id=r.main_task_id ORDER BY g.id')).rows;
 if(hash(liveScope.map((row:any)=>JSON.stringify(row)+'\n').join(''))!==manifest.files.find((f:any)=>f.file==='scope.jsonl')?.sha256)throw Error('Scope changed; capture a fresh baseline');
 const ids=rows.map(r=>r.id);const live=(await c.query("SELECT id,grid_task_id,store_id,snapshot,status,result->>'business_status' AS business_status FROM bd_store_task WHERE id=ANY($1::int[]) ORDER BY id",[ids])).rows;
 if(hash(JSON.stringify(live))!==hash(JSON.stringify([...rows].sort((a,b)=>a.id-b.id))))throw Error('Sample task snapshots changed; freeze a fresh baseline');
 for(const sample of samples){const input={q:sample.query,state:'',businessStatus:'',page:1};const before=await legacy(c,sample.grid,input,closed.get(sample.grid)??false),after=await mobileVisits(c,sample.grid,input,closed.get(sample.grid)??false);const rank=(r:any)=>{const i=r.items.findIndex((s:any)=>s.id===sample.target);return i<0?0:i+1;};const {source,...rest}=sample;results.push({...rest,before:{rank:rank(before),total:before.total},after:{rank:rank(after),total:after.total}});}
 for(const sample of negativeSamples){const input={q:sample.query,state:'',businessStatus:'',page:1};const before=await legacy(c,sample.grid,input,closed.get(sample.grid)??false),after=await mobileVisits(c,sample.grid,input,closed.get(sample.grid)??false);if(before.total||after.total)throw Error('Unexpected synthetic negative result');}
 await c.query('COMMIT');}catch(e){await c.query('ROLLBACK');throw e;}finally{c.release();}
 const largest=(await pool.query('SELECT grid_task_id,count(*)::int AS n FROM bd_store_task GROUP BY grid_task_id ORDER BY n DESC,grid_task_id LIMIT 1')).rows[0];
 const benchmark:any={measurement:'SQL helper wall time, excluding HTTP/auth/browser; run separately from browser checks',samplesPerStrategy:200,concurrency:5,grid:largest.grid_task_id,stores:largest.n,writes:false};
 const blockReports:any[]=[];
 if(!process.argv.includes('--quality-only')){
 const strategies=[['legacy',legacy],['current',mobileVisits]]as const;
 const timings:Record<string,Record<string,number[]>>={legacy:{keyword:[],noKeyword:[]},current:{keyword:[],noKeyword:[]}};
 for(const [workload,q]of [['noKeyword',''],['keyword','店']]as const){
  for(const [,fn]of strategies)for(let i=0;i<10;i++){const c=await pool.connect();try{await fn(c,largest.grid_task_id,{q,state:'',businessStatus:'',page:1},closed.get(largest.grid_task_id)??false);}finally{c.release();}}
  for(let block=0;block<10;block++)for(const [name,fn]of block%2?[...strategies].reverse():strategies){let index=0;const samples:number[]=[];
   await Promise.all(Array.from({length:5},async()=>{while(index<20){index++;const c=await pool.connect();try{const t=performance.now();await fn(c,largest.grid_task_id,{q,state:'',businessStatus:'',page:1},closed.get(largest.grid_task_id)??false);const ms=performance.now()-t;timings[name][workload].push(ms);samples.push(ms);}finally{c.release();}}}));
   blockReports.push({workload,block,strategy:name,samplesMs:samples});
  }
 }
 const stats=(values:number[])=>{const data=[...values].sort((a,b)=>a-b);return {n:data.length,p50:data[Math.floor(data.length*.5)],p95:data[Math.floor(data.length*.95)]};};
 benchmark.samplesPerStrategy=400;benchmark.samplesPerWorkloadPerStrategy=200;benchmark.scheduling='separate keyword/no-keyword workloads; 10 paired blocks of 20, alternating strategy order, 5 workers';
 for(const [name]of strategies){const data=timings[name];benchmark[name]={...stats([...data.keyword,...data.noKeyword]),keyword:stats(data.keyword),noKeyword:stats(data.noKeyword)};}
 benchmark.gates={noKeywordP95Ratio:benchmark.current.noKeyword.p95/benchmark.legacy.noKeyword.p95,noKeywordRegressionAtMost10Percent:benchmark.current.noKeyword.p95<=benchmark.legacy.noKeyword.p95*1.1,httpApiLatency:'not measured; SQL timing does not prove the HTTP gate'};
 }else{benchmark.skipped='quality-only; previously passed, unaffected checks are not rerun';benchmark.samplesPerStrategy=0;}

 const groups:any={};for(const kind of ['all','snapshot-original','derived-width-spacing','derived-name-address'])for(const split of ['all','tuning','holdout']){const group=results.filter(r=>(kind==='all'||r.kind===kind)&&(split==='all'||r.split===split));groups[kind+'/'+split]={before:metrics(group.map(r=>r.before)),after:metrics(group.map(r=>r.after))};}
 const report={negativeSamples:{n:negativeSamples.length,beforeEmpty:negativeSamples.length,afterEmpty:negativeSamples.length,label:'synthetic impossible-name checks, not verified real absent merchants'},snapshot:manifest.version,source:'local synthetic-derived samples; no tier-one correctness claims',searchVersion:STORE_SEARCH_VERSION,legacySqlHash:hash(await readFile(join(directory,'mobile-visits.legacy.ts'),'utf8')),currentSqlHash:hash(await readFile('apps/server/src/mobile-visits.ts','utf8')),groups,benchmark,regressions:results.filter(r=>r.before.rank>0&&(r.after.rank===0||r.after.rank>r.before.rank)),productionAccessed:false};
 const runDirectory=join(directory,'runs',new Date().toISOString().replace(/[:.]/g,'-'));await mkdir(runDirectory,{recursive:true,mode:0o700});
 await writeFile(join(runDirectory,'samples.json'),JSON.stringify(results,null,2),{mode:0o600,flag:'wx'});await writeFile(join(runDirectory,'timings.json'),JSON.stringify(blockReports,null,2),{mode:0o600,flag:'wx'});await writeFile(join(runDirectory,'report.json'),JSON.stringify(report,null,2),{mode:0o600,flag:'wx'});console.log(JSON.stringify({runDirectory,...report},null,2));
}finally{await pool.end();}
