import {useCallback,useRef} from 'react';
import type {Session} from '../../../../packages/contracts/src';
import type {StoreSearchFeedback,StoreSearchFeedbackReceipt} from '../../../../packages/contracts/src/store-search';
import {api} from '../api';
const key=(input:StoreSearchFeedback)=>JSON.stringify([input.kind,input.searchLogId,input.kind==='suggestion_selected'||input.kind==='completion_selected'?input.suggestedName:input.storeTaskId,input.kind==='suggestion_selected'?input.suggestionPage:null]);
/** Ephemeral per-grid event IDs make retries idempotent; no credentials/queries in localStorage. */
export function useStoreSearchFeedback(session:Session){
 const ids=useRef(new Map<string,string>()),received=useRef(new Map<string,StoreSearchFeedbackReceipt>()),pending=useRef(new Map<string,Promise<StoreSearchFeedbackReceipt>>());
 const report=useCallback((input:StoreSearchFeedback)=>{
  const k=key(input),done=received.current.get(k);if(done)return Promise.resolve(done);const inFlight=pending.current.get(k);if(inFlight)return inFlight;
  const eventId=ids.current.get(k)??crypto.randomUUID();ids.current.set(k,eventId);
  const request=api<StoreSearchFeedbackReceipt>('/batch-work/search-feedback',session,{...input,eventId}).then(receipt=>{if(receipt.accepted!==true||receipt.eventId!==eventId)throw Error('反馈响应异常，请重试');received.current.set(k,receipt);return receipt;}).finally(()=>pending.current.delete(k));
  pending.current.set(k,request);return request;
 },[session]);
 const hasRecorded=useCallback((input:StoreSearchFeedback)=>received.current.has(key(input)),[]);
 return {report,hasRecorded};
}
