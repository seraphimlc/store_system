import {useEffect,useRef,useState,type FormEvent} from 'react';
import {Button} from 'antd-mobile';
import {selfDraft,selfItems,SelfReportTasks,type SelfReportView,type TaskDraft} from '../../../packages/client/src/SelfReportTasks';
import '../../../packages/client/src/self-report.css';
import {api} from './api';
import type {Session} from '../../../packages/contracts/src';
export function TodayReport({session,language,withTasks=false,onSaved}:{session:Session;language:string;withTasks?:boolean;onSaved?:()=>void}){
 const t=(zh:string,ja:string)=>language==='ja'?ja:zh;
 const [view,setView]=useState<SelfReportView|null>(null),[draft,setDraft]=useState<TaskDraft[]>([]),[area,setArea]=useState(''),[p1,setP1]=useState('0'),[p2,setP2]=useState('0'),[loading,setLoading]=useState(true),[saving,setSaving]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const generation=useRef(0),writing=useRef(false);
 async function load(){const id=++generation.current;setLoading(true);try{const data=await api<SelfReportView>('/self-report/mine');if(id!==generation.current)return;setView(data);setDraft(selfDraft(data));setArea(data.report?.area??'');setP1(String(data.report?.p1??0));setP2(String(data.report?.p2??0));setError('');}catch(e){if(id===generation.current)setError((e as Error).message);}finally{if(id===generation.current)setLoading(false);}}
 useEffect(()=>{void load();return()=>{generation.current++;};},[session.user.id]);
 async function save(event:FormEvent){event.preventDefault();if(!view||writing.current)return;writing.current=true;setSaving(true);setError('');setNotice('');try{await api('/self-report/mine',session,{area,p1:Number(p1),p2:Number(p2),token:view.token,items:selfItems(draft)});setNotice(t('今日填报已保存','今日の日報を保存しました'));await load();window.dispatchEvent(new Event('h5-plan-updated'));onSaved?.();}catch(e){setError((e as Error).message);}finally{writing.current=false;setSaving(false);}}
 const one=Number(p1)||0,two=Number(p2)||0,total=one+two,points=one+2*two,share=(n:number,d:number)=>d?`${(100*n/d).toFixed(1)}%`:'—';
 return <section className="h5-today-report" aria-busy={loading}>
 {error&&<div className="error" role="alert"><p>{error}</p><Button fill="none" disabled={saving} onClick={()=>void load()}>{t('重新读取（将替换草稿）','再読込（入力を置換）')}</Button></div>}{notice&&<p className="success-notice" role="status">{notice}</p>}
 {loading?<p role="status">{t('正在读取今日填报…','今日の日報を読込中…')}</p>:view&&<form onSubmit={save} className="h5-report-form"><div className="h5-card"><div className="h5-row"><h2>{t('今日自报','今日の申告')}</h2><span>{view.date} · {view.report?t('已提交','提出済み'):t('未提交','未提出')}</span></div>
 {view.locked&&<p className="report-lock">{t('该日期已被正式数据覆盖，填报已锁定。','この日は確定データの対象となり、日報は変更できません。')}</p>}
 <fieldset disabled={saving||view.locked}><label>{t('担当区域','担当エリア')}<input name="area" value={area} onChange={e=>setArea(e.target.value)} maxLength={64}/></label><div className="h5-two-columns"><label>{t('1点店数','1ポイント店舗数')}<input name="p1" type="number" inputMode="numeric" min={0} max={999} step={1} value={p1} onChange={e=>setP1(e.target.value)} required/></label><label>{t('2点店数','2ポイント店舗数')}<input name="p2" type="number" inputMode="numeric" min={0} max={999} step={1} value={p2} onChange={e=>setP2(e.target.value)} required/></label></div>
 <dl className="h5-metrics"><div><dt>{t('总店铺数','合計店舗数')}</dt><dd>{total}</dd></div><div><dt>{t('总分数','合計ポイント')}</dt><dd>{points}</dd></div>{!withTasks&&<><div><dt>{t('1点分数','1ポイント分')}</dt><dd>{one}</dd></div><div><dt>{t('2点分数','2ポイント分')}</dt><dd>{two*2}</dd></div><div><dt>{t('2点分数占比','2ポイント得点比率')}</dt><dd>{share(two*2,points)}</dd></div><div><dt>{t('2点店铺占比','2ポイント店舗比率')}</dt><dd>{share(two,total)}</dd></div></>}</dl></fieldset></div>
 {!!view.items.length&&<details className="h5-card h5-joint-tasks" open={withTasks?true:undefined}><summary>{t('一起提交的车站进度','同時提出する駅進捗')} · {view.items.length}</summary><SelfReportTasks view={view} draft={draft} onChange={setDraft} language={language} disabled={saving||view.locked}/></details>}
 {!view.locked&&<Button block color="primary" type="submit" loading={saving} disabled={saving}>{withTasks?t('提交今日自报与任务进度','日報とタスク進捗を提出'):view.report?t('修改今日日报','今日の日報を編集'):t('提交今日日报','今日の日報を提出')}</Button>}
 <p className="h5-help">{withTasks?t('任一条失败则全部不保存。','いずれかが失敗すると全体を保存しません。'):t('日报用于核对，不直接参与工资计算。','日報は照合用です。給与計算には直接使用されません。')}</p></form>}
 </section>;
}
