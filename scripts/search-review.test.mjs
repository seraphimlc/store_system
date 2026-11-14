import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,writeFile,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';import {createHash} from 'node:crypto';
import {buildReviewTemplate,validateSearchReviews,searchReviewMetrics,verifiedSnapshotFile} from './search-review.mjs';
const requests=[{id:1,grid_task_id:10,query_text:'real-request',page:1,task_state:'pending',business_status:'normal',matched_count:2,request_id:'12345678-1234-4123-8123-123456789012',result_task_ids:[101,102]},{id:2,grid_task_id:10,query_text:'unknown-rank',page:1},{id:3,grid_task_id:10,query_text:'no-hit',page:1,matched_count:0,result_task_ids:[]}];
const tasks=[{id:101,grid_task_id:10,status:'pending',business_status:'normal'},{id:102,grid_task_id:10,status:'completed',business_status:'normal'},{id:201,grid_task_id:20,status:'pending'}];
const reviewed=(row,status,ids=[])=>({...row,review:{status,targetTaskIds:ids,reviewer:'reviewer',evidence:'explicitly checked source and user target',notes:''}});
test('templates preserve all request provenance and never infer targets',()=>{
 const t=buildReviewTemplate(requests,'v1');assert.equal(t.length,3);assert.equal(t[0].review.status,'unknown');assert.deepEqual(t[0].review.targetTaskIds,[]);assert.equal(t[0].query,requests[0].query_text);assert.equal(t[0].queryHash.length,64);assert.throws(()=>buildReviewTemplate([requests[0],requests[0]],'v1'));
 assert.deepEqual(searchReviewMetrics([],[]).recallAt10,null);
});
test('manual reviews require intact provenance, evidence and capture-time grid/filter eligibility',()=>{
 const t=buildReviewTemplate(requests,'v1'),valid=[reviewed(t[0],'in_scope',[101]),t[1],reviewed(t[2],'verified_absent')];assert.equal(validateSearchReviews(t,valid,tasks).length,3);
 for(const bad of [[...valid,valid[0]],valid.slice(1),[{...valid[0],query:'changed'},...valid.slice(1)],[reviewed(t[0],'in_scope',[201]),...valid.slice(1)],[reviewed(t[0],'in_scope',[102]),...valid.slice(1)],[reviewed(t[0],'outside_grid',[101]),...valid.slice(1)],[reviewed(t[0],'filtered_out',[101]),...valid.slice(1)],[reviewed(t[0],'in_scope',[999]),...valid.slice(1)],[{...valid[0],review:{...valid[0].review,evidence:''}},...valid.slice(1)],[reviewed(t[0],'unknown',[101]),...valid.slice(1)]])assert.throws(()=>validateSearchReviews(t,bad,tasks));
 assert.equal(validateSearchReviews(t,[reviewed(t[0],'outside_grid',[201]),...valid.slice(1)],tasks).length,3);assert.equal(validateSearchReviews(t,[reviewed(t[0],'filtered_out',[102]),...valid.slice(1)],tasks).length,3);
});
test('rank metrics exclude unknown target/scope and missing ranking instead of fabricating misses',()=>{
 const t=buildReviewTemplate(requests,'v1'),reviews=[reviewed(t[0],'in_scope',[101]),reviewed(t[1],'in_scope',[101]),reviewed(t[2],'in_scope',[101])];validateSearchReviews(t,reviews,tasks);const r=searchReviewMetrics(requests,reviews);assert.equal(r.inScopeFirstPageRequests,3);assert.equal(r.rankedFirstPageRequests,2);assert.equal(r.missingHistoricalRankRequests,1);assert.equal(r.recallAt10,.5);assert.equal(r.mrrAt10,.5);assert.equal(r.firstRankRate,.5);
 const outside=[reviewed(t[0],'outside_grid',[201]),t[1],reviewed(t[2],'verified_absent')];assert.equal(searchReviewMetrics(requests,outside).recallAt10,null);
 const historical=[{...requests[0],request_id:null,result_task_ids:[]}];assert.equal(searchReviewMetrics(historical,[reviews[0]]).rankedFirstPageRequests,0);assert.equal(searchReviewMetrics(historical,[reviews[0]]).recallAt10,null);
});
test('snapshot import rejects checksum or row-count tampering and accepts an explicitly empty request file',async()=>{
 const root=await mkdtemp(join(tmpdir(),'search-review-'));try{const text=JSON.stringify(requests[0])+'\n';await writeFile(join(root,'requests.jsonl'),text);const entry={file:'requests.jsonl',rows:1,sha256:createHash('sha256').update(text).digest('hex')},rows=[];await verifiedSnapshotFile(root,{files:[entry]},entry.file,row=>rows.push(row));assert.equal(rows.length,1);await assert.rejects(()=>verifiedSnapshotFile(root,{files:[{...entry,rows:2}]},entry.file,()=>{}));await writeFile(join(root,entry.file),text.replace('real-request','tampered'));await assert.rejects(()=>verifiedSnapshotFile(root,{files:[entry]},entry.file,()=>{}));
 await writeFile(join(root,'empty.jsonl'),'');await verifiedSnapshotFile(root,{files:[{file:'empty.jsonl',rows:0,sha256:createHash('sha256').update('').digest('hex')}]},'empty.jsonl',()=>assert.fail());
 }finally{await rm(root,{recursive:true,force:true});}
});
