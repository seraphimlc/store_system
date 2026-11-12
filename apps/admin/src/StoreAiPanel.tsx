import {AdminCheckbox} from './AdminCheckbox';
import {Alert} from '@appica/ui-react/alert';
import {Table,TableHeader,TableRow,TableHead,TableBody,TableCell} from '@appica/ui-react/table';
import {useEffect,useRef,useState} from 'react';
import {Button} from '@appica/ui-react/button';
import type {Session} from '../../../packages/contracts/src';
import {api} from './api';

interface Run {
 id:number;status:string;total:number;startedAt:string;finishedAt:string|null;
 summary:{applied?:Record<string,number>;left_human?:number;errors?:number;changed?:number;processed?:number;error?:string};
}
export function StoreAiPanel({session,language,onChanged}:{session:Session;language:string;onChanged:()=>void|Promise<void>}){
 const t=(zh:string,ja:string)=>language==='ja'?ja:zh;
 const [runs,setRuns]=useState<Run[]>([]),[loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[confirmed,setConfirmed]=useState(false),[error,setError]=useState('');
 const changed=useRef(onChanged),mounted=useRef(false),writing=useRef(false),active=useRef(new Set<number>());
 changed.current=onChanged;
 async function refresh(){
  try{
   const result=await api<{items:Run[]}>('/stores/ai');if(!mounted.current)return;
   const next=new Set(result.items.filter(run=>run.status==='running').map(run=>run.id));
   const completed=[...active.current].some(id=>!next.has(id));active.current=next;setRuns(result.items);setError('');
   if(completed)void changed.current();
  }catch(cause){if(mounted.current)setError((cause as Error).message);}
  finally{if(mounted.current)setLoading(false);}
 }
 useEffect(()=>{
  mounted.current=true;void refresh();
  const timer=setInterval(()=>{if(active.current.size&&document.visibilityState==='visible')void refresh();},3000);
  return()=>{mounted.current=false;clearInterval(timer);};
 },[]);
 async function start(){
  if(writing.current||!confirmed)return;writing.current=true;setBusy(true);setError('');
  try{
   const result=await api<{id:number}>('/stores/ai',session,{confirm:'RUN_STORE_AI'});
   if(!mounted.current)return;active.current.add(result.id);setConfirmed(false);await refresh();
  }catch(cause){if(mounted.current)setError((cause as Error).message);}
  finally{writing.current=false;if(mounted.current)setBusy(false);}
 }
 return <section className="store-logs" aria-labelledby="store-ai-title">
  <div className="section-heading"><h2 id="store-ai-title">{t('AI近似候选复核','AI類似候補の確認')}</h2>{error&&<Button variant="outline" disabled={busy||loading} onClick={()=>{setLoading(true);void refresh();}}>{t('重试','再試行')}</Button>}</div>
  <p>{t('只自动采纳高置信度的同店与不同店结论；同店保留最早主档。跨城或置信度不足的候选保留人工复核。','高確信度の同一店舗・別店舗の結論のみ自動採用し、同一店舗は最も古いマスターを保持します。都市が異なる場合や確信度が不十分な候補は手動確認に残します。')}</p>
  <p>{t('批处理会调用已配置的模型并可能产生费用。涉及的未封账月份在后台重算；封账月份跳过并提示。','バッチは設定済みモデルを呼び出し、料金が発生する場合があります。未締めの関連月は再計算し、締め月はスキップして通知します。')}</p>
  <label><AdminCheckbox  checked={confirmed} disabled={busy||runs.some(run=>run.status==='running')} onChange={event=>setConfirmed(event.target.checked)}/>{t('确认开始AI复核并采纳符合条件的结论','AI確認を開始し、条件に合う結論を採用する')}</label>
  <div className="dialog-actions"><Button disabled={busy||loading||!confirmed||runs.some(run=>run.status==='running')} onClick={()=>void start()}>{t(busy?'正在启动…':'开始AI复核',busy?'開始中…':'AI確認を開始')}</Button></div>
  {error&&<Alert className="error" role="alert">{error}</Alert>}
  {!loading&&!runs.length&&<p>{t('暂无AI复核批次，可继续人工处理候选。','AI確認バッチはありません。候補は引き続き手動処理できます。')}</p>}
  {!!runs.length&&<div className="table-scroll"><Table><TableHeader><TableRow>{[['批次','バッチ'],['状态 / 进度','状態 / 進捗'],['采纳结果','採用結果']].map(([zh,ja])=><TableHead key={zh}>{t(zh,ja)}</TableHead>)}</TableRow></TableHeader><TableBody>{runs.map(run=><TableRow key={run.id}><TableCell>#{run.id}<span className="row-id">{new Intl.DateTimeFormat(language==='ja'?'ja-JP':'zh-CN',{timeZone:'Asia/Tokyo',dateStyle:'short',timeStyle:'short'}).format(new Date(run.startedAt))}</span></TableCell><TableCell>{run.status==='running'?t('处理中','処理中'):(run.summary.errors||run.summary.error)?t('已结束，含错误','終了・エラーあり'):t('已结束','終了')}<span className="row-id">{run.summary.processed??0} / {run.total}</span>{run.summary.error&&<p className="error">{run.summary.error}</p>}</TableCell><TableCell>{t('同店','同一')} {run.summary.applied?.same??0} · {t('不同店','別店舗')} {run.summary.applied?.diff??0}<span className="row-id">{t('留人工','手動確認')} {run.summary.left_human??0} · {t('候选变化','候補変更')} {run.summary.changed??0} · {t('错误','エラー')} {run.summary.errors??0}</span></TableCell></TableRow>)}</TableBody></Table></div>}
 </section>;
}
