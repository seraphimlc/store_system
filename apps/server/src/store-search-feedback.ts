import {storeNameCompletions} from './store-search-completions';
import type {PoolClient} from 'pg';
import type {StoreSearchFeedback} from '../../../packages/contracts/src/store-search';
import {storeNameSuggestions} from './store-search-suggestions';import {BusinessError} from './errors';
export type StoreFeedbackInput=StoreSearchFeedback&{eventId:string};
/** Log ownership and current grid authorization must already be checked by the caller. */
export async function recordStoreSearchFeedback(client:PoolClient,actorId:number,log:any,input:StoreFeedbackInput){
 const taskId=input.kind==='suggestion_selected'||input.kind==='completion_selected'?null:input.storeTaskId,name=input.kind==='suggestion_selected'||input.kind==='completion_selected'?input.suggestedName:'';
 if(input.kind==='suggestion_selected'){
  if(log.matched_count!==0)throw new BusinessError('FEEDBACK_INVALID','该搜索不是无结果查询，不能关联相似名称',409);
  const page=await storeNameSuggestions(client,log.grid_task_id,{q:log.query_text,state:log.task_state,businessStatus:log.business_status,page:input.suggestionPage});
  if(page.status!=='ready'||!page.items.some(item=>item.name===name))throw new BusinessError('FEEDBACK_STALE','相似名称已变化，未记录此次反馈',409);
 }else if(input.kind==='completion_selected'){
  const page=await storeNameCompletions(client,log.grid_task_id,{q:log.query_text,state:log.task_state,businessStatus:log.business_status});
  if(page.status!=='ready'||!page.items.some(item=>item.name===name))throw new BusinessError('FEEDBACK_STALE','名称补全已变化，未记录此次反馈',409);
 }else{
  if(!Array.isArray(log.result_task_ids)||!log.result_task_ids.includes(taskId))throw new BusinessError('FEEDBACK_INVALID','该门店不在此次返回的搜索结果中',409);
  if(!(await client.query('SELECT 1 FROM bd_store_task WHERE id=$1 AND grid_task_id=ANY($2::int[])',[taskId,log.search_scope==='batch'?log.scope_grid_task_ids:[log.grid_task_id]])).rowCount)throw new BusinessError('FEEDBACK_STALE','门店任务已变化，未记录此次反馈',409);
 }
 const strategy=input.kind==='completion_selected'?'name-prefix-v1':input.kind==='suggestion_selected'?'name-edit-one-v1':log.strategy_version;
 const inserted=await client.query(`INSERT INTO bd_store_search_feedback(event_id,search_log_id,actor_id,kind,store_task_id,suggested_name,strategy_version) VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT(event_id) DO NOTHING RETURNING event_id`,[input.eventId,log.id,actorId,input.kind,taskId,name,strategy]);
 if(!inserted.rowCount){const existing=(await client.query('SELECT search_log_id,actor_id,kind,store_task_id,suggested_name FROM bd_store_search_feedback WHERE event_id=$1',[input.eventId])).rows[0];
  if(!existing||existing.search_log_id!==log.id||existing.actor_id!==actorId||existing.kind!==input.kind||existing.store_task_id!==taskId||existing.suggested_name!==name)throw new BusinessError('FEEDBACK_CONFLICT','反馈标识已用于其他操作',409);
 }
 return {accepted:true as const,eventId:input.eventId,duplicate:!inserted.rowCount};
}
