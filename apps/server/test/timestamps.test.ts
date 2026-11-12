import test from 'node:test';import assert from 'node:assert/strict';
import {utcTimestamp} from '../src/database';
test('legacy UTC-naive timestamps serialize independently of host timezone',()=>{
 assert.equal(utcTimestamp('2026-11-10 10:20:30.123456').toISOString(),'2026-11-10T10:20:30.123Z');
 assert.equal(new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Tokyo',hour:'2-digit',minute:'2-digit'}).format(utcTimestamp('2026-11-10 10:20:30')),'19:20');
});
