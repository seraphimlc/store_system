import {Worker} from 'node:worker_threads';
import {createHash} from 'node:crypto';
import {BusinessError} from './errors';
import type {StoreNameDocument,StoreNameRetrieval} from '../../../packages/domain/src/store-name-index';

// Measured 20k-document construction takes ~387ms on the API thread. Keep construction off
// that thread and reuse at most two immutable corpus indexes (max 300k cached documents).
const workerSource=`
const {parentPort,workerData}=require('node:worker_threads');
if(workerData.register)require(workerData.register);
const {StoreNameIndex}=require(workerData.modulePath);
const cache=new Map();
parentPort.on('message',message=>{
 try{
  let entry=cache.get(message.fingerprint),cached=!!entry;
  if(entry)cache.delete(message.fingerprint);
  else {if(!message.documents){parentPort.postMessage({id:message.id,needDocuments:true});return;}let count=[...cache.values()].reduce((n,x)=>n+x.size,0);while(cache.size&&(cache.size>=2||count+message.documents.length>300000)){const key=cache.keys().next().value;count-=cache.get(key).size;cache.delete(key);}entry={index:new StoreNameIndex(message.documents),size:message.documents.length};}
  if(entry.size<=300000){cache.set(message.fingerprint,entry);let count=[...cache.values()].reduce((n,x)=>n+x.size,0);while(cache.size>2||count>300000){const key=cache.keys().next().value;count-=cache.get(key).size;cache.delete(key);}}
  const literalCandidates=entry.index.literalSearch(message.query),names=literalCandidates.length?{method:null,candidates:[],status:'ready'}:entry.index.search(message.query),result={...names,literalCandidates,wordCandidates:literalCandidates.length||names.candidates.length?[]:entry.index.relatedWords(message.query)};
  parentPort.postMessage({id:message.id,result,cached,cacheKeys:[...cache.keys()]});
 }catch(error){parentPort.postMessage({id:message.id,error:String(error?.message??error)});}
});`;
type RuntimeNameRetrieval=StoreNameRetrieval&{literalCandidates:import('../../../packages/domain/src/store-name-index').StoreNameCandidate[];wordCandidates:import('../../../packages/domain/src/store-name-index').StoreNameCandidate[]};
let worker:Worker|null=null,nextId=0,cachedFingerprints=new Set<string>();
const pending=new Map<number,{documents:StoreNameDocument[];query:string;fingerprint:string;resolve:(value:RuntimeNameRetrieval)=>void;reject:(error:Error)=>void;timer:ReturnType<typeof setTimeout>}>();
function stop(error:Error){const current=worker;worker=null;cachedFingerprints.clear();for(const task of pending.values()){clearTimeout(task.timer);task.reject(error);}pending.clear();void current?.terminate();}
function start(){if(worker)return worker;const modulePath=require.resolve('../../../packages/domain/src/store-name-index');const instance=new Worker(workerSource,{eval:true,execArgv:[],workerData:{modulePath,register:modulePath.endsWith('.ts')?require.resolve('tsx/cjs'):null}});worker=instance;
 instance.on('message',message=>{if(worker!==instance)return;const task=pending.get(message.id);if(!task)return;if(message.needDocuments){instance.postMessage({id:message.id,fingerprint:task.fingerprint,query:task.query,documents:task.documents});return;}if(Array.isArray(message.cacheKeys))cachedFingerprints=new Set(message.cacheKeys);pending.delete(message.id);clearTimeout(task.timer);if(message.error)task.reject(new BusinessError('SEARCH_UNAVAILABLE','名称候选暂不可用，请重试',503));else task.resolve(message.result);if(!pending.size)instance.unref();});
 instance.on('error',()=>{if(worker===instance)stop(new BusinessError('SEARCH_UNAVAILABLE','名称候选暂不可用，请重试',503));});
 instance.on('exit',()=>{if(worker===instance)stop(new BusinessError('SEARCH_UNAVAILABLE','名称候选检索已中断，请重试',503));});instance.unref();return instance;
}
/** Documents must already be restricted to the freshly authorized scope, on the caller's snapshot. */
export async function retrieveStoreNames(documents:StoreNameDocument[],query:string):Promise<RuntimeNameRetrieval>{
 if(pending.size>=8)throw new BusinessError('SEARCH_BUSY','名称候选查询繁忙，请稍后重试',503);
 // Fingerprint includes every name/address, ID and order; no cache can bypass current authorization or source changes.
 const fingerprint=createHash('sha256').update(JSON.stringify(documents)).digest('hex');const instance=start(),id=++nextId;instance.ref();
 return new Promise((resolve,reject)=>{const timer=setTimeout(()=>{if(worker===instance)stop(new BusinessError('SEARCH_TIMEOUT','名称候选查询超时，请重试',503));},10000);pending.set(id,{resolve,reject,timer,documents,query,fingerprint});instance.postMessage({id,fingerprint,documents:cachedFingerprints.has(fingerprint)?undefined:documents,query});});
}
export function stopStoreNameRetrieval(){stop(new BusinessError('SEARCH_UNAVAILABLE','名称候选检索已关闭',503));}
