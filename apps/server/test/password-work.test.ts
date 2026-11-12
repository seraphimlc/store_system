import test from 'node:test';
import assert from 'node:assert/strict';
import { PasswordWork } from '../src/password-work';
test('Argon2 scheduling caps active memory-heavy work and bounds waiting requests', async () => {
  const gate = new PasswordWork(2,1);
  const releases: Array<() => void> = [];
  let active=0, peak=0;
  const work=()=>gate.run(async()=>{active++;peak=Math.max(peak,active);await new Promise<void>(resolve=>releases.push(resolve));active--;return true;});
  const first=work(), second=work(), third=work();
  await assert.rejects(work(), (error: unknown) => (error as {code:string}).code==='AUTH_BUSY');
  releases.shift()!(); await first;
  await new Promise(resolve=>setImmediate(resolve));
  releases.shift()!(); releases.shift()!();
  await Promise.all([second,third]);
  assert.equal(peak,2);
  assert.equal(await gate.run(async()=>1),1);
});
