import type {Session} from '../../contracts/src';
import {api as request} from './request';
type Ref={path:(string|number)[];key:string};
type Manifest={geometryCacheVersion?:number;geometryRefs?:Ref[];[key:string]:any};
export interface GeometryStore{get(keys:string[]):Promise<Map<string,unknown>>;put(values:Map<string,unknown>):Promise<void>}
export async function geometryHash(value:unknown){const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(value)));return Array.from(new Uint8Array(digest),b=>b.toString(16).padStart(2,'0')).join('');}
const valid=async(key:string,value:unknown)=>!!value&&typeof value==='object'&&await geometryHash(value)===key.split(':').at(-1);
/** Fresh manifests carry permissions/state; storage contains immutable geometry only, never whole API responses. */
export async function hydrateGridGeometry<T>(manifest:Manifest,store:GeometryStore,load:(keys:string[])=>Promise<Record<string,unknown>>):Promise<T>{
 if(manifest.geometryCacheVersion!==1||!manifest.geometryRefs)return manifest as T;
 const refs=manifest.geometryRefs,keys=[...new Set(refs.map(ref=>ref.key))];let cached=new Map<string,unknown>();try{cached=await store.get(keys);}catch{/* Storage denied: network remains authoritative. */}
 const missing:string[]=[];for(const key of keys){if(!await valid(key,cached.get(key)))missing.push(key);}
 const downloaded=new Map<string,unknown>();for(let i=0;i<missing.length;i+=64){const page=await load(missing.slice(i,i+64));for(const key of missing.slice(i,i+64)){if(!await valid(key,page[key]))throw Error('网格边界版本已变化，请重新加载');downloaded.set(key,page[key]);cached.set(key,page[key]);}}
 try{await store.put(downloaded);}catch{/* Quota/private-mode failures must not block maps. */}
 for(const ref of refs){let parent:any=manifest;for(const segment of ref.path.slice(0,-1))parent=parent[segment];parent[ref.path.at(-1)!]=cached.get(ref.key);}
 delete manifest.geometryCacheVersion;delete manifest.geometryRefs;return manifest as T;
}
function persistentStore(userId:number):GeometryStore{
 function database():Promise<IDBDatabase>{return new Promise((resolve,reject)=>{const open=indexedDB.open('store-grid-geometry-v1',1);open.onupgradeneeded=()=>{open.result.createObjectStore('geometry',{keyPath:'key'}).createIndex('savedAt','savedAt');};open.onsuccess=()=>resolve(open.result);open.onerror=()=>reject(open.error);open.onblocked=()=>reject(Error('Geometry cache unavailable'));});}
 const prefix=userId+':';
 return {async get(keys){const db=await database();try{return await new Promise<Map<string,unknown>>((resolve,reject)=>{const tx=db.transaction('geometry','readonly'),store=tx.objectStore('geometry'),values=new Map<string,unknown>();for(const key of keys){const read=store.get(prefix+key);read.onsuccess=()=>{if(read.result)values.set(key,read.result.geometry);};}tx.oncomplete=()=>resolve(values);tx.onabort=()=>reject(tx.error);tx.onerror=()=>reject(tx.error);});}finally{db.close();}},async put(values){if(!values.size)return;const db=await database();try{await new Promise<void>((resolve,reject)=>{const tx=db.transaction('geometry','readwrite'),store=tx.objectStore('geometry');for(const [key,geometry]of values)store.put({key:prefix+key,geometry,savedAt:Date.now()});const count=store.count();count.onsuccess=()=>{let excess=count.result-5000;if(excess<=0)return;const cursor=store.index('savedAt').openCursor();cursor.onsuccess=()=>{const row=cursor.result;if(row&&excess-->0){row.delete();row.continue();}};};tx.oncomplete=()=>resolve();tx.onabort=()=>reject(tx.error);tx.onerror=()=>reject(tx.error);});}finally{db.close();}}};
}
const cacheRoute=/^\/(?:zone-assets\/grid-viewport|batch-work\/(?:batches|regions)\/\d+\/map|batch-work\/grids\/\d+\/(?:map|visits))(?:\?|$)/;
export async function cachedGridApi<T>(path:string,session?:Session|null,body?:unknown,signal?:AbortSignal):Promise<T>{
 if(body!==undefined||!session||!cacheRoute.test(path)||typeof indexedDB==='undefined'||!globalThis.crypto?.subtle)return request<T>(path,session,body,signal);
 const visits=path.match(/^\/batch-work\/grids\/(\d+)\/visits(?:\?|$)/),loadPath=visits?`/batch-work/grids/${visits[1]}/map?limit=1`:path;
 const manifest=await request<Manifest>(path,session,undefined,signal,{'X-Grid-Geometry-Cache':'manifest'});
 return hydrateGridGeometry<T>(manifest,persistentStore(session.user.id),async keys=>{const result=await request<{geometries:Record<string,unknown>}>(loadPath,session,undefined,signal,{'X-Grid-Geometry-Cache':'load:'+keys.join(',')});return result.geometries;});
}
