import {ScrollMore,useScrollPage} from './scrollList';
import {useEffect,useRef,useState} from 'react';import {Button} from 'antd-mobile';import type {PerformanceDaily} from '../../../packages/contracts/src';import {api} from './api';
export function MyDailyPerformance({month,language}:{month:string;language:string}){
 const t=(zh:string,ja:string)=>language==='ja'?ja:zh;
 const list=useScrollPage<PerformanceDaily['items'][number],PerformanceDaily>('/payroll/mine/daily?'+new URLSearchParams({month}),row=>row.date,30);
 const {data,error,loading}=list;

 return <section className="my-daily-performance"><h2>{t('每日绩效明细','日別実績明細')}</h2><p className="report-explanation">{t('以下是已入正式表的每日统计，不是自行填报的日报。','以下は確定済み記録の日別集計です。自己申告の日報とは異なります。')}</p>{error&&<p className="error" role="alert">{error}</p>}{loading&&!data?<p role="status">{t('正在读取日明细…','日明細を読み込み中…')}</p>:data&&<><dl className="report-metrics"><div><dt>{t('整月有效店','月間有効店舗')}</dt><dd>{data.monthSummary.records}</dd></div><div><dt>{t('整月点数','月間ポイント')}</dt><dd>{data.monthSummary.points}</dd></div></dl>{list.items.length?<ul className="daily-data-list">{list.items.map(row=><li key={row.date}><h3>{row.date}</h3><dl>{[[t('有效店','有効店舗'),row.records],[t('1点店','1ポイント店舗'),row.p1],[t('2点店','2ポイント店舗'),row.p2],[t('点数','ポイント'),row.points]].map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl></li>)}</ul>:<p className="report-explanation">{t('该月暂无已入表的日明细。','この月の確定済み日明細はありません。')}</p>}<ScrollMore hasMore={list.hasMore} loading={loading} error={error} onLoad={list.loadMore} language={language}/></>}
 </section>;
}
