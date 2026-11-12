import {Alert} from '@appica/ui-react/alert';
import {useEffect,useRef,useState} from 'react';
import {Button} from '@appica/ui-react/button';
import type {Session} from '../../../packages/contracts/src';
import type {GridSelection} from './GridMerge';
import {api} from './api';
import {googleMaps} from './google-maps';
import './grid-map.css';
export type GridMapProperties=GridSelection&{name:string;sourceId:string;state:string;zoneName:string|null;selectable:boolean;stores:number;areaKm2?:number};
type Feature={type:'Feature';id:number;geometry:object;properties:GridMapProperties};
type Page={type:'FeatureCollection';features:Feature[];hasMore:boolean;nextCursor:number|null};
const centers:Record<string,{lat:number;lng:number}>={tokyo23:{lat:35.68,lng:139.76},tokyo26:{lat:35.69,lng:139.4},kanagawa:{lat:35.45,lng:139.5},saitama:{lat:35.86,lng:139.65},chiba:{lat:35.65,lng:140.1},all:{lat:35.7,lng:139.7}};
export function GridMap({session,language,filter,selections,onToggle,reload,onFocus,directSelect=false}:{session:Session;language:string;filter:{q:string;region:string;state:string};selections:Record<number,GridSelection>;onToggle:(grid:GridMapProperties)=>void;reload:number;onFocus?:(grid:GridMapProperties)=>void;directSelect?:boolean}){
 const t=(zh:string,jp:string)=>language==='ja'?jp:zh;
 const element=useRef<HTMLDivElement>(null),map=useRef<google.maps.Map|null>(null),layer=useRef<google.maps.Data|null>(null),generation=useRef(0),current=useRef({selections,onToggle,onFocus,directSelect}),request=useRef<()=>void>(()=>{}),loadingMore=useRef(false),handledMore=useRef(0);
 const [ready,setReady]=useState(false),[error,setError]=useState(''),[unconfigured,setUnconfigured]=useState(false),[busy,setBusy]=useState(false),[rows,setRows]=useState<Feature[]>([]),[focus,setFocus]=useState<GridMapProperties>(),[next,setNext]=useState<number|null>(null),[refresh,setRefresh]=useState(0),[more,setMore]=useState(0);
 current.current={selections,onToggle,onFocus,directSelect};
 useEffect(()=>{let cancelled=false;let idle:google.maps.MapsEventListener|undefined,click:google.maps.MapsEventListener|undefined;
  setError('');setUnconfigured(false);api<{enabled:boolean;key:string|null}>('/zone-assets/map-config',session).then(async config=>{
   if(cancelled)return;if(!config.enabled||!config.key){setUnconfigured(true);return;}
   const maps=await googleMaps(config.key,language);if(cancelled||!element.current)return;
   const instance=new maps.Map(element.current,{center:centers[filter.region]??centers.tokyo23,zoom:12,mapTypeControl:false,streetViewControl:false,fullscreenControl:false,gestureHandling:'cooperative'}),data=new maps.Data({map:instance});
   map.current=instance;layer.current=data;
   data.setStyle(feature=>({fillColor:current.current.selections[Number(feature.getId())]?'#0e7490':feature.getProperty('state')==='disabled'?'#94a3b8':'#64748b',fillOpacity:.18,strokeColor:current.current.selections[Number(feature.getId())]?'#0e7490':'#475569',strokeWeight:current.current.selections[Number(feature.getId())]?3:1.5,clickable:true}));
   click=data.addListener('click',(event:google.maps.Data.MouseEvent)=>{const feature=event.feature,properties:GridMapProperties={id:Number(feature.getId()),revision:feature.getProperty('revision') as number,zoneId:feature.getProperty('zoneId') as number|null,zoneRevision:feature.getProperty('zoneRevision') as number|null,name:feature.getProperty('name') as string,sourceId:feature.getProperty('sourceId') as string,state:feature.getProperty('state') as string,zoneName:feature.getProperty('zoneName') as string|null,selectable:feature.getProperty('selectable') as boolean,stores:feature.getProperty('stores') as number,areaKm2:feature.getProperty('areaKm2') as number};setFocus(properties);if(current.current.directSelect){if(properties.selectable)current.current.onToggle(properties);else current.current.onFocus?.(properties);}});
   idle=instance.addListener('idle',()=>request.current());setReady(true);
  }).catch(()=>{if(!cancelled)setError('SDK');});
  return()=>{cancelled=true;generation.current++;idle?.remove();click?.remove();layer.current?.setMap(null);layer.current=null;map.current=null;};
 },[session,refresh]);
 useEffect(()=>{layer.current?.setStyle(feature=>({fillColor:selections[Number(feature.getId())]?'#2563eb':'#64748b',fillOpacity:selections[Number(feature.getId())]?.28:.06,strokeColor:selections[Number(feature.getId())]?'#2563eb':'#475569',strokeWeight:selections[Number(feature.getId())]?2:1,clickable:true}));},[selections]);
 useEffect(()=>{if(!ready||!map.current)return;map.current.setCenter(centers[filter.region]??centers.tokyo23);map.current.setZoom(filter.region==='all'?9:12);},[ready,filter.region]);
 useEffect(()=>{
  if(!ready)return;let timer:number|undefined;let stopped=false;
  async function load(after=0){const instance=map.current,data=layer.current,bounds=instance?.getBounds();if(!instance||!data||!bounds)return;
   if(after&&loadingMore.current)return;const version=++generation.current;loadingMore.current=after>0;setBusy(true);setError('');if(!after){setNext(null);setFocus(undefined);setRows([]);data.forEach(feature=>data.remove(feature));}
   const view=bounds.toJSON(),params=new URLSearchParams({q:filter.q,region:filter.region,state:filter.state,west:String(view.west),east:String(view.east),south:String(view.south),north:String(view.north),after:String(after)});
   try{const page=await api<Page>('/zone-assets/grid-viewport?'+params,session);if(stopped||version!==generation.current)return;data.addGeoJson({type:'FeatureCollection',features:page.features});setRows(previous=>after?[...previous,...page.features]:page.features);setNext(page.hasMore?page.nextCursor:null);}
   catch(cause){if(!stopped&&version===generation.current){setError((cause as Error).message);setNext(null);}}
   finally{if(!stopped&&version===generation.current){loadingMore.current=false;setBusy(false);}}
  }
  request.current=()=>{window.clearTimeout(timer);generation.current++;timer=window.setTimeout(()=>void load(),180);};
  const append=more!==handledMore.current;handledMore.current=more;
  if(append&&next!==null)void load(next);else request.current();
  return()=>{stopped=true;generation.current++;window.clearTimeout(timer);loadingMore.current=false;};
 // next is the continuation cursor, not a trigger: each explicit load-more increments more.
 },[ready,filter.q,filter.region,filter.state,reload,more,session]);
 useEffect(()=>{if(!directSelect||busy||next===null||rows.length>=1000)return;const timer=window.setTimeout(()=>setMore(value=>value+1),40);return()=>window.clearTimeout(timer);},[directSelect,busy,next,rows.length]);
 return <section className="grid-map">{unconfigured&&<p role="status">{t('尚未配置地图公钥，可继续使用列表选择和合并。','地図の公開キーが未設定です。一覧で選択・結合できます。')}</p>}
 {error&&<Alert role="alert">{error==='SDK'?t('地图加载失败，请检查公钥与来源限制；可继续使用列表。','地図を読み込めません。公開キーと参照元制限を確認してください。一覧は利用できます。'):error}</Alert>}
 {!ready&&!unconfigured&&!error&&<p role="status">{t('正在加载地图…','地図を読込中…')}</p>}
 <div ref={element} className="grid-map-canvas" aria-label={t('基础网格地图画布','基礎グリッド地図キャンバス')} hidden={unconfigured||error==='SDK'}/>
 {ready&&<div className="grid-map-controls"><p role="status">{busy?t('正在读取当前视口…','表示範囲を読込中…'):t('视口已加载网格：','表示範囲のグリッド：')+rows.length}{next!==null&&!busy&&t('，还有网格未加载','、未読込のグリッドがあります')}</p>{next!==null&&<Button type="button" variant="outline" disabled={busy} onClick={()=>setMore(value=>value+1)}>{t('加载更多地图网格','地図グリッドを追加読込')}</Button>}
 {focus&&!directSelect&&<article className="grid-map-focus"><h5>{focus.name||focus.sourceId} · #{focus.id}</h5><p>{focus.zoneName||t('未划入片区','地域未編入')} · {focus.stores} {t('家商户','店舗')}</p>{focus.selectable?<Button type="button" variant="outline" disabled={busy} onClick={()=>onToggle(focus)}>{selections[focus.id]?t('取消选择地图网格','地図グリッドの選択を解除'):t('选择此地图网格','この地図グリッドを選択')}</Button>:<p>{t('此网格当前受保护或已禁用，不能用于合并。','保護または無効のため結合に選択できません。')}</p>}</article>}
 {!directSelect&&<details><summary>{t('当前视口网格列表','表示範囲のグリッド一覧')}</summary><ul>{rows.map(feature=><li key={feature.id}><Button type="button" onClick={()=>setFocus(feature.properties)}>{feature.properties.name||feature.properties.sourceId} · #{feature.id}</Button></li>)}</ul></details>}</div>}
 
 </section>;
}
