import {useEffect,useState} from 'react';
import {Button} from 'antd-mobile';
import type {Session} from '../../../../packages/contracts/src';
import {api} from '../api';
import {go,type Tree} from './types';
import {usePageHeader} from '../pageHeader';
import {ScrollMore,useProgressiveItems} from '../scrollList';
export function BatchDetail({session,language,id}:{session:Session;language:string;id:number}){
 const t=(zh:string,ja:string)=>language==='ja'?ja:zh;
 const [tree,setTree]=useState<Tree|null>(null),[error,setError]=useState('');
 useEffect(()=>{let live=true;setTree(null);setError('');api<Tree>('/batch-work/batches/'+id+'/mobile',session).then(v=>{if(live)setTree(v);}).catch(e=>{if(live)setError(e.message);});return()=>{live=false;};},[id,session]);
 usePageHeader(tree?.main.title||t('批次详情','バッチ詳細'),'/my/work');
 const list=useProgressiveItems(tree?.regions??[]);
 if(error)return <p className="error" role="alert">{error}</p>;
 if(!tree)return <p role="status">{t('正在读取批次…','バッチを読込中…')}</p>;
 const completed=tree.regions.reduce((sum,r)=>sum+r.summary.completed,0),total=tree.regions.reduce((sum,r)=>sum+r.summary.total,0);
 return <section className="work-page work-batch-detail"><div className="work-actions work-batch-toolbar"><span className="work-batch-summary">{t('批次进度','バッチ進捗')} {tree.main.progress}% · {completed}/{total}</span><div className="work-batch-tools"><Button onClick={()=>go('/my/work/batches/'+id+'/logs')}>{t('批次日志','バッチ履歴')}</Button><Button onClick={()=>go('/my/work/batches/'+id+'/new-stores')}>{t('新店核实','新規店舗確認')}</Button></div></div>
 <div className="work-region-list-scroll" aria-label={t('片区列表','地域一覧')}><ul className="work-region-rows">{list.items.map(r=><li key={r.id}><button className="work-region-row" onClick={()=>go('/my/work/regions/'+r.id)}><span className="work-row"><strong>{r.snapshot.name||'#'+r.id}</strong><b>{r.progress}%</b></span><span className="work-row"><span>{r.summary.grids} {t('网格','グリッド')}</span><span>{r.summary.completed} / {r.summary.total} {t('家商户','店舗')}</span></span></button></li>)}</ul><ScrollMore hasMore={list.hasMore} onLoad={list.loadMore} language={language}/></div>
 </section>;
}
