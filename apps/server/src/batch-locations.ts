import {createHash} from 'node:crypto';
import type {PoolClient} from 'pg';
import {BusinessError} from './errors';
function pair(lat:unknown,lon:unknown){const a=typeof lat==='number'?lat:typeof lat==='string'&&lat.trim()?Number(lat):NaN,b=typeof lon==='number'?lon:typeof lon==='string'&&lon.trim()?Number(lon):NaN;return Number.isFinite(a)&&Number.isFinite(b)&&Math.abs(a)<=90&&Math.abs(b)<=180?{lat:a,lon:b}:null;}
export async function batchMapRows(client:PoolClient,gridId:number,after:number,limit:number){
 const {rows}=await client.query(`SELECT s.id,s.store_id,s.snapshot,s.status,s.revision,d.lat AS profile_lat,d.lon AS profile_lon,coalesce(d.revision,0) AS profile_revision,
  m.lat AS source_lat,m.lon AS source_lon,coalesce(
   (SELECT e.new_value->>'location_accuracy' FROM bd_task_event e JOIN bd_store_task t ON t.id=e.store_task_id
    WHERE t.store_id=s.store_id AND e.event_type='verify_store_location' ORDER BY e.created_at DESC,e.id DESC LIMIT 1),nullif(m.location_accuracy,''),
   s.snapshot->>'location_accuracy','pending') AS location_accuracy FROM bd_store_task s LEFT JOIN bd_merchant_detail d ON d.store_id=s.store_id
  LEFT JOIN public.kanto_merchants_v20261029 m ON m.place_id=s.store_id WHERE s.grid_task_id=$1 AND s.id>$2 ORDER BY s.id LIMIT $3`,[gridId,after,limit+1]);
 const items=rows.slice(0,limit).map(row=>{const point=pair(row.profile_lat,row.profile_lon)??pair(row.source_lat,row.source_lon)??pair(row.snapshot?.lat,row.snapshot?.lon);return {id:row.id,storeId:row.store_id,name:String(row.snapshot?.name??row.store_id).slice(0,300),address:String(row.snapshot?.address??'').slice(0,500),status:row.status,revision:row.revision,profileRevision:row.profile_revision,lat:point?.lat??null,lon:point?.lon??null,accuracy:row.location_accuracy||row.snapshot?.location_accuracy||'pending'};});
 return {items,hasMore:rows.length>limit,nextCursor:rows.length>limit?items.at(-1)?.id??null:null};
}
export interface LocationInput{revision:number;profileRevision:number;accuracy:'pending'|'accurate'|'nonexistent'|'doubtful';lat?:number;lon?:number}
/** Caller already holds current batch/grid/store locks and owns the transaction. */
export async function writeLocation(client:PoolClient,store:any,input:LocationInput){
 if(store.revision!==input.revision)throw new BusinessError('TASK_CHANGED','店铺任务已变化，请刷新后重试',409);
 await client.query("SELECT pg_advisory_xact_lock(hashtext('merchant-photos'),hashtext($1))",[store.store_id]);
 // Also serialize with the retained Python detail writer during the short coexistence window.
 await client.query('SELECT pg_advisory_xact_lock($1::bigint)',[createHash('sha256').update(store.store_id).digest().readBigInt64BE(0).toString()]);
 await client.query("INSERT INTO bd_merchant_detail(store_id,photo_urls,revision,updated_at,place_lookup_state) VALUES($1,'[]',0,now(),'pending') ON CONFLICT(store_id) DO NOTHING",[store.store_id]);
 const {rows:[profile]}=await client.query('SELECT * FROM bd_merchant_detail WHERE store_id=$1 FOR UPDATE',[store.store_id]);
 if(profile.revision!==input.profileRevision)throw new BusinessError('LOCATION_CHANGED','商户资料已变化，请刷新后重试',409);
 const {rows:[source]}=await client.query('SELECT lat,lon FROM public.kanto_merchants_v20261029 WHERE place_id=$1',[store.store_id]);
 const snapshot={...store.snapshot},current=pair(profile.lat,profile.lon)??pair(source?.lat,source?.lon)??pair(snapshot.lat,snapshot.lon);
 const changed=input.lat!==undefined&&(input.lat!==current?.lat||input.lon!==current?.lon),accuracy=input.lat!==undefined?'accurate':input.accuracy;
 if(changed){await client.query("UPDATE bd_merchant_detail SET lat=$1,lon=$2,google_place_id=NULL,place_lookup_state='unconfigured',revision=revision+1,updated_at=now() WHERE store_id=$3",[input.lat,input.lon,store.store_id]);profile.revision++;profile.google_place_id=null;profile.place_lookup_state='unconfigured';}
 const before={location_accuracy:snapshot.location_accuracy??'pending',lat:snapshot.lat??null,lon:snapshot.lon??null};
 // Keep merchant source read-only; current accuracy is reconstructed from the ops audit stream.
 if(input.lat!==undefined){snapshot.lat=input.lat;snapshot.lon=input.lon;}snapshot.location_accuracy=accuracy;
 if(store.status!=='completed')await client.query('UPDATE bd_store_task SET snapshot=$1,revision=revision+1,updated_at=now() WHERE id=$2',[JSON.stringify(snapshot),store.id]);
 else await client.query('UPDATE bd_store_task SET updated_at=now() WHERE id=$1',[store.id]);
 const after={location_accuracy:accuracy,lat:input.lat??before.lat,lon:input.lon??before.lon};
 return {before,after,result:{accuracy,lat:after.lat,lon:after.lon,googlePlaceId:profile.google_place_id,placeLookupState:profile.place_lookup_state,revision:store.revision+(store.status==='completed'?0:1),profileRevision:profile.revision}};
}
