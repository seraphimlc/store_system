import {useEffect,useRef,useState} from 'react';
import {Button,Popup,Toast} from 'antd-mobile';
import type {Session} from '../../../../packages/contracts/src';
import type {MerchantSearchFeedback,MerchantSearchItem,MerchantSearchPage} from '../../../../packages/contracts/src/store-search';
import {api} from '../api';import {SearchText} from './SearchText';
import './merchant-search.css';

type DisplayItem=MerchantSearchItem&{searchLogId:string|null};
type Page=Omit<MerchantSearchPage,'items'>&{items:DisplayItem[]};
export function MerchantSearchPanel({session,language,query,onQuery}:{session:Session;language:string;query:string;onQuery:(value:string)=>void}){
 const t=(zh:string,ja:string)=>language==='ja'?ja:zh;
 const [mode,setMode]=useState<'grid'|'region'>('grid'),[group,setGroup]=useState<number|null>(null),[page,setPage]=useState(1),[revision,setRevision]=useState(0),[composing,setComposing]=useState(false);
 const [data,setData]=useState<Page|null>(null),[loading,setLoading]=useState(false),[error,setError]=useState(''),[selected,setSelected]=useState<DisplayItem|null>(null),[feedbackError,setFeedbackError]=useState(''),[confirmed,setConfirmed]=useState(false),[confirming,setConfirming]=useState(false);
 const confirmedEvent=useRef<string>(''),previousQuery=useRef(query),sourceVersion=useRef<string|null>(null);
 useEffect(()=>{if(previousQuery.current!==query){previousQuery.current=query;setPage(1);setGroup(null);setSelected(null);}},[query]);
 useEffect(()=>{
  let current=true;if(!query.trim()){setData(null);setError('');setLoading(false);return;}if(composing){setLoading(true);return;}
  setLoading(true);setError('');const timer=setTimeout(()=>{
   const search=new URLSearchParams({q:query,page:String(page),...(group===null?{}:mode==='grid'?{groupGrid:String(group)}:{groupRegion:String(group)})});
   void api<MerchantSearchPage>('/batch-work/merchants/search?'+search,session).then(result=>{if(!current)return;if(page>1&&sourceVersion.current!==null&&sourceVersion.current!==result.sourceVersion){sourceVersion.current=result.sourceVersion;setData(null);setPage(1);return;}sourceVersion.current=result.sourceVersion;const value={...result,items:result.items.map(item=>({...item,searchLogId:result.searchLogId}))};setData(previous=>page===1?value:previous?.sourceVersion===value.sourceVersion?{...value,items:[...previous.items,...value.items.filter(item=>!previous.items.some(old=>old.merchantId===item.merchantId))]}:value);}).catch(e=>{if(current)setError(e instanceof Error?e.message:t('搜索失败，请重试','検索に失敗しました。再試行してください'));}).finally(()=>{if(current)setLoading(false);});
  },180);return()=>{current=false;clearTimeout(timer);};
 },[query,page,group,mode,revision,composing,session]);
 function choose(item:DisplayItem){setSelected(item);setConfirmed(false);setFeedbackError('');confirmedEvent.current=crypto.randomUUID();if(item.searchLogId){const event:MerchantSearchFeedback={eventId:crypto.randomUUID(),searchLogId:item.searchLogId,merchantId:item.merchantId,kind:'opened'};void api('/batch-work/merchants/search-feedback',session,event).catch(()=>{});}}
 async function confirm(){if(!selected?.searchLogId||confirmed||confirming)return;setConfirming(true);setFeedbackError('');try{const event:MerchantSearchFeedback={eventId:confirmedEvent.current,searchLogId:selected.searchLogId,merchantId:selected.merchantId,kind:'confirmed'};await api('/batch-work/merchants/search-feedback',session,event);setConfirmed(true);Toast.show(t('已记录找到反馈','見つかったことを記録しました'));}catch(e){setFeedbackError(e instanceof Error?e.message:t('反馈失败，请重试','記録に失敗しました。再試行してください'));}finally{setConfirming(false);}}
 const groups=data?(mode==='grid'?data.gridGroups:data.regionGroups):[];
 return <section className="merchant-search" aria-label={t('全量商户搜索','全店舗検索')}>
  <label className="merchant-search-input">{t('搜索全部商户','すべての店舗を検索')}<input aria-label={t('全量商户关键词','全店舗検索キーワード')} type="search" autoFocus enterKeyHint="search" value={query} placeholder={t('名称、地址或编号','店舗名・住所・番号')} onCompositionStart={()=>setComposing(true)} onCompositionEnd={event=>{onQuery(event.currentTarget.value);setComposing(false);}} onChange={event=>onQuery(event.target.value)} onKeyDown={event=>{if(event.key==='Enter'&&!event.nativeEvent.isComposing){event.preventDefault();setPage(1);setRevision(n=>n+1);}}}/></label>
  <p className="work-help">{t('搜索全量商户基础资料；分类不授予其他团队的作业权限。','全店舗の基本情報を検索します。分類によって他チームの作業権限は付与されません。')}</p>
  {query.trim()&&<>
   <section className="work-search-groups" aria-label={t('搜索结果分类','検索結果の分類')}>
    <div role="group" aria-label={t('分类方式','分類方法')}>{(['grid','region'] as const).map(value=><button key={value} type="button" aria-pressed={mode===value} onClick={()=>{setMode(value);setGroup(null);setPage(1);}}>{value==='grid'?t('网格','グリッド'):t('片区','エリア')}</button>)}</div>
    {data&&<label className="merchant-search-input">{mode==='grid'?t('选择网格','グリッドを選択'):t('选择片区','エリアを選択')}<select aria-label={t('结果分类筛选','検索結果の絞り込み')} value={group===null?'all':String(group)} disabled={loading} onChange={event=>{setGroup(event.target.value==='all'?null:Number(event.target.value));setPage(1);}}><option value="all">{t('全部','すべて')} {data.matchedTotal}</option>{groups.map(value=><option key={value.id} value={value.id}>{value.name||'#'+value.id} {value.count}</option>)}{data.unassignedTotal>0&&<option value="0">{t('未归属','未分類')} {data.unassignedTotal}</option>}</select></label>}
   </section>
   <div role="status" aria-live="polite">{composing?t('输入完成后搜索','入力確定後に検索します'):loading?t('正在搜索…','検索中…'):error?'':t('找到','一致')+' '+(data?.total??0)+t(' 家商户',' 店舗')}</div>
   {error&&<p role="alert">{error} <Button size="small" onClick={()=>setRevision(n=>n+1)}>{t('重试','再試行')}</Button></p>}
   {!loading&&!error&&data?.searchMode==='related'&&<p className="work-search-related-notice">{data.nameMatchMethod?t('以下为名称相近候选，请核对名称和地址。','似た店名の候補です。店舗名と住所を確認してください。'):t('以下仅为部分词相关候选，请核对名称和地址。','一部の語に関連する候補です。店舗名と住所を確認してください。')}</p>}
   {!loading&&!error&&data?.total===0&&<p>{t('未找到相关商户，可尝试名称中的其他词或地址。','一致する店舗はありません。店名の別の語や住所でお試しください。')}</p>}
   <ul className="work-store-list merchant-search-results" aria-busy={loading}>{data?.items.map(item=><li key={item.merchantId}><button type="button" disabled={loading||!!error||composing} onClick={()=>choose(item)}><strong><SearchText text={item.name||item.merchantId} query={query} terms={item.searchMatch.nameTerms}/></strong><span><SearchText text={item.address||t('暂无地址','住所なし')} query={query} terms={item.searchMatch.addressTerms}/></span></button></li>)}</ul>
   {data&&data.items.length<data.total&&!error&&<Button block loading={loading} disabled={loading} onClick={()=>setPage(n=>n+1)}>{t('加载更多','さらに表示')}</Button>}
  </>}
  <Popup visible={!!selected} onMaskClick={()=>{if(!confirming)setSelected(null);}} bodyClassName="work-store-popup" bodyStyle={{borderRadius:'16px 16px 0 0',overflow:'visible'}}>
   {selected?.searchLogId&&<section className="work-search-confirmation" aria-label={t('搜索结果反馈','検索結果のフィードバック')}><span className="work-search-feedback-label">{t('搜索反馈','検索フィードバック')}</span><p className="work-search-feedback-description">{t('仅反馈搜索结果，不会提交巡店记录。','検索結果だけを記録します。巡回実績は送信されません。')}</p><Button size="small" disabled={confirmed||confirming} onClick={()=>void confirm()}>{confirmed?t('已反馈找到','記録済み'):confirming?t('正在记录…','記録中…'):t('找到了','見つかりました')}</Button>{feedbackError&&<p role="alert">{feedbackError}</p>}</section>}
   {selected&&<section className="work-drawer merchant-search-detail" aria-label={t('商户资料','店舗情報')}><h3><SearchText text={selected.name||selected.merchantId} query={query} terms={selected.searchMatch.nameTerms}/></h3><p><SearchText text={selected.address||t('暂无地址','住所なし')} query={query} terms={selected.searchMatch.addressTerms}/></p><p>{t('坐标','座標')}：{selected.lat===null||selected.lon===null?t('未知','不明'):selected.lat+', '+selected.lon}</p><p className="work-help">{t('此处仅展示商户基础资料。巡店请从你有权限的任务进入。','基本情報のみ表示しています。巡回は権限のある作業から開いてください。')}</p><Button block disabled={confirming} onClick={()=>setSelected(null)}>{t('关闭','閉じる')}</Button></section>}
  </Popup>
 </section>;
}
