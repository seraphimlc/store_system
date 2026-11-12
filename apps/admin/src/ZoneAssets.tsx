import {SuccessNotice} from './SuccessNotice';
import{useEffect,useState}from'react';import{Tabs,TabsList,TabsTrigger}from'@appica/ui-react/tabs';import{Alert}from'@appica/ui-react/alert';import type{Session}from'../../../packages/contracts/src';import{GridAvailability}from'./GridAvailability';import{RegionMap}from'./RegionMap';import{RegionDetails}from'./RegionDetails';import{MapDrawer}from'./MapDrawer';
const selected=()=>Number(location.pathname.match(/\/zones\/(\d+)/)?.[1])||null;
export function ZoneAssets({session,language}:{session:Session;language:string}){
 const t=(zh:string,ja:string)=>language==='ja'?ja:zh,[tab,setTab]=useState(()=>new URLSearchParams(location.search).get('tab')==='grids'||new URLSearchParams(location.search).get('mode')==='manage'?'grids':'regions'),[id,setId]=useState<number|null>(selected),[showGrids,setShowGrids]=useState(false),[reload,setReload]=useState(0),[notice,setNotice]=useState('');
 useEffect(()=>{const pop=()=>{setId(selected());const value=new URLSearchParams(location.search).get('tab');if(value)setTab(value==='grids'?'grids':'regions');};window.addEventListener('popstate',pop);return()=>window.removeEventListener('popstate',pop);},[]);
 function close(){setId(null);setShowGrids(false);history.replaceState({},'','/admin/zones?tab='+tab);}
 return <section className="zone-management">{notice&&<SuccessNotice>{notice}</SuccessNotice>}<Tabs value={tab} size="sm" onValueChange={next=>{close();setTab(String(next));history.replaceState({},'','/admin/zones?tab='+next);}}><TabsList aria-label={t('片区管理视图','地域管理ビュー')}><TabsTrigger value="grids">{t('网格管理','グリッド管理')}</TabsTrigger><TabsTrigger value="regions">{t('片区查看','地域表示')}</TabsTrigger></TabsList></Tabs>
 {tab==='grids'?<GridAvailability session={session} language={language}/>:<RegionMap session={session} language={language} refresh={reload} activeId={id} gridRegionId={showGrids?id:null} onRegionChange={close} onDetail={value=>{setId(value);setShowGrids(false);history.replaceState({},'','/admin/zones/'+value+'?tab=regions');}}/>}
 {tab==='regions'&&id!==null&&<MapDrawer title={t('片区详情','地域詳細')} language={language} onClose={close}><RegionDetails id={id} session={session} language={language} showGrids={showGrids} onShowGrids={setShowGrids} onChanged={(message,archived)=>{setNotice(message??'');setReload(value=>value+1);if(archived)close();}}/></MapDrawer>}
 </section>;
}
