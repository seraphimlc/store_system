/** Uses real temporary files and mocked service actions, never deployment CLI or Docker. */
import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,readFile,writeFile,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';
import {replaceGateway,handoffRelease} from './release-transition.mjs';
const previous='server { location / { proxy_pass http://previous; } }\n';const candidate='server { location / { proxy_pass http://candidate; } }\n';
test('real temporary gateway bytes restore after candidate health failure while previous release metadata remains unchanged',async()=>{
 const root=await mkdtemp(join(tmpdir(),'search-rollback-')),file=join(root,'gateway.conf'),state=join(root,'state.json');const events=[];
 try{await writeFile(file,previous,{mode:0o600});await writeFile(state,JSON.stringify({current:'previous',previous:'older'}),{mode:0o600});const stateBefore=await readFile(state);
  await assert.rejects(()=>replaceGateway({read:()=>readFile(file,'utf8'),write:bytes=>writeFile(file,bytes),configuration:candidate,validate:async()=>{const bytes=await readFile(file,'utf8');assert.ok([previous,candidate].includes(bytes));events.push('validate');},reload:async()=>events.push('reload'),health:async()=>{events.push('health');throw Error('candidate not ready');},delay:async()=>{},attempts:2}),/candidate not ready/);
  assert.equal(await readFile(file,'utf8'),previous);assert.deepEqual(await readFile(state),stateBefore);assert.equal(events.filter(x=>x==='health').length,2);assert.equal(events.at(-1),'reload');
 }finally{await rm(root,{recursive:true,force:true});}
});
test('candidate worker failure restores the real gateway file before resuming the previous worker',async()=>{
 const root=await mkdtemp(join(tmpdir(),'search-handoff-')),file=join(root,'gateway.conf'),events=[];let oldWorker=true,newWorker=false;
 try{await writeFile(file,previous);await assert.rejects(()=>handoffRelease({stopOldWorker:async()=>{oldWorker=false;events.push('old stopped');},verifyTasks:async()=>{assert.equal(oldWorker,false);events.push('tasks checked');},switchGateway:async()=>{await writeFile(file,candidate);events.push('candidate routed');},startWorker:async()=>{assert.equal(oldWorker,false);newWorker=true;throw Error('worker failed');},stopWorker:async()=>{newWorker=false;events.push('candidate stopped');},restoreGateway:async()=>{await writeFile(file,previous);events.push('old routed');},startOldWorker:async()=>{assert.equal(newWorker,false);assert.equal(await readFile(file,'utf8'),previous);oldWorker=true;events.push('old started');}}),/worker failed/);
  assert.equal(oldWorker,true);assert.equal(newWorker,false);assert.deepEqual(events,['old stopped','tasks checked','candidate routed','candidate stopped','old routed','old started']);assert.equal(await readFile(file,'utf8'),previous);
 }finally{await rm(root,{recursive:true,force:true});}
});
