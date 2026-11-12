import {useEffect,useRef,useState,type FormEvent} from 'react';
import {Button,Popup} from 'antd-mobile';
import {StationTasks as View,type TaskButtonProps} from '../../../packages/client/src/StationTasks';
import type {Session} from '../../../packages/contracts/src';
import {api} from './api';
import {ScrollMore,useScrollPage} from './scrollList';
import {ChevronLeft as LeftIcon} from 'lucide-react';
import {NavBar} from 'antd-mobile';
import {usePageHeader} from './pageHeader';
import {TodayReport} from './TodayReport';
function TaskButton({children,disabled,onClick,primary}:TaskButtonProps){return <Button color={primary?'primary':'default'} fill={primary?'solid':'outline'} disabled={disabled} onClick={onClick}>{children}</Button>;}
interface TaskPage {items:{id:number;label:string;state:string;pct:number;dispatchDate?:string}[];total:number;counts:Record<string,number>;dispatchSummary:{today:number;carried:number;longest:number}}
export function MyStationTasks({session,language}:{session:Session;language:string}){
 const t=(zh:string,ja:string)=>language==='ja'?ja:zh,manager=['admin','leader'].includes(session.user.role);
 const [tab,setTab]=useState(()=>(['mine','done','unassigned','doing','pending'].includes(new URLSearchParams(location.search).get('tab')||'')?new URLSearchParams(location.search).get('tab')!:'mine')),[query,setQuery]=useState(''),[q,setQ]=useState(''),[refresh,setRefresh]=useState(0),[detail,setDetail]=useState(()=>/\/my\/tasks\/\d+/.test(location.pathname));const generation=useRef(0);
 const list=useScrollPage<TaskPage['items'][number],TaskPage>('/station-tasks?'+new URLSearchParams({tab,q}),item=>item.id,30,refresh);
 useEffect(()=>{const pop=()=>setDetail(/\/my\/tasks\/\d+/.test(location.pathname));window.addEventListener('popstate',pop);return()=>window.removeEventListener('popstate',pop);},[]);
 usePageHeader(detail?t('任务详情','タスク詳細'):'','');
 function close(){setDetail(false);history.pushState({},'','/my/tasks?tab='+tab);window.dispatchEvent(new PopStateEvent('popstate'));setRefresh(v=>v+1);}
 const tabs=manager?[['mine','我的','自分'],['unassigned','待分派','未割当'],['doing','进行中','進行中'],['pending','待确认','確認待ち'],['done','已完成','完了']]:[['mine','我的','自分'],['done','已完成','完了']];
 return <section className="h5-station-page"><div className="h5-segments" role="tablist" aria-label={t('任务分组','タスク分類')}>{tabs.map(([key,zh,ja])=><button role="tab" aria-selected={tab===key} key={key} onClick={()=>{setTab(key);history.replaceState({},'','/my/tasks?tab='+key);}}>{t(zh,ja)} {list.data?.counts?.[key]??0}</button>)}</div>
 {list.error&&<div role="alert" className="error">{list.error}<Button onClick={()=>void list.loadMore()}>{t('重试','再試行')}</Button></div>}
 {tab==='mine'&&list.data?.dispatchSummary&&<div className="h5-card h5-row h5-dispatch"><span>{t('今天派的','本日の割当')} {list.data.dispatchSummary.today}</span><span>{t('延续','繰越')} {list.data.dispatchSummary.carried} · {t('最长','最長')} {list.data.dispatchSummary.longest} {t('天','日')}</span></div>}
 {tab==='mine'&&session.user.personCode&&<TodayReport session={session} language={language} withTasks onSaved={()=>setRefresh(v=>v+1)}/>}
 <form className="h5-search" onSubmit={(e:FormEvent)=>{e.preventDefault();setQ(query.trim());}}><label>{t('任务搜索','タスク検索')}<input value={query} onChange={e=>setQuery(e.target.value)} maxLength={200}/></label><Button type="submit">{t('查询','検索')}</Button></form>
 {list.loading&&!list.items.length?<p role="status">{t('正在读取任务…','タスクを読込中…')}</p>:<><ul className="h5-task-cards">{list.items.map(task=><li key={task.id}><button className="h5-card h5-task-open" onClick={()=>{history.pushState({},'','/my/tasks/'+task.id);setDetail(true);}}><span className="h5-row"><strong>{task.label}</strong><b>{task.pct}%</b></span><span className="h5-row"><span>{t(({done:'已完成',doing:'进行中',unassigned:'待分派'} as Record<string,string>)[task.state]??task.state,({done:'完了',doing:'進行中',unassigned:'未割当'} as Record<string,string>)[task.state]??task.state)}</span><span>{t('详情','詳細')} ›</span></span></button></li>)}</ul>{!list.items.length&&<p className="h5-card">{t('当前分组暂无任务','この分類のタスクはありません')}</p>}</>}
 <ScrollMore hasMore={list.hasMore} loading={list.loading} error={list.error} onLoad={list.loadMore} language={language}/>
 {manager&&<details className="h5-card" onToggle={event=>{if(event.target===event.currentTarget&&!event.currentTarget.open)setRefresh(v=>v+1);}}><summary>{t('任务工具','タスク操作')}</summary><View mobile session={session} language={language} request={api} Button={TaskButton} base="/my/tasks"/></details>}
 <Popup visible={detail} onMaskClick={close} bodyStyle={{borderRadius:'16px 16px 0 0',maxHeight:'88dvh',overflow:'auto'}}><div className="h5-task-detail"><NavBar backIcon={<LeftIcon size={22}/>} onBack={close}>{t('任务详情','タスク詳細')}</NavBar><View key={location.pathname} mobile session={session} language={language} request={api} Button={TaskButton} base="/my/tasks"/></div></Popup>
 </section>;
}
