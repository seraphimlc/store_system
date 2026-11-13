import type {GridReadOnlyReason} from '../../../packages/contracts/src';
/** Explain the same current-owner rule enforced by context(..., execute=true). */
export function gridExecution(input:{batchStatus:string;actorRole:string;actorId:number;assigneeId:number|null;leaderId:number|null;membershipRole:string|null;captain:boolean;owner:boolean}):{canExecute:boolean;readOnlyReason:GridReadOnlyReason|null}{
 let reason:GridReadOnlyReason|null=null;
 if(input.batchStatus==='disabled')reason='batch_disabled';
 else if(input.batchStatus==='completed')reason='batch_completed';
 else if(!['staff','leader'].includes(input.actorRole))reason='account_role';
 else if(input.assigneeId===null)reason='unassigned';
 else if(input.assigneeId!==input.actorId)reason='not_assignee';
 else if(!input.membershipRole)reason='team_membership';
 else if(input.leaderId===input.actorId&&!input.captain)reason='leader_identity';
 else if(!input.owner)reason='team_membership';
 return {canExecute:reason===null,readOnlyReason:reason};
}
