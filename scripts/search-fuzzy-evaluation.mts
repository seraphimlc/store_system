/** Offline experiment only: extensions and rows live exclusively in the existing disposable test DB. */
import {createRequire} from 'node:module';import {createHash} from 'node:crypto';
import {createReadStream} from 'node:fs';import {createInterface} from 'node:readline';
import {readFile,realpath,mkdir,writeFile} from 'node:fs/promises';import {relative,join} from 'node:path';import {performance} from 'node:perf_hooks';
import {normalizeStoreQuery,storeMatchTier,storeQueryTerms,STORE_SEARCH_VERSION} from '../packages/domain/src/store-search';
const {Pool}=createRequire(new URL('../apps/server/package.json',import.meta.url))('pg');
export function fuzzyEvaluationTarget(value:string,isolated:string|undefined){const url=new URL(value);if(isolated!=='1'||!['postgres:','postgresql:'].includes(url.protocol)||!['localhost','127.0.0.1','[::1]'].includes(url.hostname)||!/^store_test_\d{13}_[a-f0-9]{12}_refactor_p1$/.test(url.pathname.slice(1)))throw Error('Fuzzy experiment requires a disposable local test database');return url.href;}
export function fuzzyNameEligible(query:string){const q=normalizeStoreQuery(query),length=[...q].length;return length>=5&&length<=64&&storeQueryTerms(query).length===1&&/^[\p{L}\p{M}ー]+$/u.test(q);}
const hash=(value:string)=>createHash('sha256').update(value).digest('hex');
export async function evaluateFuzzyNames(connection:string,datasetPath:string){
 const target=fuzzyEvaluationTarget(connection,process.env.NEXT_TEST_ISOLATED),root=await realpath('data/refactor/search-evaluation'),directory=await realpath(datasetPath),rel=relative(root,directory);
 if(!rel||rel.startsWith('..'))throw Error('Dataset must be inside the private local search-evaluation directory');
 const manifest=JSON.parse(await readFile(join(directory,'manifest.json'),'utf8'));if(!manifest.complete||manifest.database!=='store_settle'||manifest.readOnly!==true)throw Error('A complete read-only local snapshot is required');
 const tasks:any[]=[],digest=createHash('sha256');
 for await(const line of createInterface({input:createReadStream(join(directory,'task-snapshots.jsonl')),crlfDelay:Infinity})){digest.update(line+'\n');tasks.push(JSON.parse(line));}
 if(digest.digest('hex')!==manifest.files.find((f:any)=>f.file==='task-snapshots.jsonl')?.sha256)throw Error('Task snapshot checksum mismatch');
 const names=new Map<string,any>();for(const task of tasks){const name=normalizeStoreQuery(task.snapshot?.name??'');if(fuzzyNameEligible(name)&&!names.has(name))names.set(name,task);}
 const selected=[...names.entries()].sort(([a],[b])=>hash(a).localeCompare(hash(b))).slice(0,24).map(([,task])=>task);if(selected.length<20)throw Error('Too few eligible frozen names for this experiment');
 const grids=new Set(selected.map(task=>task.grid_task_id)),corpus=tasks.filter(task=>grids.has(task.grid_task_id)),byGrid=new Map<number,any[]>();
 for(const task of corpus){const rows=byGrid.get(task.grid_task_id)??[];rows.push(task);byGrid.set(task.grid_task_id,rows);}
 const queries:any[]=[];
 for(const task of selected){const name=normalizeStoreQuery(task.snapshot.name),chars=[...name],at=Math.floor(chars.length/2);
  const variants=[{kind:'original',q:name},{kind:'deletion',q:chars.filter((_,i)=>i!==at).join('')},{kind:'substitution',q:chars.map((c,i)=>i===at?(c==='誤'?'異':'誤'):c).join('')},{kind:'insertion',q:[...chars.slice(0,at),'誤',...chars.slice(at)].join('')}];
  for(const variant of variants)queries.push({id:queries.length+1,grid:task.grid_task_id,target:task.id,...variant,eligible:fuzzyNameEligible(variant.q)});
 }
 for(let i=0;i<24;i++)queries.push({id:queries.length+1,grid:selected[i%selected.length].grid_task_id,target:null,kind:'synthetic-negative',q:'zznotashop'+hash(String(i)).slice(0,18).replace(/[0-9]/g,'z'),eligible:true});
 const pool=new Pool({connectionString:target,max:1,application_name:'store-fuzzy-disposable-experiment'}),c=await pool.connect();const observations:any[]=[];
 try{
  await c.query('CREATE EXTENSION IF NOT EXISTS pg_trgm');await c.query('CREATE EXTENSION IF NOT EXISTS fuzzystrmatch');
  await c.query('BEGIN');await c.query("SET LOCAL statement_timeout='60s'");
  await c.query('CREATE TEMP TABLE experiment_names(id integer PRIMARY KEY,grid integer NOT NULL,name text NOT NULL) ON COMMIT DROP');
  await c.query(`INSERT INTO experiment_names SELECT id,grid,coalesce(name,'') FROM jsonb_to_recordset($1::jsonb) AS x(id integer,grid integer,name text)`,[JSON.stringify(corpus.map(t=>({id:t.id,grid:t.grid_task_id,name:normalizeStoreQuery(t.snapshot?.name??'')})))]);
  await c.query('CREATE INDEX ON experiment_names(grid)');await c.query('ANALYZE experiment_names');
  for(const query of queries){
   const literal=(byGrid.get(query.grid)??[]).map(row=>({id:row.id,tier:storeMatchTier(query.q,{storeId:row.store_id,name:row.snapshot?.name??'',address:row.snapshot?.address??''})})).filter(row=>row.tier!==null).sort((a,b)=>a.tier!-b.tier!||a.id-b.id);
   const start=performance.now();
   const candidates=query.eligible?(await c.query(`WITH scores AS(SELECT id,name,similarity(name,$2) AS similarity,CASE WHEN char_length(name) BETWEEN 1 AND 64 AND abs(char_length(name)-char_length($2))<=1 THEN levenshtein_less_equal(name,$2,1) END AS edits FROM experiment_names WHERE grid=$1) SELECT * FROM scores WHERE similarity>=0.3 OR edits<=1 ORDER BY similarity DESC,id`,[query.grid,query.q])).rows:[];
   const elapsedMs=performance.now()-start;
   const policies=[{name:'trigram-0.30',select:(r:any)=>r.similarity>=.3},{name:'trigram-0.45',select:(r:any)=>r.similarity>=.45},{name:'trigram-0.60',select:(r:any)=>r.similarity>=.6},{name:'one-edit',select:(r:any)=>r.edits!==null&&r.edits<=1}];
   const rank=(items:any[])=>{const i=items.findIndex(row=>row.id===query.target);return i<0?0:i+1;};
   observations.push({...query,literal:{count:literal.length,rank:rank(literal)},elapsedMs,policies:policies.map(policy=>{const matches=candidates.filter(policy.select).sort((a:any,b:any)=>policy.name==='one-edit'?a.edits-b.edits||b.similarity-a.similarity||a.id-b.id:b.similarity-a.similarity||a.id-b.id);const fallback=literal.length?literal:matches;return {policy:policy.name,candidateCount:matches.length,returnedCount:fallback.length,rank:rank(fallback),multipleDistinctNames:new Set(matches.map((r:any)=>r.name)).size>1};})});
  }
  const protections=(await c.query("SELECT levenshtein_less_equal('新宿中央薬局','新橋中央薬局',1) AS distinct_merchant_edit_distance,similarity('新宿中央薬局','新橋中央薬局') AS distinct_merchant_similarity")).rows[0];
  const summary:any[]=[];
  for(const policy of ['trigram-0.30','trigram-0.45','trigram-0.60','one-edit'])for(const kind of ['original','deletion','substitution','insertion','synthetic-negative']){
   const group=observations.filter(r=>r.kind===kind),eligible=group.filter(r=>r.eligible),positives=eligible.filter(r=>r.target!==null),negatives=eligible.filter(r=>r.target===null);
   const score=(r:any)=>r.policies.find((p:any)=>p.policy===policy);
   summary.push({policy,kind,queries:group.length,eligible:eligible.length,originalRecallAt10:positives.length?positives.filter(r=>r.literal.rank>0&&r.literal.rank<=10).length/positives.length:null,recallAt10:positives.length?positives.filter(r=>score(r).rank>0&&score(r).rank<=10).length/positives.length:null,negativeReturnRate:negatives.length?negatives.filter(r=>score(r).returnedCount>0).length/negatives.length:null,ambiguousFallbacks:eligible.filter(r=>!r.literal.count&&score(r).multipleDistinctNames).length,maxCandidateCount:Math.max(0,...eligible.map(r=>score(r).candidateCount))});
  }
  const times=observations.filter(r=>r.eligible).map(r=>r.elapsedMs).sort((a,b)=>a-b),report={snapshot:manifest.version,strategy:STORE_SEARCH_VERSION,experiment:'name-only zero-literal-result fallback; offline only, no runtime provider',sampleSource:'24 frozen task names plus insertion/deletion/substitution; derived labels, not user correctness evidence',corpusTasks:corpus.length,grids:grids.size,queries:queries.length,eligibility:'single word, 5-64 Unicode characters, letters/marks only; short/numeric/punctuation inputs excluded; alphabetic IDs cannot be identified by syntax alone',summary,protectionCounterexample:{...protections,label:'synthetic pair of different names, not verified real merchants'},p95CandidateQueryMs:times[Math.floor(times.length*.95)],fullCandidateCounts:true,productionAccessed:false,baselineDatabaseWritten:false,extensionsInstalledOnlyInDisposableDatabase:true,decision:'Do not enable automatic fuzzy substitution: different names can be one edit apart; synthetic examples do not prove identity; needs reviewed real targets/negative samples or explicit, labeled suggestions.'};
  await c.query('COMMIT');
  const output=join(directory,'fuzzy-experiments',new Date().toISOString().replace(/[:.]/g,'-'));await mkdir(output,{recursive:true,mode:0o700});await writeFile(join(output,'report.json'),JSON.stringify(report,null,2),{mode:0o600,flag:'wx'});await writeFile(join(output,'observations.json'),JSON.stringify(observations,null,2),{mode:0o600,flag:'wx'});
  return {output,report};
 }catch(e){await c.query('ROLLBACK');throw e;}finally{c.release();await pool.end();}
}
