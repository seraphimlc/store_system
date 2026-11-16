import {useEffect,useLayoutEffect,useRef,useState,type ReactNode} from 'react';
import {Tabs,TabsList,TabsTrigger} from '@appica/ui-react/tabs';
import {Drawer,DrawerContent,DrawerHeader,DrawerTitle,DrawerBody,DrawerFooter} from '@appica/ui-react/drawer';
import {AdminCheckbox} from './AdminCheckbox';
import {Button} from '@appica/ui-react/button';
import type {Session} from '../../../packages/contracts/src';
import {cachedGridApi} from '../../../packages/client/src/grid-geometry-cache';
import {googleMaps,onGoogleMapsAuthFailure} from '../../../packages/client/src/google-maps';
import './batch-assignment-map.css';

export interface AssignmentMapProps {
 batchId:number;
 regions:{id:number;teamId:number|null;teamName:string|null;snapshot:{name?:string}}[];
 selectedIds:number[];
 disabled:boolean;
 onChange:(ids:number[])=>void;
 filter:ReactNode;
 actions:ReactNode;
 list:ReactNode;
}
type Feature={type:'Feature';id:number;geometry:unknown;properties:{regionId:number;name?:string}};
export function BatchAssignmentMap({batchId,regions,selectedIds,disabled,onChange,filter,actions,list,session,language}:AssignmentMapProps&{session:Session;language:string}) {
 const t=(zh:string,ja:string)=>language==='ja'?ja:zh;
 const [view,setView]=useState('map'),[drawerOpen,setDrawerOpen]=useState(()=>window.innerWidth>=768),[frame,setFrame]=useState({top:0,left:0,width:0,height:320});
 const workspace=useRef<HTMLDivElement>(null);
 useLayoutEffect(()=>{
  if(view!=='map')return;
  const fit=()=>{const node=workspace.current;if(!node)return;const rect=node.getBoundingClientRect();const next={top:rect.top,left:rect.left,width:rect.width,height:Math.max(240,window.innerHeight-(rect.top>=window.innerHeight?0:Math.max(0,rect.top))-24)};setFrame(previous=>Object.keys(next).every(key=>previous[key as keyof typeof next]===next[key as keyof typeof next])?previous:next);};
  fit();const observer=new ResizeObserver(fit);if(workspace.current)observer.observe(workspace.current);window.addEventListener('resize',fit);window.addEventListener('scroll',fit,true);return()=>{observer.disconnect();window.removeEventListener('resize',fit);window.removeEventListener('scroll',fit,true);};
 },[view]);
 const element=useRef<HTMLDivElement>(null),map=useRef<google.maps.Map|null>(null),layer=useRef<google.maps.Data|null>(null);
 const current=useRef({regions,selectedIds,disabled,onChange});current.current={regions,selectedIds,disabled,onChange};
 const [features,setFeatures]=useState<Feature[]>([]),[sdk,setSdk]=useState<typeof google.maps>(),[loading,setLoading]=useState(true),[error,setError]=useState(''),[unavailable,setUnavailable]=useState(false),[attempt,setAttempt]=useState(0);
 useEffect(()=>{
  let live=true;setLoading(true);setError('');setUnavailable(false);setSdk(undefined);setFeatures([]);
  const unsubscribe=onGoogleMapsAuthFailure(()=>{if(live){setUnavailable(true);setError(t('地图认证失败，请使用片区列表或重新加载。','地図の認証に失敗しました。地域一覧をご利用ください。'));}});
  void cachedGridApi<{features:Feature[];enabled:boolean;key:string|null}>(`/batch-work/batches/${batchId}/map`,session).then(async result=>{
   if(!live)return;setFeatures(result.features);
   if(!result.enabled||!result.key){setUnavailable(true);return;}
   const maps=await googleMaps(result.key,language);if(live)setSdk(maps);
  }).catch(cause=>{if(live){setUnavailable(true);setError((cause as Error).message);}}).finally(()=>{if(live)setLoading(false);});
  return()=>{live=false;unsubscribe();};
 },[batchId,session,language,attempt]);
 useEffect(()=>{
  if(!sdk||!element.current||unavailable)return;
  const view=new sdk.Map(element.current,{center:{lat:35.68,lng:139.76},zoom:11,mapTypeControl:false,streetViewControl:false,fullscreenControl:true});
  const data=new sdk.Data({map:view});map.current=view;layer.current=data;
  const listener=data.addListener('click',(event:google.maps.Data.MouseEvent)=>{
   const value=current.current,id=Number(event.feature.getProperty('regionId'));
   if(value.disabled||!value.regions.some(region=>region.id===id))return;
   setDrawerOpen(true);value.onChange(value.selectedIds.includes(id)?value.selectedIds.filter(item=>item!==id):[...value.selectedIds,id]);
  });
  return()=>{listener.remove();data.setMap(null);map.current=null;layer.current=null;};
 },[sdk,unavailable]);
 useEffect(()=>{
  const data=layer.current,view=map.current;if(!data||!view||!sdk)return;
  data.forEach(feature=>data.remove(feature));data.addGeoJson({type:'FeatureCollection',features});
  const bounds=new sdk.LatLngBounds();data.forEach(feature=>feature.getGeometry()?.forEachLatLng(point=>bounds.extend(point)));
  if(!bounds.isEmpty())view.fitBounds(bounds,{top:100,bottom:32,left:24,right:window.innerWidth>=768?364:24});
 },[sdk,features,unavailable]);
 useEffect(()=>{
  const byId=new Map(regions.map(region=>[region.id,region])),selectedSet=new Set(selectedIds);
  layer.current?.setStyle(feature=>{
   const region=byId.get(Number(feature.getProperty('regionId'))),selected=!!region&&selectedSet.has(region.id);
   return {visible:!!region,clickable:!disabled,strokeColor:selected?'#1d4ed8':'#64748b',strokeWeight:selected?3:1,fillColor:selected?'#2563eb':region?.teamId!=null?'#059669':'#94a3b8',fillOpacity:selected?.55:.25};
  });
 },[regions,selectedIds,disabled,sdk,features,unavailable]);
 useEffect(()=>{if(view==='map'&&sdk&&map.current)sdk.event.trigger(map.current,'resize');},[view,sdk]);
 const selectedRegions=regions.filter(region=>selectedIds.includes(region.id));
 const mappedIds=new Set(features.map(feature=>feature.properties.regionId)),missing=regions.filter(region=>!mappedIds.has(region.id));
 return <section className="batch-assignment-map" aria-label={t('批次片区地图分配','バッチ地域の地図割当')}>
  <Tabs value={view} size="sm" onValueChange={next=>setView(String(next))}><TabsList aria-label={t('批次分配视图','バッチ割当ビュー')}><TabsTrigger value="map">{t('地图','地図')}</TabsTrigger><TabsTrigger value="list">{t('列表','一覧')}</TabsTrigger></TabsList></Tabs>
  <div ref={workspace} className="batch-assignment-workspace" hidden={view!=='map'} style={{height:frame.height}}>
   <div ref={element} className="batch-assignment-canvas" hidden={!sdk||unavailable} aria-label={t('批次片区选择地图','バッチ地域選択地図')}/>
   <div className="batch-assignment-floating" data-panel-open={drawerOpen||undefined}>
    {filter}<div className="batch-assignment-legend"><span><i className="unassigned"/>{t('未分配','未割当')}</span><span><i className="assigned"/>{t('已分配','割当済み')}</span><span><i className="selected"/>{t('已选中','選択中')}</span></div>
   </div>
   {!drawerOpen&&<Button className="batch-assignment-open" variant="outline" onClick={()=>setDrawerOpen(true)}>{t('已选片区','選択地域')} ({selectedRegions.length})</Button>}
   <div className="batch-assignment-map-state" data-panel-open={drawerOpen||undefined}>
    {loading&&<p role="status">{t('正在读取批次边界…','バッチ境界を読込中…')}</p>}
    {error&&<p role="alert" className="error">{error}</p>}
    {unavailable&&<p role="status">{t('地图不可用，可切换列表视图继续选择和分配。','地図を利用できません。一覧から選択・割当できます。')} <Button variant="outline" onClick={()=>setAttempt(value=>value+1)}>{t('重新加载地图','地図を再読込')}</Button></p>}
    {!loading&&!unavailable&&missing.length>0&&<p role="status">{t(`${missing.length} 个片区缺少批次边界，可切换列表选择。`,`${missing.length} 地域にバッチ境界がありません。一覧から選択してください。`)}</p>}
   </div>
  </div>
  {view==='list'&&<section className="batch-assignment-list"><div className="batch-assignment-list-tools">{filter}{actions}</div>{list}</section>}
  {view==='map'&&<Drawer side="right" open={drawerOpen} modal={false} disablePointerDismissal onOpenChange={setDrawerOpen}><DrawerContent frame={false} backdrop={false} className="batch-assignment-drawer" closeLabel={t('关闭已选片区','選択地域を閉じる')} viewportProps={{className:'batch-assignment-drawer-viewport',style:{top:frame.top,left:frame.left,width:frame.width,height:frame.height}}}>
   <DrawerHeader><DrawerTitle>{t('已选片区','選択地域')} · {selectedRegions.length}</DrawerTitle></DrawerHeader>
   <DrawerBody>
    <ul className="batch-assignment-region-list" aria-label={t('已选片区列表','選択地域一覧')}>{selectedRegions.map(region=><li key={region.id} data-selected={selectedIds.includes(region.id)||undefined}><label><AdminCheckbox aria-label={t('地图选择片区 ','地図の地域を選択 ')+(region.snapshot.name||region.id)} checked={selectedIds.includes(region.id)} disabled={disabled} onChange={event=>onChange(event.target.checked?[...new Set([...selectedIds,region.id])]:selectedIds.filter(id=>id!==region.id))}/><span><strong>{region.snapshot.name||'#'+region.id}</strong><small>{region.teamName||t('未分配','未割当')}</small></span></label></li>)}</ul>
    {!selectedRegions.length&&<p className="batch-assignment-empty">{t('尚未选择片区','地域が選択されていません')}</p>}
   </DrawerBody>
   <DrawerFooter className="batch-assignment-drawer-footer">{actions}</DrawerFooter>
  </DrawerContent></Drawer>}
 </section>;
}
