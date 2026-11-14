import 'reflect-metadata';import test from 'node:test';import assert from 'node:assert/strict';import {hash} from '@node-rs/argon2';import {randomUUID} from 'node:crypto';import request from '../node_modules/supertest';
import {createApp} from '../src/main';import {Database} from '../src/database';import {BatchWorkService} from '../src/batch-work';import {spawnSync} from 'node:child_process';
import {seedBatchWork,clearBatchWork} from '../../../scripts/batch-work-fixture.mjs';
import './store-search-log.test';
const url=process.env.NEXT_TEST_DATABASE_URL;
test('search feedback binds the actor to returned targets, protects CSRF/live scope and distinguishes explicit success from opening',{skip:!url},async()=>{
 const app=await createApp({databaseUrl:url!,sessionSecret:'feedback-tests-secret-at-least-forty-characters',cookieSecure:false,port:0,host:'127.0.0.1'}),db=app.get(Database),service=app.get(BatchWorkService);let f:any;
 try{f=await seedBatchWork(db,'FEEDBACK-'+Date.now(),await hash('fixture-feedback-pass'));const actor=f.users[2],grid=f.grids[0][1],task=f.stores[0][1][0];await db.query("UPDATE bd_store_task SET snapshot=jsonb_build_object('name','ABCDEF','address','mock') WHERE id=$1",[task]);
 const sessionId=randomUUID(),hit=await service.visits(actor,grid,{q:'ABCDEF',querySessionId:sessionId});assert.ok(hit.searchLogId);assert.equal(hit.items[0].searchLogId,hit.searchLogId);assert.match(hit.searchRequestId!,/^[0-9a-f-]{36}$/);
 const log=(await db.query('SELECT * FROM bd_store_search_log WHERE id=$1',[hit.searchLogId])).rows[0];assert.equal(log.query_session_id,sessionId);assert.deepEqual(log.result_task_ids,[task]);assert.ok(log.context_duration_ms>=0&&log.query_duration_ms>=0);
 const opened={eventId:randomUUID(),searchLogId:hit.searchLogId,kind:'opened',storeTaskId:task};assert.equal((await service.searchFeedback(actor,opened)).duplicate,false);assert.equal((await service.searchFeedback(actor,opened)).duplicate,true);
 assert.equal((await db.query("SELECT count(*)::int AS n FROM bd_store_search_feedback WHERE kind='confirmed'")).rows[0].n,0,'opening never becomes confirmed');
 const client=request.agent(app.getHttpServer()),login=await client.post('/api/auth/login').send({username:f.codes[2],password:'fixture-feedback-pass'});assert.equal(login.status,201);
 const confirmed={...opened,eventId:randomUUID(),kind:'confirmed'};assert.equal((await client.post('/api/batch-work/search-feedback').send(confirmed)).status,403);
 assert.equal((await client.post('/api/batch-work/search-feedback').set('X-CSRF-Token',login.body.csrfToken).send(confirmed)).status,201);
 await assert.rejects(()=>service.searchFeedback(actor,{...opened,kind:'confirmed'}),{code:'FEEDBACK_CONFLICT'});
 await assert.rejects(()=>service.searchFeedback(actor,{...confirmed,eventId:randomUUID(),storeTaskId:f.stores[0][1][1]}),{code:'FEEDBACK_INVALID'});
 await assert.rejects(()=>service.searchFeedback(f.users[0],{...confirmed,eventId:randomUUID()}),{code:'NOT_FOUND'},'even an admin cannot label another actors query');
 const miss=await service.visits(actor,grid,{q:'ABCDEH',querySessionId:sessionId});assert.equal(miss.total,0);const suggestion=await service.suggestions(actor,grid,{q:'ABCDEH'});const selected={suggestionReceipt:suggestion.receipt,kind:'suggestion_selected',searchLogId:miss.searchLogId,suggestedName:'ABCDEF',suggestionPage:1,eventId:randomUUID()};assert.equal((await service.searchFeedback(actor,selected)).accepted,true);await assert.rejects(()=>service.searchFeedback(actor,{...selected,eventId:randomUUID(),suggestionReceipt:selected.suggestionReceipt+'.tampered'}),{code:'FEEDBACK_INVALID'});await assert.rejects(()=>service.searchFeedback(actor,{...selected,eventId:randomUUID(),suggestionPage:2}),{code:'FEEDBACK_INVALID'});
 await assert.rejects(()=>service.searchFeedback(actor,{...selected,eventId:randomUUID(),suggestedName:'not-really-suggested'}),{code:'FEEDBACK_INVALID'});
 await assert.rejects(()=>service.searchFeedback(actor,{...selected,eventId:randomUUID(),searchLogId:hit.searchLogId}),{code:'FEEDBACK_INVALID'});
 const summary=spawnSync(process.execPath,['scripts/analyze-store-search.mjs'],{env:{...process.env,NEXT_DATABASE_URL:url!},encoding:'utf8'});assert.equal(summary.status,0,summary.stderr);const metrics=JSON.parse(summary.stdout);assert.equal(metrics.feedback.opened_requests,1);assert.equal(metrics.feedback.confirmed_requests,1);assert.equal(metrics.feedback.suggestion_selected_requests,1);assert.equal(Number(metrics.feedback.confirmed_request_rate_percent),50);assert.equal(metrics.grid_page_sessions,1);
 const extra=(await db.query(`INSERT INTO bd_store_task(grid_task_id,store_id,snapshot,status,result,revision,updated_at) SELECT $1,$2||n::text,jsonb_build_object('name','page-feedback '||n::text,'address','mock'),'pending','{}',0,now() FROM generate_series(1,21) AS n RETURNING id`,[grid,f.prefix+'-page-'])).rows;f.stores[0][1].push(...extra.map((r:any)=>r.id));
 const paged=await service.visits(actor,grid,{q:'page-feedback',page:2,querySessionId:sessionId});assert.equal(paged.items.length,1);assert.ok(paged.searchLogId);assert.notEqual(paged.searchLogId,hit.searchLogId);
 const pageLog=(await db.query('SELECT page,result_task_ids FROM bd_store_search_log WHERE id=$1',[paged.searchLogId])).rows[0];assert.equal(pageLog.page,2);assert.deepEqual(pageLog.result_task_ids,[paged.items[0].id]);
 await db.query('UPDATE bd_grid_task SET assignee_id=$1 WHERE id=$2',[f.users[3],grid]);await assert.rejects(()=>service.searchFeedback(actor,{...confirmed,eventId:randomUUID()}),{code:'FORBIDDEN'});
 assert.equal((await db.query('SELECT count(*)::int AS n FROM bd_task_event')).rows[0].n,0);
 // Cascading telemetry retention does not touch tasks or user data.
 const events=(await db.query('SELECT count(*)::int AS n FROM bd_store_search_feedback')).rows[0].n;assert.equal(events,3);await db.query('DELETE FROM bd_store_search_log WHERE id=$1',[hit.searchLogId]);assert.equal((await db.query('SELECT count(*)::int AS n FROM bd_store_search_feedback')).rows[0].n,1);
 }finally{if(f)await clearBatchWork(db,f);await app.close();}
});
