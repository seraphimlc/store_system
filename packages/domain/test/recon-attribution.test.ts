import test from 'node:test';import assert from 'node:assert/strict';import {reconAttribution} from '../src/recon-attribution';
test('record attribution uses timestamp only when present, counts sets rather than visit points, and retains first source reason',()=>{
 const report=[{store:'A',modified:'2026-11-01 10:00:00'},{store:'B',modified:'2026-11-01 11:00:00'},{store:'B',modified:'2026-11-01 11:00:00'},{store:'C',modified:'2026-11-01'}];
 const formal=[{store:'A',modified:'2026-11-01 10:00:00'},{store:'A',modified:'2026-11-01 12:00:00'}];
 const result=reconAttribution(report,formal,[{store:'B',modified:'2026-11-01 11:00:00',status:'master_late'},{store:'B',modified:'2026-11-01 11:00:00',status:'valid'}]);assert.equal(result.consistent,1);assert.equal(result.only_system,1);assert.equal(result.only_report,2);assert.equal(result.only_report_reason.master_late,1);assert.equal(result.only_report_reason.not_found,1);assert.equal(result.net,-1);
 const dates=reconAttribution([{store:'A',modified:'2026-11-01'}],formal,[]);assert.equal(dates.consistent,1);assert.equal(dates.only_system,0);assert.equal(dates.only_report,0);
});
