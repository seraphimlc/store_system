import {Worker} from 'node:worker_threads';
import type {PoolClient} from 'pg';
import {BusinessError} from './errors';
import type {Database} from './database';

export interface MerchantRuntimePage {total:number;matchedTotal:number;unassignedTotal:number;page:number;pageSize:number;gridGroups:{id:number;name:string;regionId?:number;regionName?:string;count:number}[];regionGroups:{id:number;name:string;count:number}[];storeIds:string[];key:string}
interface Entry {key:string;worker:Worker;ready:Promise<void>;pending:Map<number,{resolve:(r:MerchantRuntimePage)=>void;reject:(e:Error)=>void;timer:ReturnType<typeof setTimeout>}>;nextId:number;disposed:boolean}
const runtimes=new WeakMap<Database,Entry>();
function unavailable(){return new BusinessError('MERCHANT_SEARCH_BUSY','商户搜索索引暂不可用，请稍后重试',503);}
function close(entry:Entry){if(entry.disposed)return;entry.disposed=true;for(const pending of entry.pending.values()){clearTimeout(pending.timer);pending.reject(unavailable());}entry.pending.clear();void entry.worker.terminate();}
export function disposeMerchantSearchRuntime(db:Database){const entry=runtimes.get(db);if(entry)close(entry);runtimes.delete(db);}
export async function merchantRuntimeKey(client:PoolClient){
 const exists=(await client.query("SELECT to_regclass('public.bd_merchant_search_geography_version') AS table_name")).rows[0].table_name;
 if(!exists)throw new BusinessError('MERCHANT_SEARCH_NOT_READY','商户搜索版本结构尚未就绪',503);
 return String((await client.query('SELECT revision::text FROM bd_merchant_search_geography_version WHERE singleton')).rows[0].revision);
}
function entryFor(db:Database,key:string):Entry{
 const previous=runtimes.get(db);if(previous?.key===key&&!previous.disposed)return previous;if(previous)close(previous);
 const modulePath=require.resolve('./merchant-search-runtime-worker'),worker=new Worker(`if(require('node:worker_threads').workerData.register)require(require('node:worker_threads').workerData.register);require(require('node:worker_threads').workerData.modulePath);`,{eval:true,execArgv:[],workerData:{modulePath,register:modulePath.endsWith('.ts')?require.resolve('tsx/cjs'):null,key,connectionString:db.pool.options.connectionString}});
 let resolveReady:()=>void,rejectReady:(e:Error)=>void;const ready=new Promise<void>((resolve,reject)=>{resolveReady=resolve;rejectReady=reject;});const entry:Entry={key,worker,ready,pending:new Map(),nextId:0,disposed:false};runtimes.set(db,entry);
 const timer=setTimeout(()=>{rejectReady(unavailable());close(entry);},120000);
 worker.on('message',message=>{
  if(message.ready){clearTimeout(timer);resolveReady();if(!entry.pending.size)worker.unref();return;}
  if(message.error&&!message.id){clearTimeout(timer);rejectReady(unavailable());close(entry);return;}
  const pending=entry.pending.get(message.id);if(!pending)return;clearTimeout(pending.timer);entry.pending.delete(message.id);if(message.error)pending.reject(unavailable());else pending.resolve(message.result);if(!entry.pending.size)worker.unref();
 });
 const failed=()=>{clearTimeout(timer);rejectReady(unavailable());close(entry);};worker.on('error',failed);worker.on('exit',code=>{if(!entry.disposed||code)failed();});
 // A runtime belongs to this DB lifecycle, not to a request/service instance or another test database.
 if(!previous){const destroy=db.onModuleDestroy.bind(db);db.onModuleDestroy=async()=>{disposeMerchantSearchRuntime(db);await destroy();};}
 return entry;
}
export async function merchantRuntimeSearch(db:Database,key:string,query:string,filter:{page:number;groupGrid?:number;groupRegion?:number},candidates?:{storeId:string;score:number}[]){
 const entry=entryFor(db,key);await entry.ready;if(entry.disposed)throw unavailable();if(entry.pending.size>=8)throw unavailable();entry.worker.ref();
 return new Promise<MerchantRuntimePage>((resolve,reject)=>{const id=++entry.nextId,timer=setTimeout(()=>{entry.pending.delete(id);reject(unavailable());if(!entry.pending.size)entry.worker.unref();},2500);entry.pending.set(id,{resolve,reject,timer});entry.worker.postMessage({id,query,filter,candidates});});
}
/** Prewarm only a published generation. Missing optional schema keeps unrelated API functions available. */
export async function warmMerchantSearch(db:Database){
 const info=await db.transaction(async client=>{const exists=(await client.query("SELECT to_regclass('public.bd_merchant_search_active') AS active,to_regclass('public.bd_merchant_search_geography_version') AS geography")).rows[0];if(!exists.active||!exists.geography)return null;const current=(await client.query('SELECT g.id::text,v.revision::text FROM bd_merchant_search_active a JOIN bd_merchant_search_generation g ON g.id=a.generation_id CROSS JOIN bd_merchant_search_geography_version v WHERE g.ready AND v.singleton')).rows[0];return current?current.id+':'+current.revision:null;});
 if(info)await entryFor(db,info).ready;
}
