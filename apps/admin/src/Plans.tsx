import {AdminDateInput} from './AdminDateInput';
import {Alert} from '@appica/ui-react/alert';
import {Form} from '@appica/ui-react/form';
import {AdminSelect} from './AdminSelect';
import {Table,TableHeader,TableRow,TableHead,TableBody,TableCell} from '@appica/ui-react/table';
import {Input} from '@appica/ui-react/input';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Button } from '@appica/ui-react/button';
import { Download } from 'lucide-react';
import type { PlanMatrix } from '../../../packages/contracts/src';
import { api } from './api';
function defaultKey(){const day=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Tokyo',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());return day.slice(0,7)+(Number(day.slice(8))<=15?'-H1':'-H2');}
export function Plans({language}:{language:string}){
 const t=(zh:string,ja:string)=>language==='ja'?ja:zh;
 const [key,setKey]=useState(defaultKey),[data,setData]=useState<PlanMatrix|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(true);
 const generation=useRef(0);
 useEffect(()=>{const id=++generation.current;setLoading(true);api<PlanMatrix>('/plans?key='+key).then(value=>{if(id===generation.current)setData(value);}).catch(e=>{if(id===generation.current)setError(e.message);}).finally(()=>{if(id===generation.current)setLoading(false);});return()=>{generation.current++;};},[key]);
 function filter(event:FormEvent<HTMLFormElement>){event.preventDefault();const values=new FormData(event.currentTarget);setError('');setKey(String(values.get('month'))+'-'+values.get('half'));}
 return <section><Form className="report-filters admin-filter-panel" onSubmit={filter}><label>{t('月份','月')}<AdminDateInput name="month" type="month" defaultValue={key.slice(0,7)} required/></label><label>{t('半月','半月')}<AdminSelect name="half" defaultValue={key.slice(-2)}><option value="H1">{t('上半月 1–15','前半 1–15')}</option><option value="H2">{t('下半月 16–月末','後半 16–月末')}</option></AdminSelect></label><div className="admin-filter-submit"><Button type="submit">{t('查询','検索')}</Button><a className="toolbar-download" href={'/api/plans/export?key='+key} download><Download size={16}/>{t('导出出勤计划','出勤予定を出力')}</a></div></Form>
 {error&&<Alert className="error" role="alert">{error}</Alert>}<p className="plan-legend">{t('○ 计划出勤　× 不出勤　□ 已自报　△ 计划出勤未自报　– 未登记','○ 出勤予定　× 欠勤　□ 申告済み　△ 出勤予定・未申告　– 未登録')}</p>{loading?<p role="status">{t('正在读取计划…','予定を読み込み中…')}</p>:!data?.rows.length?<div className="empty-state">{t('暂无员工计划','スタッフの予定はまだありません')}</div>:<div className="table-scroll plan-matrix"><Table><TableHeader><TableRow><TableHead>{t('员工','スタッフ')}</TableHead>{data.rows[0].days.map(day=><TableHead key={day.date}>{day.date.slice(5)}</TableHead>)}<TableHead>{t('已出勤','申告済み')}</TableHead><TableHead>{t('登记状态','登録状態')}</TableHead></TableRow></TableHeader><TableBody>{data.summary&&[['计划出勤','planned'],['实际出勤','actual'],['未登记','unregistered']].map(([label,field])=><TableRow key={field} className="matrix-summary-row"><TableCell>{t(label,({planned:'出勤予定',actual:'申告済み',unregistered:'未登録'} as Record<string,string>)[field])}</TableCell>{data.summary!.map(day=><TableCell key={day.date}>{day[field as 'planned'|'actual'|'unregistered']??'—'}</TableCell>)}<TableCell colSpan={2}/></TableRow>)}{data.rows.map(row=><TableRow key={row.code}><TableCell><strong>{row.name}</strong><span className="row-id">{row.code}</span></TableCell>{row.days.map(day=><TableCell className={'plan-cell plan-'+day.state} key={day.date} title={day.date+(day.reported?t(' 已自报',' 申告済み'):'')}>{day.mark||' '}</TableCell>)}<TableCell>{row.done_cnt}</TableCell><TableCell>{row.submitted?t('已登记','登録済み'):t('未登记','未登録')}</TableCell></TableRow>)}</TableBody></Table></div>}</section>;
}
