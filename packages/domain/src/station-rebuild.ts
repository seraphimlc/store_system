import {stationAssetName} from './station-assets';
export interface AssetStation{id:number;name:string;name_norm:string;pref:string;lon:number|null;lat:number|null;group_code:string;line_id:number|null;place_id:number|null}
export interface AssetLine{id:number;name:string;operator:string;operator_short:string}
export interface AssetPlace{id:number;name:string;name_norm:string;pref:string;lon:number|null;lat:number|null;group_code:string;n_line:number;n_operator:number;operators:string;lines_text:string;source:string}
export function planStationPlaces(stations:AssetStation[],places:AssetPlace[],lines:AssetLine[],taskPlaceIds:ReadonlySet<number>){
 const groups=new Map<string,AssetStation[]>(),official=new Map<string,AssetPlace>(),manual=new Map<string,AssetPlace>(),byLine=new Map(lines.map(row=>[row.id,row]));
 for(const row of [...stations].sort((a,b)=>a.id-b.id)){const key=row.group_code||'manual:'+row.name_norm+'|'+(row.pref||'');const group=groups.get(key)??[];group.push(row);groups.set(key,group);}
 for(const place of [...places].sort((a,b)=>a.id-b.id)){if(place.group_code)official.set(place.group_code,place);else manual.set(JSON.stringify([place.name_norm,place.pref]),place);}
 const updates:{placeId:number|null;stationIds:number[];values:Omit<AssetPlace,'id'>;changed:boolean}[]=[],blockers:{stationId:number;oldPlaceId:number;targetPlaceId:number|null}[]=[],used=new Set<number>();let linked=0;
 for(const rows of groups.values()){
  const first=rows[0],name=stationAssetName(first.name),gc=first.group_code||'',pref=first.pref||'',existing=gc?official.get(gc):manual.get(JSON.stringify([name,pref]));
  const lineIds=rows.flatMap(row=>row.line_id?[row.line_id]:[]),operators=[...new Set(lineIds.flatMap(id=>byLine.has(id)?[byLine.get(id)!.operator_short||byLine.get(id)!.operator]:[]))].sort(),names=[...new Set(lineIds.flatMap(id=>byLine.has(id)?[byLine.get(id)!.name]:[]))].sort();
  const mean=(key:'lon'|'lat')=>{const values=rows.map(row=>row[key]).filter((value):value is number=>value!==null);return values.reduce((sum,value)=>sum+value,0)/Math.max(1,values.length)||null;};
  const values={name:first.name,name_norm:name,pref,lon:mean('lon'),lat:mean('lat'),group_code:gc,n_line:lineIds.length,n_operator:operators.length,operators:operators.join('、'),lines_text:names.join('、'),source:gc?'mlit':'manual'};
  if(existing)used.add(existing.id);for(const row of rows)if(row.place_id!==existing?.id){linked++;if(row.place_id!==null&&taskPlaceIds.has(row.place_id))blockers.push({stationId:row.id,oldPlaceId:row.place_id,targetPlaceId:existing?.id??null});}
  updates.push({placeId:existing?.id??null,stationIds:rows.map(row=>row.id),values,changed:!existing||Object.entries(values).some(([key,value])=>(existing as unknown as Record<string,unknown>)[key]!==value)});
 }
 const orphanIds=places.filter(row=>!used.has(row.id)).map(row=>row.id),retained=orphanIds.filter(id=>taskPlaceIds.has(id)),remove=orphanIds.filter(id=>!taskPlaceIds.has(id));
 return {updates,blockers,retained,remove,summary:{stations:stations.length,places:updates.length,created:updates.filter(row=>row.placeId===null).length,reused:updates.filter(row=>row.placeId!==null).length,changed:updates.filter(row=>row.changed).length,linked,orphansRemoved:remove.length,referencedOrphansRetained:retained.length,blockedLinks:blockers.length}};
}
