/** Offline review only: no database, network or inferred merchant identity. */
import {createHash} from 'node:crypto';import {createReadStream} from 'node:fs';import {createInterface} from 'node:readline';
import {readFile,realpath,mkdir,writeFile} from 'node:fs/promises';import {resolve,relative,join} from 'node:path';import {pathToFileURL} from 'node:url';
const hash=value=>createHash('sha256').update(value).digest('hex');
function positive(value){return Number.isSafeInteger(value)&&value>0;}
export function buildReviewTemplate(requests,dataset){
 const seen=new Set();return requests.map(request=>{if(!positive(request.id)||!positive(request.grid_task_id)||typeof request.query_text!=='string'||seen.has(request.id))throw Error('Invalid or duplicate request identity');seen.add(request.id);
  return {dataset,requestId:request.id,queryHash:hash(request.query_text),query:request.query_text,gridTaskId:request.grid_task_id,requestPage:request.page??1,state:request.task_state??'',businessStatus:request.business_status??'',review:{status:'unknown',targetTaskIds:[],reviewer:'',evidence:'',notes:''}};
 });
}
/** Validates capture-time scope only; evidence of historical scope must be reviewed separately. */
export function validateSearchReviews(templates,annotations,tasks){
 if(!Array.isArray(annotations))throw Error('Annotations must be an array');const expected=new Map(templates.map(row=>[row.requestId,row])),seen=new Set(),validated=[];
 const taskMap=new Map(tasks.map(task=>[task.id,task]));if(taskMap.size!==tasks.length)throw Error('Duplicate frozen task identities');
 for(const row of annotations){const original=expected.get(row.requestId);if(!original||seen.has(row.requestId))throw Error('Unknown or duplicate reviewed request');seen.add(row.requestId);
  for(const field of ['dataset','queryHash','query','gridTaskId','requestPage','state','businessStatus'])if(row[field]!==original[field])throw Error('Review does not match frozen request '+field);
  const review=row.review;if(!review||!['unknown','in_scope','outside_grid','filtered_out','verified_absent'].includes(review.status)||!Array.isArray(review.targetTaskIds)||review.targetTaskIds.some(id=>!positive(id))||new Set(review.targetTaskIds).size!==review.targetTaskIds.length)throw Error('Invalid review state or target identities');
  for(const key of ['reviewer','evidence','notes'])if(typeof review[key]!=='string'||review[key].length>2000)throw Error('Invalid review evidence');
  if(review.status==='unknown'){if(review.targetTaskIds.length)throw Error('Unknown reviews must not contain guessed targets');}
  else{
   if(!review.reviewer.trim()||!review.evidence.trim())throw Error('Reviewed labels require reviewer and explicit evidence');
   if(review.status==='verified_absent'){if(review.targetTaskIds.length)throw Error('Absent label cannot contain target IDs');}
   else{
    if(!review.targetTaskIds.length)throw Error('Reviewed target label requires target IDs');
    for(const id of review.targetTaskIds){const task=taskMap.get(id);if(!task)throw Error('Target is not present in frozen task corpus');
     const sameGrid=task.grid_task_id===original.gridTaskId,passes=(!original.state||task.status===original.state)&&(!original.businessStatus||(task.business_status??'normal')===original.businessStatus);
     if(review.status==='in_scope'&&(!sameGrid||!passes))throw Error('In-scope target fails capture-time grid/status filter');
     if(review.status==='outside_grid'&&sameGrid)throw Error('Outside-grid target is in the requested grid');
     if(review.status==='filtered_out'&&(!sameGrid||passes))throw Error('Filtered target must be in-grid and excluded by a filter');
    }
   }
  }
  validated.push(row);
 }
 if(seen.size!==expected.size)throw Error('Every request must remain represented, including unknown labels');
 return validated;
}
export function searchReviewMetrics(requests,reviews){
 const requestMap=new Map(requests.map(r=>[r.id,r])),statusCounts={unknown:0,in_scope:0,outside_grid:0,filtered_out:0,verified_absent:0};let eligible=0,ranked=0,recalled=0,first=0,reciprocal=0;
 for(const row of reviews){statusCounts[row.review.status]++;const request=requestMap.get(row.requestId);if(row.review.status!=='in_scope'||(request.page??1)!==1)continue;eligible++;
  // Missing historical ranks stay unknown, never zero. An explicitly recorded [] is a known miss.
  const ids=request.result_task_ids,recordedZero=request.matched_count===0,hasRequestSource=typeof request.request_id==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(request.request_id);
  if(!Array.isArray(ids)||ids.some(id=>!positive(id))||new Set(ids).size!==ids.length||(!recordedZero&&!hasRequestSource)||(request.matched_count>0&&ids.length===0))continue;ranked++;
  const index=request.result_task_ids.findIndex(id=>row.review.targetTaskIds.includes(id));if(index>=0&&index<10){recalled++;reciprocal+=1/(index+1);if(index===0)first++;}
 }
 return {requests:reviews.length,statusCounts,inScopeFirstPageRequests:eligible,rankedFirstPageRequests:ranked,missingHistoricalRankRequests:eligible-ranked,recallAt10:ranked?recalled/ranked:null,mrrAt10:ranked?reciprocal/ranked:null,firstRankRate:ranked?first/ranked:null,eligibility:'reviewed in-scope first-page requests with recorded ordered task IDs only',historicalScope:'capture-time task/filter check; reviewer evidence must justify historical equivalence',independentIntentMetrics:'not inferred from page sessions or consecutive prefixes'};
}
export async function verifiedSnapshotFile(directory,manifest,name,onRow){
 const entry=manifest.files?.find(f=>f.file===name);if(!entry||!Number.isSafeInteger(entry.rows)||entry.rows<0)throw Error('Missing frozen dataset entry');
 const canonicalDirectory=await realpath(directory),file=await realpath(join(canonicalDirectory,name));if(relative(canonicalDirectory,file).startsWith('..'))throw Error('Snapshot link escapes dataset');
 const digest=createHash('sha256');let rows=0;for await(const line of createInterface({input:createReadStream(file),crlfDelay:Infinity})){digest.update(line+'\n');if(!line)throw Error('Unexpected blank JSONL record');const value=JSON.parse(line);onRow(value);rows++;}
 if(rows!==entry.rows||digest.digest('hex')!==entry.sha256)throw Error('Frozen snapshot count/hash mismatch: '+name);
}
export async function runSearchReview(args=process.argv.slice(2)){
 const [mode,datasetPath,annotationPath]=args;if(!['prepare','validate'].includes(mode)||!datasetPath||mode==='validate'&&!annotationPath||args.length>(mode==='prepare'?2:3))throw Error('Usage: search-review.mjs prepare DATASET | validate DATASET ANNOTATIONS_JSON');
 const root=await realpath('data/refactor/search-evaluation'),directory=await realpath(resolve(datasetPath));if(directory===root||relative(root,directory).startsWith('..'))throw Error('Only a private local evaluation dataset is accepted');
 const manifest=JSON.parse(await readFile(join(directory,'manifest.json'),'utf8'));if(!manifest.complete||manifest.database!=='store_settle'||manifest.readOnly!==true||typeof manifest.version!=='string')throw Error('Complete read-only local snapshot required');
 const requests=[];await verifiedSnapshotFile(directory,manifest,'local-search-requests.jsonl',row=>requests.push(row));const templates=buildReviewTemplate(requests,manifest.version);
 const output=join(directory,'reviews',new Date().toISOString().replace(/[:.]/g,'-'));await mkdir(output,{recursive:true,mode:0o700});
 if(mode==='prepare'){
  await writeFile(join(output,'annotations.json'),JSON.stringify(templates,null,2)+'\n',{mode:0o600,flag:'wx'});
  const report={mode,dataset:manifest.version,requests:requests.length,reviewed:0,status:requests.length?'awaiting-human-review':'insufficient-real-request-samples',output,productionAccessed:false,databaseWritten:false};await writeFile(join(output,'report.json'),JSON.stringify(report,null,2),{mode:0o600,flag:'wx'});console.log(JSON.stringify(report));return report;
 }
 const file=await realpath(resolve(annotationPath));if(relative(root,file).startsWith('..'))throw Error('Annotations must stay inside the private evaluation root');
 const annotations=JSON.parse(await readFile(file,'utf8')),tasks=[];await verifiedSnapshotFile(directory,manifest,'task-snapshots.jsonl',row=>tasks.push(row));
 const reviews=validateSearchReviews(templates,annotations,tasks),metrics=searchReviewMetrics(requests,reviews),report={mode,dataset:manifest.version,annotationSha256:hash(await readFile(file)),output,...metrics,status:metrics.rankedFirstPageRequests?'reviewed-subset-only':'insufficient-reviewed-rank-evidence',productionAccessed:false,databaseWritten:false};
 await writeFile(join(output,'report.json'),JSON.stringify(report,null,2)+'\n',{mode:0o600,flag:'wx'});console.log(JSON.stringify(report));return report;
}
if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href)runSearchReview().catch(()=>{console.error('[search-review] Refused: verify private snapshot integrity, request identities and manual evidence; no database or network action performed.');process.exitCode=1;});
