import test from 'node:test';import assert from 'node:assert/strict';import {aggregateReconDays,reconcileDays,integerPoints} from '../src/reconciliation';
test('reconciliation distinguishes missing sides and keeps system-minus-report points independent of visit counts',()=>{
 const report=aggregateReconDays([{code:'A',name:'A',date:'2026-11-01',points:1,count:1},{code:'A',name:'A',date:'2026-11-01',points:2,count:1},{code:'B',name:'B',date:'2026-11-02',points:0,count:1}]);assert.equal(report[0].points,3);assert.equal(report[0].count,2);
 const diff=reconcileDays(report,[{code:'A',date:'2026-11-01',points:3,count:3},{code:'C',date:'2026-11-03',points:2,count:1}]);assert.equal(diff.length,2);assert.equal(diff[0].side,'report_only');assert.equal(diff[0].diff,0);assert.equal(diff[1].side,'local_only');assert.equal(diff[1].diff,2);
 assert.equal(integerPoints('2.9'),2);assert.equal(integerPoints('-1.9'),-1);assert.equal(integerPoints('bad'),null);assert.equal(integerPoints(''),null);assert.equal(integerPoints('Infinity'),null);
});
