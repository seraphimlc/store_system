import {createHash} from 'node:crypto';
import {normalizeStoreQuery,STORE_NORMALIZER_VERSION} from './store-search';
export const STORE_DOCUMENT_VERSION='store-task-document-v1';
export interface StoreSearchSource {id:number;grid_task_id:number;store_id:string;revision:number;snapshot:unknown}
/** Rebuildable task projection. It never mutates source snapshots or authorizes a result. */
export function storeSearchDocument(source:StoreSearchSource){
 for(const id of [source.id,source.grid_task_id])if(!Number.isSafeInteger(id)||id<=0)throw Error('Search document requires positive task/grid identities');
 if(!Number.isSafeInteger(source.revision)||source.revision<0)throw Error('Invalid task source revision');
 if(typeof source.store_id!=='string'||!source.store_id)throw Error('Missing merchant source identity');
 if(!source.snapshot||typeof source.snapshot!=='object'||Array.isArray(source.snapshot))throw Error('Task snapshot must be an object');
 const snapshot=source.snapshot as Record<string,unknown>;
 const text=(field:string)=>{const value=snapshot[field];if(value===undefined||value===null)return '';if(typeof value!=='string')throw Error('Invalid task snapshot '+field);return value;};
 const name=text('name'),address=text('address');
 const sourceHash=createHash('sha256').update(JSON.stringify([STORE_DOCUMENT_VERSION,source.id,source.grid_task_id,source.store_id,source.revision,name,address])).digest('hex');
 return {documentId:'store-task:'+source.id,storeTaskId:source.id,gridTaskId:source.grid_task_id,storeId:source.store_id,sourceRevision:source.revision,sourceHash,documentVersion:STORE_DOCUMENT_VERSION,normalizerVersion:STORE_NORMALIZER_VERSION,original:{name,address},normalized:{name:normalizeStoreQuery(name),address:normalizeStoreQuery(address),storeId:normalizeStoreQuery(source.store_id)},missing:{name:!name,address:!address}};
}
