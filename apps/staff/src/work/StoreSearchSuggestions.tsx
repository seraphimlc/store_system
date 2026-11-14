import {useEffect,useRef,useState} from 'react';import {Button} from 'antd-mobile';
import type {Session} from '../../../../packages/contracts/src';
import type {StoreSuggestionPage} from '../../../../packages/contracts/src/store-search';
import {api} from '../api';
export function StoreSearchSuggestions({gridId,q,state,businessStatus,session,language,onChoose,onRefresh}:{gridId:number;q:string;state:string;businessStatus:string;session:Session;language:string;onChoose:(name:string,page:number,receipt?:string)=>void;onRefresh:()=>void}){
 const t=(zh:string,ja:string)=>language==='ja'?ja:zh;
 const [data,setData]=useState<StoreSuggestionPage|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const generation=useRef(0),pending=useRef(false);useEffect(()=>()=>{generation.current++;},[]);
 async function load(page=1){if(pending.current)return;const g=++generation.current;pending.current=true;setBusy(true);setError('');
  try{const result=await api<StoreSuggestionPage>('/batch-work/grids/'+gridId+'/suggestions?'+new URLSearchParams({q,state,businessStatus,page:String(page)}),session);if(g===generation.current)setData(result);}
  catch(e){if(g===generation.current)setError(e instanceof Error?e.message:t('相似名称暂不可用','候補を取得できません'));}
  finally{if(g===generation.current){setBusy(false);pending.current=false;}}
 }
 return <section className="work-search-suggestions" aria-label={t('相似名称建议','似た店舗名の候補')}>
  <p>{t('相似名称不是精确结果，请核对地址后再选择门店。','候補は完全一致ではありません。住所を確認してから店舗を選んでください。')}</p>
  {!data&&!error&&<Button size="small" disabled={busy} onClick={()=>void load()}>{t('查找相似名称','似た店舗名を探す')}</Button>}
  {busy&&<p role="status">{t('正在查找相似名称…','候補を検索中…')}</p>}
  {error&&<div><p role="alert">{error}</p><Button size="small" disabled={busy} onClick={()=>void load(data?.page??1)}>{t('重试相似名称','候補を再取得')}</Button></div>}
  {!busy&&data?.status==='unsupported_query'&&<p role="status">{t('暂不为短词、编号或多词查询提供相似名称。','短い語・番号・複数語の検索には候補を表示しません。')}</p>}
  {!busy&&data?.status==='scope_too_large'&&<p role="status">{t('当前网格超出建议范围，请继续使用普通搜索或状态筛选。','候補検索の対象が多すぎます。通常検索か状態の絞り込みをご利用ください。')}</p>}
  {!busy&&data?.status==='literal_results'&&<div><p role="status">{t('当前已有普通匹配，请重新搜索查看。','通常検索で一致する店舗があります。再検索してください。')}</p><Button size="small" onClick={onRefresh}>{t('重新搜索','再検索')}</Button></div>}
  {data?.status==='ready'&&!error&&<><p role="status">{data.total?t('相似名称','候補名')+' '+data.total+t(' 个，候选门店 ',' 件・候補店舗 ')+data.candidateTotal+t(' 家',' 店'):t('当前筛选下没有相似名称','条件に一致する候補はありません')}</p>
   <ul className="work-suggestion-list">{data.items.map(item=><li key={item.name}><button disabled={busy} onClick={()=>onChoose(item.name,data.page,data.receipt)}><span>{item.name}</span><small>{item.candidateCount+t(' 家同名候选',' 店の同名候補')}</small></button></li>)}</ul>
   {(data.page>1||data.hasMore)&&<div className="work-suggestion-pages"><Button size="small" disabled={busy||data.page<=1} onClick={()=>void load(data.page-1)}>{t('上一页建议','前の候補')}</Button><span>{t('第','ページ ')} {data.page} {t('页','')}</span><Button size="small" disabled={busy||!data.hasMore} onClick={()=>void load(data.page+1)}>{t('下一页建议','次の候補')}</Button></div>}
  </>}
 </section>;
}
