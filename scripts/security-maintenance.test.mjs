import test from 'node:test';import assert from 'node:assert/strict';import {createRequire} from 'node:module';import {mkdtemp,readFile,stat,rm,access} from 'node:fs/promises';import {join} from 'node:path';import {tmpdir} from 'node:os';
import {rotateWeakAccounts} from './security-maintenance.mjs';
const require=createRequire(new URL('../apps/server/package.json',import.meta.url)),{hash,verify}=require('@node-rs/argon2');
test('weak-account cleanup is read-only by default, delivers independent private credentials and rolls back failures',async()=>{
 const root=await mkdtemp(join(tmpdir(),'store-security-maintenance-'));try{
  const weak=await hash('demo123'),strong=await hash('individual-fixture-password'),rows=[{id:1,username:'fixture-a',password_hash:weak},{id:2,username:'fixture-b',password_hash:weak},{id:3,username:'fixture-c',password_hash:strong}];
  let mutations=0,rollback=false,fail=false;const updated=[];
  const client={query:async(sql,args)=>{if(sql.startsWith('SELECT'))return {rows};if(sql==='ROLLBACK')rollback=true;if(sql.startsWith('UPDATE users')){if(fail)throw Error('fixture failure');updated.push(args);}mutations++;return {rowCount:1,rows:[]};}};
  assert.deepEqual(await rotateWeakAccounts(client),{affected:2,applied:false});assert.equal(mutations,0);
  const file=join(root,'once.json');assert.equal((await rotateWeakAccounts(client,{apply:true,credentialFile:file})).affected,2);
  assert.equal((await stat(file)).mode&0o777,0o600);const items=JSON.parse(await readFile(file,'utf8'));assert.equal(items.length,2);assert.notEqual(items[0].temporaryPassword,items[1].temporaryPassword);assert.doesNotMatch(JSON.stringify(items),/password_hash|\$argon2|demo123/);
  assert.equal(await verify(updated[0][0],items[0].temporaryPassword),true);
  fail=true;const failed=join(root,'failed.json');await assert.rejects(rotateWeakAccounts(client,{apply:true,credentialFile:failed}));assert.equal(rollback,true);await assert.rejects(access(failed));
 }finally{await rm(root,{recursive:true,force:true});}
});
