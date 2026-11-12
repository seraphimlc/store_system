import {UISuccessNotice} from './ui-controls';
import {UIFieldset} from './ui-controls';
import {UIButton,UIForm,UISelect,UIInput} from './ui-controls';
import {useEffect,useLayoutEffect,useId,useRef,useState,type FormEvent} from 'react';
import type {Session} from '../../contracts/src';
import {api} from './request';
import {googleMaps} from './google-maps';
import './batch-map.css';
type Point={id:number;storeId:string;name:string;address:string;status:string;revision:number;profileRevision:number;lat:number|null;lon:number|null;accuracy:string};
type Page={items:Point[];boundary:unknown;gridId:number;total:number;canExecute:boolean;hasMore:boolean;nextCursor:number|null};
function extent(boundary:unknown,points:Point[]=[]){let west=Infinity,east=-Infinity,south=Infinity,north=-Infinity;const point=(x:number,y:number)=>{if(Number.isFinite(x)&&Number.isFinite(y)&&Math.abs(x)<=180&&Math.abs(y)<=90){west=Math.min(west,x);east=Math.max(east,x);south=Math.min(south,y);north=Math.max(north,y);}};
 function visit(value:unknown){if(!Array.isArray(value))return;if(value.length===2&&typeof value[0]==='number'&&typeof value[1]==='number')point(value[0],value[1]);else for(const child of value)visit(child);}visit((boundary as {coordinates?:unknown}|null)?.coordinates);for(const row of points)if(row.lat!==null&&row.lon!==null)point(row.lon,row.lat);
 return Number.isFinite(west)?{west,east,south,north}:null;
}
export function BatchMap(props:{mobile?:boolean;gridId:number;session:Session;language:string;refresh:number;onSelect:(id:number)=>void;onUpdated:()=>Promise<void>}){
 const [open,setOpen]=useState(false),t=(zh:string,jp:string)=>props.language==='ja'?jp:zh;
 return <section className="batch-map"><UIButton type="button" onClick={()=>setOpen(!open)}>{open?t('收起巡店地图','巡回地図を閉じる'):t('打开巡店地图','巡回地図を開く')}</UIButton>{open&&<ExecutionMap {...props}/>}</section>;
}
function ExecutionMap({mobile=false,gridId,session,language,refresh,onSelect,onUpdated}:{mobile?:boolean;gridId:number;session:Session;language:string;refresh:number;onSelect:(id:number)=>void;onUpdated:()=>Promise<void>}){
 const t=(zh:string,jp:string)=>language==='ja'?jp:zh;
 const drawer=useRef<HTMLDetailsElement>(null);
 const [data,setData]=useState<Page>(),[items,setItems]=useState<Point[]>([]),[focus,setFocus]=useState<Point>(),[lat,setLat]=useState(''),[lon,setLon]=useState(''),[accuracy,setAccuracy]=useState('pending'),[loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[mapError,setMapError]=useState(false),[unconfigured,setUnconfigured]=useState(false),[following,setFollowing]=useState(false),[gpsError,setGpsError]=useState(false),[version,setVersion]=useState(0),[cursor,setCursor]=useState(0),[sdk,setSdk]=useState<typeof google.maps>(),[expanded,setExpanded]=useState(false);
 const headingId=useId(),panel=useRef<HTMLDivElement>(null),fullscreenButton=useRef<HTMLButtonElement>(null),gpsGeneration=useRef(0);
 const element=useRef<HTMLDivElement>(null),map=useRef<google.maps.Map|null>(null),pointsLayer=useRef<google.maps.Data|null>(null),boundaryLayer=useRef<google.maps.Data|null>(null),gpsLayer=useRef<google.maps.Data|null>(null),generation=useRef(0),writing=useRef(false),watch=useRef<number|null>(null),alive=useRef(true),current=useRef({items,boundary:data?.boundary});current.current={items,boundary:data?.boundary};
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;gpsGeneration.current++;if(watch.current!==null)navigator.geolocation?.clearWatch(watch.current);};},[]);
 useEffect(()=>{let ignore=false;setUnconfigured(false);setMapError(false);api<{enabled:boolean;key:string|null}>(`/batch-work/grids/${gridId}/map-config`,session).then(async config=>{if(ignore)return;if(!config.enabled||!config.key){setUnconfigured(true);return;}try{const maps=await googleMaps(config.key,language);if(!ignore)setSdk(maps);}catch{if(!ignore)setMapError(true);}}).catch(cause=>{if(!ignore)setError(cause.message);});return()=>{ignore=true;};},[gridId,session]);
 useEffect(()=>setCursor(0),[refresh]);
 useEffect(()=>{const request=++generation.current;setLoading(true);setError('');const after=cursor;
  api<Page>(`/batch-work/grids/${gridId}/map?after=${after}`,session).then(value=>{if(request!==generation.current)return;setData(previous=>({...value,boundary:value.boundary??previous?.boundary??null}));setItems(previous=>after?[...previous,...value.items]:value.items);setFocus(previous=>previous?value.items.find(row=>row.id===previous.id)??previous:undefined);}).catch(cause=>{if(request===generation.current)setError(cause.message);}).finally(()=>{if(request===generation.current)setLoading(false);});
  return()=>{generation.current++;};
 },[gridId,cursor,version,refresh,session]);
 useEffect(()=>{if(!sdk||!data||!element.current||map.current)return;const bounds=extent(data.boundary,items),instance=new sdk.Map(element.current,{center:bounds?{lat:(bounds.south+bounds.north)/2,lng:(bounds.west+bounds.east)/2}:{lat:35.68,lng:139.76},zoom:15,gestureHandling:'cooperative',mapTypeControl:false,streetViewControl:false,fullscreenControl:false});
  map.current=instance;const markers=new sdk.Data({map:instance}),boundary=new sdk.Data({map:instance}),gps=new sdk.Data({map:instance});pointsLayer.current=markers;boundaryLayer.current=boundary;gpsLayer.current=gps;
  markers.setStyle(feature=>({title:String(feature.getProperty('name')??''),label:feature.getProperty('status')==='completed'?'✓':'•'}));boundary.setStyle({fillColor:'#0e7490',fillOpacity:.04,strokeColor:'#0e7490',strokeWeight:2,clickable:false});
  if(bounds)instance.fitBounds(bounds,24);const click=markers.addListener('click',(event:google.maps.Data.MouseEvent)=>{const row=current.current.items.find(item=>item.id===Number(event.feature.getId()));if(row)setFocus(row);});
  const drag=instance.addListener('dragstart',stopFollowing);
  return()=>{click.remove();drag.remove();markers.setMap(null);boundary.setMap(null);gps.setMap(null);pointsLayer.current=null;boundaryLayer.current=null;gpsLayer.current=null;map.current=null;};
 },[sdk,!!data]);
 useEffect(()=>{const markers=pointsLayer.current,boundary=boundaryLayer.current;if(!markers||!boundary)return;markers.forEach(feature=>markers.remove(feature));boundary.forEach(feature=>boundary.remove(feature));
  if(data?.boundary)boundary.addGeoJson({type:'Feature',geometry:data.boundary,properties:{}});
  markers.addGeoJson({type:'FeatureCollection',features:items.filter(point=>point.lat!==null&&point.lon!==null).map(point=>({type:'Feature',id:point.id,geometry:{type:'Point',coordinates:[point.lon,point.lat]},properties:{name:point.name,status:point.status}}))});
 },[items,data?.boundary,sdk]);
 useEffect(()=>{if(!focus)return;setLat(focus.lat===null?'':String(focus.lat));setLon(focus.lon===null?'':String(focus.lon));setAccuracy(focus.accuracy);},[focus?.id,focus?.revision,focus?.profileRevision,focus?.accuracy,focus?.lat,focus?.lon]);
 useEffect(()=>setNotice(''),[focus?.id]);
 function stopFollowing(){gpsGeneration.current++;if(watch.current!==null)navigator.geolocation?.clearWatch(watch.current);watch.current=null;setFollowing(false);}
 function locate(){if(watch.current!==null){stopFollowing();return;}setGpsError(false);if(!navigator.geolocation){setGpsError(true);return;}const request=++gpsGeneration.current;setFollowing(true);watch.current=navigator.geolocation.watchPosition(position=>{if(!alive.current||request!==gpsGeneration.current)return;const {latitude,longitude}=position.coords;map.current?.setCenter({lat:latitude,lng:longitude});gpsLayer.current?.forEach(feature=>gpsLayer.current?.remove(feature));gpsLayer.current?.addGeoJson({type:'Feature',id:'my-position',geometry:{type:'Point',coordinates:[longitude,latitude]},properties:{name:t('我的位置','現在地')}});},()=>{if(!alive.current||request!==gpsGeneration.current)return;stopFollowing();setGpsError(true);},{enableHighAccuracy:true,timeout:15000,maximumAge:5000});}

 async function save(event:FormEvent){event.preventDefault();if(!focus||writing.current)return;if((!!lat.trim())!==(!!lon.trim())){setError(t('纬度和经度必须同时填写','緯度と経度を両方入力してください'));return;}
  writing.current=true;setBusy(true);setError('');setNotice('');try{await api(`/batch-work/stores/${focus.id}/location`,session,{revision:focus.revision,profileRevision:focus.profileRevision,accuracy,...lat.trim()&&(Number(lat)!==focus.lat||Number(lon)!==focus.lon)?{lat:Number(lat),lon:Number(lon)}:{}});if(!alive.current)return;setNotice(t('门店定位已保存','店舗の位置を保存しました'));setCursor(0);setVersion(value=>value+1);await onUpdated();}catch(cause){if(alive.current)setError((cause as Error).message);}finally{writing.current=false;if(alive.current)setBusy(false);}}
 function showAll(){stopFollowing();const bounds=extent(current.current.boundary,current.current.items);if(bounds)map.current?.fitBounds(bounds,24);}
 useEffect(()=>{map.current?.setOptions({gestureHandling:expanded?'greedy':'cooperative'});const frame=requestAnimationFrame(()=>{if(map.current)sdk?.event?.trigger(map.current,'resize');});return()=>cancelAnimationFrame(frame);},[expanded,sdk,!!data]);
 useEffect(()=>{if(!expanded)return;const overflow=document.body.style.overflow;document.body.style.overflow='hidden';fullscreenButton.current?.focus();
  const keyboard=(event:KeyboardEvent)=>{if(event.key==='Escape'){event.preventDefault();event.stopPropagation();setExpanded(false);return;}if(event.key!=='Tab')return;
   const controls=Array.from(panel.current?.querySelectorAll<HTMLElement>('button:not([disabled]),a[href],input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex="0"]')??[]).filter(node=>node.getClientRects().length);
   const first=controls[0],last=controls.at(-1);if(!first){event.preventDefault();return;}if(event.shiftKey&&(document.activeElement===first||!panel.current?.contains(document.activeElement))){event.preventDefault();last?.focus();}else if(!event.shiftKey&&(document.activeElement===last||!panel.current?.contains(document.activeElement))){event.preventDefault();first.focus();}
  };document.addEventListener('keydown',keyboard,true);return()=>{document.removeEventListener('keydown',keyboard,true);document.body.style.overflow=overflow;fullscreenButton.current?.focus();};
 },[expanded]);
 useLayoutEffect(()=>{
  if(!mobile||expanded)return;
  const fit=()=>{const node=panel.current;if(!node)return;const navigation=node.closest('.staff-shell')?.querySelector('.bottom-navigation');const top=Math.max(56,node.getBoundingClientRect().top);const remaining=Math.max(280,window.innerHeight-top-(navigation?.getBoundingClientRect().height??64)-16);node.style.setProperty('--map-height',remaining+'px');};
  fit();window.addEventListener('resize',fit);return()=>window.removeEventListener('resize',fit);
 },[mobile,expanded,!!data]);
 useEffect(()=>{if(focus&&drawer.current)drawer.current.open=true;},[focus]);
 return <div ref={panel} className={'batch-map-content'+(mobile?' batch-map-mobile':'')+(expanded?' batch-map-expanded':'')} role={expanded?'dialog':undefined} aria-modal={expanded?true:undefined} aria-labelledby={expanded?headingId:undefined}><h4 id={headingId}>{t('本批次网格巡店地图','このバッチの巡回グリッド地図')}</h4>{error&&<p role="alert" className="error">{error}</p>}{notice&&<UISuccessNotice role="status">{notice}</UISuccessNotice>}{unconfigured&&<p role="status">{t('地图公钥未配置，可用下方门店列表核实定位。','地図の公開キーが未設定です。下の店舗一覧で位置を確認できます。')}</p>}{mapError&&<p role="alert">{t('地图加载失败，可继续使用门店列表。','地図を読込できません。店舗一覧は利用できます。')}</p>}
 <div ref={element} className="batch-map-canvas" aria-label={t('巡店地图画布','巡回地図キャンバス')} hidden={!sdk||unconfigured||mapError}/>
 {sdk&&<div className="batch-map-actions"><UIButton ref={fullscreenButton} type="button" aria-pressed={expanded} onClick={()=>setExpanded(value=>!value)}>{expanded?t('退出全屏','全画面を終了'):t('全屏','全画面')}</UIButton><UIButton type="button" onClick={showAll}>{t('查看全部已加载门店','読込済み全店舗を表示')}</UIButton><UIButton type="button" aria-pressed={following} onClick={locate}>{following?t('停止跟随位置','位置追従を停止'):t('跟随我的位置','現在地を追従')}</UIButton></div>}{gpsError&&<p role="status">{t('无法取得设备位置，请检查定位权限；不会修改门店坐标。','端末の位置を取得できません。権限を確認してください。店舗の座標は変更されません。')}</p>}
 <p role="status">{loading?t('正在读取巡店地图…','巡回地図を読込中…'):t('已加载门店：','読込済み店舗：')+items.length+' / '+(data?.total??0)}</p>
 <details ref={drawer} className={mobile?'batch-map-drawer':'batch-map-information'} open={mobile?undefined:true}>
 <summary>{t('门店列表与定位','店舗一覧・位置確認')} · {items.length} / {data?.total??0}</summary><div className="batch-map-drawer-body">

 <ul className="batch-map-stores">{items.map(point=><li key={point.id}><UIButton type="button" onClick={()=>setFocus(point)}>{point.name} · {point.status==='completed'?t('已完成','完了'):t('待巡店','未実施')}{point.lat===null?t(' · 无有效坐标',' · 有効な座標なし'):''}</UIButton></li>)}</ul>
 {data?.hasMore&&data.nextCursor!==null&&<UIButton type="button" disabled={loading||busy} onClick={()=>setCursor(data.nextCursor!)}>{t('加载更多巡店门店','巡回店舗を追加読込')}</UIButton>}
 {focus&&<article className="batch-location-editor"><h5>{focus.name}</h5><p>{focus.address}</p><UIButton type="button" disabled={busy} onClick={()=>{setExpanded(false);onSelect(focus.id);}}>{t('打开此店巡店填报','この店舗の訪問報告を開く')}</UIButton>
 <UIForm onSubmit={save}><UIFieldset disabled={busy||!data?.canExecute}><label>{t('定位核实状态','位置確認の状態')}<UISelect aria-label={t('定位核实状态','位置確認の状態')} value={accuracy} onChange={event=>setAccuracy(event.target.value)}>{[['pending','待核实','未確認'],['accurate','准确','正確'],['nonexistent','不存在','該当なし'],['doubtful','存疑','要確認']].map(([value,zh,jp])=><option key={value} value={value}>{t(zh,jp)}</option>)}</UISelect></label><label>{t('定位纬度','位置の緯度')}<UIInput type="number" step="any" min={-90} max={90} value={lat} onChange={event=>setLat(event.target.value)}/></label><label>{t('定位经度','位置の経度')}<UIInput type="number" step="any" min={-180} max={180} value={lon} onChange={event=>setLon(event.target.value)}/></label><p>{t('修改坐标后自动标记准确；设备定位不会自动覆盖门店位置。','座標を変更すると正確として保存します。端末の位置で店舗の位置を自動上書きしません。')}</p><UIButton type="submit">{t('保存门店定位','店舗の位置を保存')}</UIButton></UIFieldset></UIForm>{!data?.canExecute&&<p>{t('当前仅可查看，不能修改定位。','現在は参照のみで、位置は変更できません。')}</p>}</article>}
 </div></details>
 </div>;
}
