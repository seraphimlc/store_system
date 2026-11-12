import {stationAssetName} from './station-assets';
export interface SequenceStation{id:number;name:string;line_id:number|null;lat:number|null;lon:number|null;seq?:number|null;along_km?:number|null;seq_src?:string}
export interface SequenceLine{id:number;name:string;operator:string;operator_short?:string}
export interface RouteStop{name:string;lat:number;lon:number;km?:number}
export interface SequenceRoute{rel_id:number;name:string;n_stop:number;stops:RouteStop[]}
export interface FallbackLine{[key:string]:unknown;line_id?:number;operator?:string;name?:string;line?:string;source?:string;max_proj_m?:number;cover?:string;stops:{id:number;name:string;seq:number;km:number|null;proj_m?:number}[]}
export interface SequenceSources{osm:{source:string;license:string;routes:SequenceRoute[]};geometry:{source:string;lines:FallbackLine[]};manual:{source:string;license?:string;lines:FallbackLine[]}}
export function sequenceDistance(lat1:number,lon1:number,lat2:number,lon2:number){const radians=(x:number)=>x*Math.PI/180,p1=radians(lat1),p2=radians(lat2),x=Math.sin((p2-p1)/2)**2+Math.cos(p1)*Math.cos(p2)*Math.sin(radians(lon2-lon1)/2)**2;return 2*6371000*Math.asin(Math.sqrt(Math.min(1,Math.max(0,x))));}
export function sequenceCore(name:string){return name.replace(/[（(].*?[)）]/g,'').trim().replace(/\s*[:：].*$/,'').replace(/\s*[-–—].*$/,'').replace(/\s*(各駅停車|各停|快速|急行|特急|下り|上り|北行|南行|直通運転|直通)$/,'').replace(/^(JR|都営|東京メトロ|東京地下鉄)\s*/,'').replace(/^\d+号線/,'').trim();}
const stationName=(name:string)=>stationAssetName(name).replaceAll('駅','');
const round=(n:number,places:number)=>Number(n.toFixed(places));
export function prepareSequenceRoutes(routes:SequenceRoute[]){return routes.filter(route=>route.n_stop>=2&&route.stops.length>=2).map(route=>{let km=0;return {...route,stops:route.stops.map((stop,index)=>{if(index)km+=sequenceDistance(route.stops[index-1].lat,route.stops[index-1].lon,stop.lat,stop.lon)/1000;return {...stop,km};})};});}
export function bestSequenceRoute(stations:SequenceStation[],lineName:string,routes:SequenceRoute[],near=300){
 const own=stations.filter(row=>row.lat!==null&&row.lon!==null);if(!own.length)return null;const core=sequenceCore(lineName),candidates:{tier:number;route:SequenceRoute;hit:number;n:number;f1:number;precision:number;rank:(number|string)[]}[]=[];
 for(const route of routes){const stops=route.stops;if(stops.length<2||stops.length>3*own.length+10)continue;let hit=0,reverse=0;for(const row of own)if(stops.some(stop=>sequenceDistance(row.lat!,row.lon!,stop.lat,stop.lon)<=near))hit++;for(const stop of stops)if(own.some(row=>sequenceDistance(row.lat!,row.lon!,stop.lat,stop.lon)<=near))reverse++;
  const recall=hit/own.length,precision=reverse/stops.length;if(recall<.5||own.length<=3&&hit<own.length)continue;const f1=recall+precision?2*recall*precision/(recall+precision):0,related=core.length>=3&&sequenceCore(route.name).includes(core),tier=f1>=.7?1:related&&recall>=.85?2:related&&recall>=.5?3:0;if(!tier)continue;candidates.push({tier,route,hit,n:own.length,f1,precision,rank:[tier,-hit,-round(f1,4),Math.abs(stops.length-own.length),route.name||'']});
 }
 candidates.sort((a,b)=>{for(let i=0;i<a.rank.length;i++){const x=a.rank[i],y=b.rank[i];if(x===y)continue;return x<y?-1:1;}return 0;});return candidates[0]??null;
}
export interface SequenceEntry{stationId:number;seq:number;km:number|null;source:string}
export function planStationSequence(lines:SequenceLine[],stations:SequenceStation[],sources:SequenceSources,near=300){
 if(!Number.isFinite(near)||near<=0||near>2000)throw Error('SEQUENCE_NEAR_INVALID');const routes=prepareSequenceRoutes(sources.osm.routes),entries:SequenceEntry[]=[],reports:{lineId:number;name:string;source:string|null;matched:number;total:number;tier?:number;reason?:string}[]=[];
 const fallback=(line:SequenceLine,own:SequenceStation[],reference:FallbackLine,source:string)=>{
  if(source==='geom:N02'&&(!Number.isFinite(reference.max_proj_m)||reference.max_proj_m!>250||reference.stops.some(stop=>!Number.isFinite(stop.proj_m)||stop.proj_m!>250)))return {ok:false,reason:'GEOMETRY_QUALITY'};
  const found:SequenceEntry[]=[],seen=new Set<number>();for(const stop of reference.stops){const candidates=own.filter(row=>stationName(row.name)===stationName(stop.name)),row=candidates.length===1?candidates[0]:undefined;if(!row||seen.has(row.id)||!Number.isInteger(stop.seq)||stop.seq<1||stop.km!==null&&(!Number.isFinite(stop.km)||stop.km<0))return {ok:false,reason:'REFERENCE_STATION_MISMATCH'};seen.add(row.id);found.push({stationId:row.id,seq:stop.seq,km:stop.km,source});}
  if(seen.size!==own.length||new Set(found.map(row=>row.seq)).size!==found.length)return {ok:false,reason:'REFERENCE_INCOMPLETE'};entries.push(...found);reports.push({lineId:line.id,name:line.name,source,matched:found.length,total:own.length});return {ok:true};
 };
 for(const line of [...lines].sort((a,b)=>a.id-b.id)){
  const own=stations.filter(row=>row.line_id===line.id),pick=bestSequenceRoute(own,line.name,routes,near),geometries=sources.geometry.lines.filter(row=>row.name===line.name&&row.operator===line.operator),geom=geometries.length===1?geometries[0]:undefined,manuals=sources.manual.lines.filter(row=>(row.line??row.name)===line.name&&row.operator===line.operator),manual=manuals.length===1?manuals[0]:undefined;
  if((!pick||pick.hit<.5*own.length)&&geom){const result=fallback(line,own,geom,'geom:N02');if(!result.ok)reports.push({lineId:line.id,name:line.name,source:null,matched:0,total:own.length,reason:result.reason});continue;}
  if(manual){const result=fallback(line,own,manual,'manual:'+(manual.source??'reference').split(' ')[0]);if(!result.ok)reports.push({lineId:line.id,name:line.name,source:null,matched:0,total:own.length,reason:result.reason});continue;}
  if(!pick){reports.push({lineId:line.id,name:line.name,source:null,matched:0,total:own.length,reason:'NO_MATCH'});continue;}
  let matched=0;const source='osm:'+(pick.route.name||'rel'+pick.route.rel_id);for(const station of own){if(station.lat===null||station.lon===null)continue;let best=pick.route.stops.reduce((a,b)=>sequenceDistance(station.lat!,station.lon!,a.lat,a.lon)<=sequenceDistance(station.lat!,station.lon!,b.lat,b.lon)?a:b);if(sequenceDistance(station.lat,station.lon,best.lat,best.lon)>near){const same=pick.route.stops.filter(stop=>stationName(stop.name)===stationName(station.name));if(!same.length)continue;best=same.reduce((a,b)=>sequenceDistance(station.lat!,station.lon!,a.lat,a.lon)<=sequenceDistance(station.lat!,station.lon!,b.lat,b.lon)?a:b);if(sequenceDistance(station.lat,station.lon,best.lat,best.lon)>2000)continue;}entries.push({stationId:station.id,seq:pick.route.stops.indexOf(best)+1,km:round(best.km??0,3),source});matched++;}
  reports.push({lineId:line.id,name:line.name,source,matched,total:own.length,tier:pick.tier});
 }
 const byId=new Map(entries.map(row=>[row.stationId,row]));let changed=0,cleared=0;for(const station of stations){const row=byId.get(station.id);if(row){if(station.seq!==row.seq||station.along_km!==row.km||(station.seq_src??'')!==row.source)changed++;}else if(station.seq!==null&&station.seq!==undefined||station.along_km!==null&&station.along_km!==undefined||station.seq_src){cleared++;}}
 return {entries,reports,summary:{stations:stations.length,planned:entries.length,changed,cleared,fullLines:reports.filter(row=>row.source&&row.matched===row.total).length,partialLines:reports.filter(row=>row.source&&row.matched!==row.total).length,unmatchedLines:reports.filter(row=>!row.source).length},licenses:[sources.osm.license,sources.manual.license??''].filter(Boolean)};
}
