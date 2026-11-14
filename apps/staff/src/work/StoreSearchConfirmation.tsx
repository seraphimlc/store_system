import {useState} from 'react';import {Button,Toast} from 'antd-mobile';
import type {StoreSearchFeedback,StoreSearchFeedbackReceipt} from '../../../../packages/contracts/src/store-search';
export function StoreSearchConfirmation({searchLogId,storeTaskId,language,report,hasRecorded}:{searchLogId:number;storeTaskId:number;language:string;report:(input:StoreSearchFeedback)=>Promise<StoreSearchFeedbackReceipt>;hasRecorded:(input:StoreSearchFeedback)=>boolean}){
 const t=(zh:string,ja:string)=>language==='ja'?ja:zh,input={kind:'confirmed' as const,searchLogId,storeTaskId};
 const [done,setDone]=useState(()=>hasRecorded(input)),[busy,setBusy]=useState(false),[error,setError]=useState('');
 async function confirm(){if(busy||done)return;setBusy(true);setError('');try{await report(input);setDone(true);Toast.show(t('已记录找到反馈','見つかったことを記録しました'));}catch(e){setError(e instanceof Error?e.message:t('反馈暂不可用，仍可继续巡店','記録できませんでした。巡回は続けられます'));}finally{setBusy(false);}}
 return <section className="work-search-confirmation" aria-label={t('搜索结果反馈','検索結果のフィードバック')}><p>{t('仅反馈搜索结果，不会提交巡店记录。','検索結果だけを記録します。巡回実績は送信されません。')}</p><Button size="small" disabled={busy||done} onClick={()=>void confirm()}>{done?t('已反馈找到','記録済み'):busy?t('正在记录…','記録中…'):t('这家就是我要找的','探していた店舗です')}</Button>{error&&<p role="alert">{error}</p>}</section>;
}
