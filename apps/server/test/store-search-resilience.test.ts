import 'reflect-metadata';import test from 'node:test';import assert from 'node:assert/strict';import {performance} from 'node:perf_hooks';import {hash} from '@node-rs/argon2';
import {writeStoreSearchTelemetry,suggestionFailure,searchFailureKind} from '../src/store-search-resilience';
import {createApp} from '../src/main';import {Database} from '../src/database';import {BatchWorkService} from '../src/batch-work';import {seedBatchWork,clearBatchWork} from '../../../scripts/batch-work-fixture.mjs';
const url=process.env.NEXT_TEST_DATABASE_URL;
test('locked telemetry fails quickly without blocking returned results and recovers after unlocking',{skip:!url},async()=>{
 assert.equal(process.env.NEXT_TEST_ISOLATED,'1');const app=await createApp({databaseUrl:url!,sessionSecret:'search-resilience-tests-secret-more-than-forty-characters',cookieSecure:false,port:0,host:'127.0.0.1'}),db=app.get(Database),service=app.get(BatchWorkService);let f:any,locker:any,release:Promise<unknown>|undefined;
 try{f=await seedBatchWork(db,'RESILIENCE-'+Date.now(),await hash('fixture-pass'));const grid=f.grids[0][0],id=f.stores[0][0][0];await db.query("UPDATE bd_store_task SET snapshot=jsonb_build_object('name','ABCDEF','address','mock') WHERE id=$1",[id]);
 locker=await db.pool.connect();await locker.query('BEGIN');await locker.query('LOCK TABLE bd_store_search_log IN ACCESS EXCLUSIVE MODE');
 release=new Promise(resolve=>setTimeout(()=>void locker.query('ROLLBACK').then(resolve),600));const start=performance.now();const result=await service.visits(f.users[2],grid,{q:'ABCDEF'}),duration=performance.now()-start;await release;
 assert.equal(result.total,1);assert.equal(result.items[0].id,id);assert.equal(result.searchLogId,null);assert.equal(result.searchRequestId,null);assert.equal(result.items[0].searchLogId,undefined);assert.ok(duration<500,'optional telemetry must not wait for the 600ms table lock');assert.equal((await db.query('SELECT count(*)::int AS n FROM bd_store_search_log')).rows[0].n,0);const recovered=await service.visits(f.users[2],grid,{q:'ABCDEF'});assert.ok(recovered.searchLogId);assert.equal(recovered.total,1);console.log(JSON.stringify({telemetryLockObservation:{durationMs:duration,logRecorded:result.searchLogId!==null,lockHeldMs:600,recovered:!!recovered.searchLogId}}));
 const statementStart=performance.now();await assert.rejects(()=>writeStoreSearchTelemetry(db,'SELECT pg_sleep(1)',[]),(error:any)=>error.code==='57014');assert.ok(performance.now()-statementStart<800);assert.equal((await db.query('SELECT 1 AS ok')).rows[0].ok,1);
 }finally{if(release)await release;if(locker){await locker.query('ROLLBACK');locker.release();}if(f)await clearBatchWork(db,f);await app.close();}
});

test('suggestion database pressure maps to a retryable error without exposing database details',()=>{
 for(const code of ['55P03','57014']){const result=suggestionFailure({code,message:'private SQL and host'});assert.equal(result.code,'SEARCH_SUGGESTIONS_BUSY');assert.equal(result.getStatus(),503);assert.equal(result.message.includes('private'),false);}
 const denied={code:'FORBIDDEN'};assert.equal(suggestionFailure(denied),denied);assert.equal(searchFailureKind({code:'55P03'}),'lock_timeout');assert.equal(searchFailureKind({code:'57014'}),'statement_timeout');assert.equal(searchFailureKind(Error('secret')),'storage_error');
});

test('suggestion reads time out with a retryable response and recover after a real isolated table lock',{skip:!url},async()=>{
 assert.equal(process.env.NEXT_TEST_ISOLATED,'1');const app=await createApp({databaseUrl:url!,sessionSecret:'suggestion-resilience-secret-more-than-forty-characters',cookieSecure:false,port:0,host:'127.0.0.1'}),db=app.get(Database),service=app.get(BatchWorkService);let f:any,locker:any,release:Promise<unknown>|undefined;
 try{f=await seedBatchWork(db,'SUGGEST-RECOVERY-'+Date.now(),await hash('fixture-pass'));const grid=f.grids[0][0],id=f.stores[0][0][0];await db.query("UPDATE bd_store_task SET snapshot=jsonb_build_object('name','ABCDEF','address','mock') WHERE id=$1",[id]);
  locker=await db.pool.connect();await locker.query('BEGIN');await locker.query('LOCK TABLE bd_store_task IN ACCESS EXCLUSIVE MODE');release=new Promise(resolve=>setTimeout(()=>void locker.query('ROLLBACK').then(resolve),1800));const started=performance.now();
  await assert.rejects(()=>service.suggestions(f.users[2],grid,{q:'ABCDEH'}),(error:any)=>error.code==='SEARCH_SUGGESTIONS_BUSY'&&error.getStatus()===503);const duration=performance.now()-started;assert.ok(duration<1750,'suggestion must not wait for the full 1800ms business-table lock');await release;
  const recovered=await service.suggestions(f.users[2],grid,{q:'ABCDEH'});assert.equal(recovered.status,'ready');assert.equal(recovered.total,1);const normal=await service.visits(f.users[2],grid,{q:'ABCDEF'});assert.equal(normal.total,1);
  console.log(JSON.stringify({suggestionLockObservation:{durationMs:duration,lockHeldMs:1800,recovered:true,ordinarySearchRecovered:true}}));
 }finally{if(release)await release;if(locker){await locker.query('ROLLBACK');locker.release();}if(f)await clearBatchWork(db,f);await app.close();}
});
