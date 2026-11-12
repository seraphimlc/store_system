import {useEffect,useRef,useState} from 'react';
import {Button} from 'antd-mobile';
import type {Session} from '../../../../packages/contracts/src';
import {api} from '../api';
import {ScrollMore} from '../scrollList';
interface Proposal {id:number;revision:number;state:string;reason:string;name:string;address:string;lat:number;lon:number;submittedBy:number;submitterName:string;regionId:number;gridId:number;canReview:boolean}
interface Option {id:number;name:string}
interface Page {items:Proposal[];total:number;pageSize:number;canReview:boolean;canApprove:boolean;regions:Option[];grids:(Option&{regionId:number})[];submitters:Option[]}
export function BatchProposals({id,session,language}:{id:number;session:Session;language:string}){
 const t=(zh:string,ja:string)=>language==='ja'?ja:zh;
 const [filters,setFilters]=useState({state:'pending',region:'',grid:'',submitter:''}),[page,setPage]=useState(1),[data,setData]=useState<Page|null>(null),[selected,setSelected]=useState<number[]>([]),[reason,setReason]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState(''),[loading,setLoading]=useState(false),[busy,setBusy]=useState(false),[refresh,setRefresh]=useState(0);
 const writing=useRef(false);
 useEffect(()=>{let live=true;setLoading(true);setError('');const query=new URLSearchParams({page:String(page),state:filters.state});for(const key of ['region','grid','submitter'] as const)if(filters[key])query.set(key,filters[key]);
 api<Page>(`/batch-work/batches/${id}/proposals?${query}`,session).then(result=>{if(live)setData(previous=>page>1&&previous?{...result,items:[...previous.items,...result.items.filter(row=>!previous.items.some(p=>p.id===row.id))]}:result);}).catch(e=>{if(live)setError(e.message);}).finally(()=>{if(live)setLoading(false);});return()=>{live=false;};},[id,session,filters,page,refresh]);
 function filter(key:keyof typeof filters,value:string){setFilters(previous=>({...previous,[key]:value,...(key==='region'?{grid:''}:{})}));setPage(1);setData(previous=>previous?{...previous,items:[],total:0}:null);setSelected([]);setNotice('');}
 const pending=data?.items.filter(row=>row.state==='pending'&&row.canReview)??[];
 async function review(action:'approve'|'reject'){
  if(writing.current)return;const selections=pending.filter(row=>selected.includes(row.id)).map(({id,revision})=>({id,revision}));if(!selections.length)return;
  writing.current=true;setBusy(true);setError('');setNotice('');
  try{await api(`/batch-work/batches/${id}/proposal-review`,session,{action,reason,selections});setPage(1);setSelected([]);setReason('');setData(previous=>previous?{...previous,items:[],total:0}:null);setRefresh(n=>n+1);setNotice(t('核实结果已保存','確認結果を保存しました'));}catch(e){setError((e as Error).message);}finally{writing.current=false;setBusy(false);}
 }
 const status=(state:string)=>({pending:t('待核实','確認待ち'),approved:t('已通过','承認済み'),rejected:t('已驳回','却下済み')}[state]||state);
 return <div className="batch-proposals">
 <p className="work-help">{t('待核实新店不计入任务，通过后加入当前批次，初始为未巡店。','確認待ちの新規店舗は対象外です。承認後に現在のバッチへ未巡回として追加されます。')}</p>
 <fieldset className="batch-proposal-filters" disabled={busy} style={{border:0,padding:0}}>
 <select aria-label={t('核实状态','確認状態')} value={filters.state} onChange={e=>filter('state',e.target.value)}><option value="">{t('全部状态','すべての状態')}</option>{['pending','approved','rejected'].map(s=><option key={s} value={s}>{status(s)}</option>)}</select>
 <select aria-label={t('片区','地域')} value={filters.region} onChange={e=>filter('region',e.target.value)}><option value="">{t('全部片区','すべての地域')}</option>{data?.regions.map(r=><option key={r.id} value={r.id}>{r.name}</option>)}</select>
 <select aria-label={t('网格','グリッド')} value={filters.grid} onChange={e=>filter('grid',e.target.value)}><option value="">{t('全部网格','すべてのグリッド')}</option>{data?.grids.filter(g=>!filters.region||g.regionId===Number(filters.region)).map(g=><option key={g.id} value={g.id}>{g.name}</option>)}</select>
 <select aria-label={t('提交人','提出者')} value={filters.submitter} onChange={e=>filter('submitter',e.target.value)}><option value="">{t('全部提交人','すべての提出者')}</option>{data?.submitters.map(u=><option key={u.id} value={u.id}>{u.name}</option>)}</select>
 </fieldset>
 {error&&<p role="alert" className="error">{error}</p>}{notice&&<p role="status">{notice}</p>}
 {data?.canReview&&pending.length>0&&<div className="batch-proposal-review"><label className="batch-proposal-select"><input type="checkbox" disabled={busy} checked={pending.length>0&&pending.every(row=>selected.includes(row.id))} onChange={e=>setSelected(e.target.checked?pending.slice(0,200).map(row=>row.id):[])}/>{t('选择当前列表','表示中の一覧を選択')}</label><input aria-label={t('驳回原因','却下理由')} placeholder={t('驳回原因（驳回时必填）','却下理由（却下時は必須）')} value={reason} maxLength={500} disabled={busy} onChange={e=>setReason(e.target.value)}/><div className="work-actions"><Button color="primary" disabled={busy||loading||!selected.length||!data.canApprove} onClick={()=>void review('approve')}>{t('批量通过','一括承認')}</Button><Button disabled={busy||loading||!selected.length||!reason.trim()} onClick={()=>void review('reject')}>{t('批量驳回','一括却下')}</Button></div></div>}
 <ul className="batch-proposal-list">{data?.items.map(row=><li key={row.id}><div className="work-row"><strong>{row.name}</strong><span>{status(row.state)}</span></div><p>{row.address}</p><p>{t('提交人','提出者')}：{row.submitterName} · {data.grids.find(g=>g.id===row.gridId)?.name||'#'+row.gridId}</p><div className="work-row"><a href={`/api/batch-work/proposals/${row.id}/photo`} target="_blank" rel="noreferrer">{t('门店照片','店舗写真')}</a><a href={`https://www.google.com/maps/search/?api=1&query=${row.lat},${row.lon}`} target="_blank" rel="noreferrer">{t('查看定位','位置を表示')}</a></div>{row.reason&&<p>{t('驳回原因','却下理由')}：{row.reason}</p>}{row.canReview&&row.state==='pending'&&<label className="batch-proposal-select"><input type="checkbox" aria-label={t('选择核实','確認対象に選択')+' '+row.name} disabled={busy||(!selected.includes(row.id)&&selected.length>=200)} checked={selected.includes(row.id)} onChange={e=>setSelected(previous=>e.target.checked?[...previous,row.id]:previous.filter(n=>n!==row.id))}/>{t('选择核实','確認対象に選択')}</label>}</li>)}</ul>
 {!loading&&!error&&data?.total===0&&<p>{t('暂无新店申请','新規店舗の申請はありません')}</p>}
 <ScrollMore hasMore={!!data&&data.items.length<data.total} loading={loading||busy} error={error} language={language} onLoad={()=>{if(loading||busy)return;if(error)setRefresh(n=>n+1);else setPage(n=>n+1);}}/>
 </div>;
}
