import test from 'node:test';import assert from 'node:assert/strict';
import {retentionTarget,retentionOptions,retainSearchTelemetry} from './search-log-retention.mjs';
const now=Date.parse('2026-11-13T12:00:00.000Z');
test('retention requires an explicit old UTC cutoff, bounded batch and explicit apply confirmation',()=>{
 const options=retentionOptions(['--before=2026-10-01T00:00:00.000Z'],now);assert.equal(options.apply,false);assert.equal(options.limit,1000);
 for(const args of [[],['--before=2026-11-13T00:00:00.000Z'],['--before=2026-02-30T00:00:00.000Z'],['--before=2026-10-01'],['--before=2026-10-01T00:00:00.000Z','--apply'],['--before=2026-10-01T00:00:00.000Z','--max-delete=0'],['--before=2026-10-01T00:00:00.000Z','--max-delete=10001'],['--before=2026-10-01T00:00:00.000Z','--all']])assert.throws(()=>retentionOptions(args,now));
 assert.equal(retentionOptions(['--before=2026-10-01T00:00:00.000Z','--apply','--confirm-database=store_settle','--max-delete=2'],now).limit,2);
});
test('retention targets cannot be production, a legacy database, or a non-isolated random database',()=>{
 assert.equal(retentionTarget('postgresql://u:p@127.0.0.1/store_settle').database,'store_settle');
 const temp='postgresql://u:p@127.0.0.1/store_test_1234567890123_aabbccddeeff_refactor_p1';assert.throws(()=>retentionTarget(temp));assert.match(retentionTarget(temp,'1').database,/store_test_/);
 for(const url of ['postgresql://u:p@production/store_settle','postgresql://u:p@127.0.0.1/store_settle_refactor_p1','postgresql://u:p@127.0.0.1/postgres','mysql://u:p@127.0.0.1/store_settle'])assert.throws(()=>retentionTarget(url,'1'));
});
test('confirmation mismatch executes no SQL and any in-transaction failure rolls back',async()=>{
 const calls=[],client={query:async(sql)=>{calls.push(sql);if(sql.startsWith('SELECT current_database'))return {rows:[{database:'store_settle',readonly:'off',logs:'bd_store_search_log',feedback:'bd_store_search_feedback'}]};if(sql.startsWith('SELECT pg_try'))return {rows:[{locked:true}]};if(sql.startsWith('SELECT (SELECT count'))return {rows:[{eligible_logs:1}]};if(sql.startsWith('SELECT l.id'))return {rows:[{id:1}]};if(sql.startsWith('SELECT count'))return {rows:[{n:1}]};if(sql.startsWith('DELETE'))throw Error('injected deletion failure');return {rows:[]};}};
 const options={apply:true,confirmDatabase:'wrong',before:'2026-10-01T00:00:00.000Z',limit:1};await assert.rejects(()=>retainSearchTelemetry(client,options,'store_settle'));assert.equal(calls.length,0);
 await assert.rejects(()=>retainSearchTelemetry(client,{...options,confirmDatabase:'store_settle'},'store_settle'),/injected/);assert.equal(calls.at(-1),'ROLLBACK');assert.equal(calls.includes('COMMIT'),false);
});
