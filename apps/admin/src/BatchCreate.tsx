import {RegionMap} from './RegionMap';
import {Alert} from '@appica/ui-react/alert';
import {AdminCheckbox} from './AdminCheckbox';
import {Form} from '@appica/ui-react/form';
import {Table,TableHeader,TableRow,TableHead,TableBody,TableCell} from '@appica/ui-react/table';
import {MapDrawer} from './MapDrawer';
import {Input} from '@appica/ui-react/input';
import {useEffect,useState,useRef,type FormEvent} from 'react';
import {Button} from '@appica/ui-react/button';
import type {Session} from '../../../packages/contracts/src';
import {api} from './api';
interface Region{id:number;name:string;releaseId:number}
interface RegionPage{total:number;items:Region[]}
export function BatchCreate({session,language,onCreated}:{session:Session;language:string;onCreated:(id:number)=>void}){
 const t=(zh:string,ja:string)=>language==='ja'?ja:zh;
 const [open,setOpen]=useState(false),[title,setTitle]=useState(''),[query,setQuery]=useState(''),[filter,setFilter]=useState(''),[page,setPage]=useState(1),[data,setData]=useState<RegionPage|null>(null),[selection,setSelection]=useState<Record<number,string>>({}),[saving,setSaving]=useState(false),[error,setError]=useState(''),[loading,setLoading]=useState(false);const writing=useRef(false);
 useEffect(()=>{if(!open)return;let live=true;setLoading(true);setError('');setData(null);void api<RegionPage>('/batch-work/creation-regions?'+new URLSearchParams({q:filter,page:String(page)})).then(value=>{if(live)setData(value);}).catch(cause=>{if(live)setError(cause.message);}).finally(()=>{if(live)setLoading(false);});return()=>{live=false;};},[open,filter,page]);
 const ids=Object.keys(selection).map(Number);
 async function save(event:FormEvent<HTMLFormElement>){event.preventDefault();if(writing.current||!title.trim()||!ids.length||ids.length>2000)return;writing.current=true;setSaving(true);setError('');try{const batch=await api<{id:number}>('/batch-work/batches',session,{title,regionIds:ids});setTitle('');setSelection({});setOpen(false);onCreated(batch.id);}catch(cause){setError((cause as Error).message);}finally{writing.current=false;setSaving(false);}}
 return <><div className="admin-actions"><Button onClick={()=>setOpen(true)}>{t('新建巡店批次','巡回バッチを作成')}</Button></div>{open&&<MapDrawer title={t('新建巡店批次','巡回バッチを作成')} language={language} onClose={()=>setOpen(false)}><div className="batch-create-map">
  <RegionMap session={session} language={language} selectedIds={ids} onPick={(id,name)=>setSelection(previous=>{const next={...previous};if(Object.hasOwn(next,id))delete next[id];else next[id]=name;return next;})} onDetail={()=>{}}/>
  </div><div className="batch-create-panel"><details className="batch-region-picker-list"><summary>{t('按列表选择片区','一覧から地域を選択')}</summary>
  {error&&<Alert role="alert" className="error">{error}</Alert>}
  <Form className="batch-search" onSubmit={event=>{event.preventDefault();setFilter(query.trim());setPage(1);}}><label>{t('查找来源片区','元の地域を検索')}<Input maxLength={128} value={query} onChange={event=>setQuery(event.target.value)} disabled={saving}/></label><Button type="submit" disabled={saving}>{t('查找片区','地域を検索')}</Button></Form>
  {loading?<p role="status">{t('正在读取片区…','地域を読み込み中…')}</p>:data?.items.length?<div className="table-scroll"><Table><TableHeader><TableRow><TableHead>{t('选择','選択')}</TableHead><TableHead>{t('来源片区','元の地域')}</TableHead><TableHead>{t('来源版本','元のバージョン')}</TableHead></TableRow></TableHeader><TableBody>{data.items.map(row=><TableRow key={row.id}><TableCell><AdminCheckbox  aria-label={`${t('选择来源片区','元の地域を選択')} ${row.name} #${row.id}`} checked={Object.hasOwn(selection,row.id)} disabled={saving} onChange={event=>{const checked=event.target.checked;setSelection(previous=>{const next={...previous};if(checked)next[row.id]=row.name;else delete next[row.id];return next;});}}/></TableCell><TableCell>{row.name} · #{row.id}</TableCell><TableCell>#{row.releaseId}</TableCell></TableRow>)}</TableBody></Table></div>:data&&<p>{t('没有匹配片区','該当する地域はありません')}</p>}
  <div className="batch-actions"><Button type="button" variant="outline" disabled={saving||loading||page===1} onClick={()=>setPage(page-1)}>{t('片区上一页','地域の前ページ')}</Button><span>{page} / {Math.max(1,Math.ceil((data?.total??0)/30))}</span><Button type="button" variant="outline" disabled={saving||loading||page*30>=(data?.total??0)} onClick={()=>setPage(page+1)}>{t('片区下一页','地域の次ページ')}</Button></div>
  </details>
  <Form onSubmit={save}><p role="status">{t('已选片区：','選択済みの地域：')}{ids.length}</p>{ids.length>0&&<details><summary>{t('查看已选片区','選択済みの地域を表示')}</summary><ul>{ids.map(id=><li key={id}>{selection[id]} · #{id}</li>)}</ul><Button type="button" variant="outline" disabled={saving} onClick={()=>setSelection({})}>{t('清空选择','選択を解除')}</Button></details>}<label>{t('批次名称','バッチ名')}<Input required maxLength={160} value={title} onChange={event=>setTitle(event.target.value)} disabled={saving}/></label><Button type="submit" disabled={saving||!title.trim()||!ids.length||ids.length>2000}>{saving?t('正在创建批次…','作成中…'):t('创建巡店批次','巡回バッチを作成する')}</Button></Form>
 </div></MapDrawer>}</>;
}
