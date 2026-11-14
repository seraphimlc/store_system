import 'reflect-metadata';import test from 'node:test';import assert from 'node:assert/strict';import {hash} from '@node-rs/argon2';import {randomUUID} from 'node:crypto';import {readFile} from 'node:fs/promises';import {spawnSync} from 'node:child_process';
import {createApp} from '../src/main';import {Database} from '../src/database';import {BatchWorkService} from '../src/batch-work';import {seedBatchWork,clearBatchWork} from '../../../scripts/batch-work-fixture.mjs';
const url=process.env.NEXT_TEST_DATABASE_URL;
test('completion selection needs the displayed prefix receipt and a matching owned query; it is not confirmed success',{skip:!url},async()=>{
 assert.equal(process.env.NEXT_TEST_ISOLATED,'1');const app=await createApp({databaseUrl:url!,sessionSecret:'completion-feedback-secret-at-least-forty-characters',cookieSecure:false,host:'127.0.0.1',port:0}),db=app.get(Database),service=app.get(BatchWorkService);let f:any;
 try{f=await seedBatchWork(db,'CMP-FB-'+Date.now(),await hash('fixture-pass'));const actor=f.users[2],grid=f.grids[0][1],task=f.stores[0][1][0];await db.query("UPDATE bd_store_task SET snapshot=jsonb_build_object('name','ABCDEF','address','mock') WHERE id=$1",[task]);
 const query=await service.visits(actor,grid,{q:'ABC'}),completion=await service.completions(actor,grid,{q:'ABC'});assert.ok(completion.receipt);assert.deepEqual(completion.items,[{name:'ABCDEF',candidateCount:1}]);
 const event={eventId:randomUUID(),kind:'completion_selected',searchLogId:query.searchLogId,suggestedName:'ABCDEF',completionReceipt:completion.receipt};assert.equal((await service.searchFeedback(actor,event)).duplicate,false);assert.equal((await service.searchFeedback(actor,event)).duplicate,true);
 const record=(await db.query('SELECT kind,strategy_version,store_task_id FROM bd_store_search_feedback WHERE event_id=$1',[event.eventId])).rows[0];assert.deepEqual(record,{kind:'completion_selected',strategy_version:'name-prefix-v1',store_task_id:null});
 await assert.rejects(()=>service.searchFeedback(actor,{...event,eventId:randomUUID(),completionReceipt:completion.receipt+'.tampered'}),{code:'FEEDBACK_INVALID'});
 const another=await service.visits(actor,grid,{q:'ABCD'});await assert.rejects(()=>service.searchFeedback(actor,{...event,eventId:randomUUID(),searchLogId:another.searchLogId}),{code:'FEEDBACK_INVALID'});
 await assert.rejects(()=>service.searchFeedback(actor,{...event,eventId:randomUUID(),suggestedName:'ABC other'}),{code:'FEEDBACK_INVALID'});
 const otherFilter=await service.completions(actor,grid,{q:'ABC',state:'pending'});await assert.rejects(()=>service.searchFeedback(actor,{...event,eventId:randomUUID(),completionReceipt:otherFilter.receipt}),{code:'FEEDBACK_INVALID'});
 const miss=await service.visits(actor,grid,{q:'ABCDEH'}),similar=await service.suggestions(actor,grid,{q:'ABCDEH'});
 await assert.rejects(()=>service.searchFeedback(actor,{...event,eventId:randomUUID(),searchLogId:miss.searchLogId,completionReceipt:similar.receipt}),{code:'FEEDBACK_INVALID'},'a similarity receipt cannot be used as a completion receipt');
 await assert.rejects(()=>service.searchFeedback(f.users[0],{...event,eventId:randomUUID()}),{code:'NOT_FOUND'});
 await db.query("UPDATE bd_store_task SET snapshot=jsonb_build_object('name','Different','address','mock') WHERE id=$1",[task]);await assert.rejects(()=>service.searchFeedback(actor,{...event,eventId:randomUUID()}),{code:'FEEDBACK_STALE'});
 const metrics=spawnSync(process.execPath,['scripts/analyze-store-search.mjs'],{env:{...process.env,NEXT_DATABASE_URL:url!},encoding:'utf8'});assert.equal(metrics.status,0,metrics.stderr);const summary=JSON.parse(metrics.stdout);assert.equal(summary.feedback.completion_selected_requests,1);assert.equal(summary.feedback.confirmed_requests,0);assert.equal(summary.feedback.suggestion_selected_requests,0);
 await db.query('UPDATE bd_grid_task SET assignee_id=$1 WHERE id=$2',[f.users[3],grid]);await assert.rejects(()=>service.searchFeedback(actor,{...event,eventId:randomUUID()}),{code:'FORBIDDEN'});
 assert.equal((await db.query('SELECT count(*)::int AS n FROM bd_task_event')).rows[0].n,0);
 // Reapplying the additive constraints preserves both historical event kinds and the new one.
 const migration=await readFile('deploy-next/migrations/005-store-completion-feedback.sql','utf8');await db.transaction(async c=>{await c.query(migration);await c.query(migration);});assert.equal((await db.query('SELECT count(*)::int AS n FROM bd_store_search_feedback')).rows[0].n,1);
 }finally{if(f)await clearBatchWork(db,f);await app.close();}
});
