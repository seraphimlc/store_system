import {ScrollMore,useProgressiveItems} from './scrollList';
import {useEffect, useRef, useState} from 'react';
import {Button} from 'antd-mobile';
import {api} from './api';
interface Feedback {
  periods: {id:number;start:string;end:string}[];
  selected?: {id:number;start:string;end:string};
  person: {sys_total:number;rep_total:number;acc:number|null;gaps:number}|null;
  days: {date:string;sys_total:number|null;rep_total:number|null;dt:number|null;acc:number|null}[];
}
const percent = (value:number|null) => value === null ? '—' : (value * 100).toFixed(1) + '%';
export function MyReportFeedback({language}:{language:string}) {
  const t=(zh:string,ja:string)=>language==='ja'?ja:zh;
  const [data,setData]=useState<Feedback|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(true);
  const generation=useRef(0);
  async function load(analysisId?:number) {
    const id=++generation.current;setLoading(true);setError('');
    try {const value=await api<Feedback>('/reports/feedback'+(analysisId?'?analysisId='+analysisId:''));if(id===generation.current)setData(value);}
    catch(cause){if(id===generation.current)setError((cause as Error).message);}
    finally{if(id===generation.current)setLoading(false);}
  }
  useEffect(()=>{void load();return()=>{generation.current++;};},[]);
  const list=useProgressiveItems(data?.days??[]);
  return <section className="my-feedback-page" aria-busy={loading}>
    {error&&<div role="alert" className="error"><p>{error}</p><Button onClick={()=>void load(data?.selected?.id)}>{t('重试','再試行')}</Button></div>}
    {data&&data.periods.length>0&&<label>{t('核对区间','照合期間')}<select value={data.selected?.id??''} onChange={event=>void load(Number(event.target.value))} disabled={loading}>{data.periods.map(period=><option key={period.id} value={period.id}>{period.start} — {period.end}</option>)}</select></label>}
    {loading?<p role="status">{t('正在读取核对结果…','照合結果を読込中…')}</p>:data&&!data.person?<p className="report-lock">{t('本期数据还在核对中，暂无可查看的核对报告。','この期間のデータは照合中です。表示できるレポートはまだありません。')}</p>:data?.person&&<>
      <dl className="report-metrics">{[[t('系统店数','システム店舗数'),data.person.sys_total],[t('自报店数','申告店舗数'),data.person.rep_total],[t('准确率','正確率'),percent(data.person.acc)],[t('应填未填','未申告日数'),data.person.gaps]].map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
      <h2>{t('逐日核对','日別照合')}</h2><ul className="daily-data-list">{list.items.map(day=><li key={day.date}><h3>{day.date}</h3><dl>{[[t('系统店数','システム店舗数'),day.sys_total??'—'],[t('自报店数','申告店舗数'),day.rep_total??'—'],[t('差值（系统 − 自报）','差分（システム − 申告）'),day.dt??'—'],[t('准确率','正確率'),percent(day.acc)]].map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl></li>)}</ul><ScrollMore hasMore={list.hasMore} onLoad={list.loadMore} language={language}/>
    </>}
  </section>;
}
