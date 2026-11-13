import test from 'node:test';
import assert from 'node:assert/strict';
import {gridExecution} from '../src/grid-execution';
const own={batchStatus:'active',actorRole:'staff',actorId:2,assigneeId:2,leaderId:1,membershipRole:'member',captain:false,owner:true};
test('grid read-only reasons follow execution permission; valid assigned captains may execute',()=>{
 assert.deepEqual(gridExecution(own),{canExecute:true,readOnlyReason:null});
 assert.deepEqual(gridExecution({...own,actorRole:'leader',actorId:1,assigneeId:1,leaderId:1,membershipRole:'leader',captain:true}),{canExecute:true,readOnlyReason:null});
 for(const [change,expected] of [
  [{batchStatus:'disabled'},'batch_disabled'],[{batchStatus:'completed'},'batch_completed'],[{actorRole:'admin',owner:false},'account_role'],[{assigneeId:null,owner:false},'unassigned'],[{assigneeId:3,owner:false},'not_assignee'],[{membershipRole:null,owner:false},'team_membership'],[{leaderId:2,owner:false},'leader_identity'],[{membershipRole:'other',owner:false},'team_membership'],
 ] as const){assert.deepEqual(gridExecution({...own,...change}),{canExecute:false,readOnlyReason:expected});}
 assert.equal(gridExecution({...own,batchStatus:'completed',assigneeId:3,owner:false}).readOnlyReason,'batch_completed');
});
