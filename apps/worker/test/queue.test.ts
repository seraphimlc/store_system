import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DurableQueue } from '../src/queue';
test('durable tasks survive restart, claim once and recover only idempotent supported kinds',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'store-queue-'));
 try{
  const queue=new DurableQueue(directory);
  await queue.enqueue('system.check',{value:1},'same-id');
  await assert.rejects(()=>queue.enqueue('system.check',{},'same-id'));
  const claims=await Promise.all([queue.claim(),queue.claim()]);
  assert.equal(claims.filter(Boolean).length,1);
  const restarted=new DurableQueue(directory);
  await restarted.recover(new Set(['system.check']));
  const task=await restarted.claim();assert.ok(task);assert.equal(task.attempts,2);
  await restarted.finish(task,{ok:true});
  assert.equal(JSON.parse(await readFile(join(directory,'done','same-id.json'),'utf8')).result.ok,true);
  assert.equal(await restarted.claim(),null);
  await assert.rejects(()=>queue.enqueue('system.check',{},'../escape'));
  await assert.rejects(()=>queue.enqueue('system.check','x'.repeat(70000),'large'));
 }finally{await rm(directory,{recursive:true,force:true});}
});
