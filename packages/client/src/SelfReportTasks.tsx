import {UIFieldset} from './ui-controls';
import {UIInput} from './ui-controls';
export interface SelfTask{taskId:number;label:string;pct:number;currentPct:number;reportedPct:number|null;review:string|null;editable:boolean;token:string;storeCount:number|null}
export interface SelfReportView{token:string;date:string;personCode:string;backfill:boolean;locked:boolean;report:{area:string;p1:number;p2:number}|null;items:SelfTask[]}
export interface TaskDraft{taskId:number;token:string;pct:string;note:string;storeCount:string}
export function selfDraft(view:SelfReportView){return view.items.map(row=>({taskId:row.taskId,token:row.token,pct:String(row.pct),note:'',storeCount:row.storeCount===null?'':String(row.storeCount)}));}
export function selfItems(draft:TaskDraft[]){return draft.map(row=>({taskId:row.taskId,token:row.token,pct:row.pct.trim()===''?null:Number(row.pct),note:row.note,...row.storeCount.trim()===''?{}:{storeCount:Number(row.storeCount)}}));}
export function SelfReportTasks({view,draft,onChange,language,disabled}:{view:SelfReportView;draft:TaskDraft[];onChange:(value:TaskDraft[])=>void;language:string;disabled:boolean}){
 const t=(zh:string,ja:string)=>language==='ja'?ja:zh;
 function edit(id:number,key:'pct'|'note'|'storeCount',value:string){onChange(draft.map(row=>row.taskId===id?{...row,[key]:value}:row));}
 return <section className="self-report-tasks"><h2>{t('本次一起提交的车站进度','日報と一緒に提出する駅進捗')}</h2><p>{t('日报和有变化的任务一次保存，任一部分失败均不保存。进度没变且没填备注的任务跳过；完成时须填店铺数，可填0。','日報と変更したタスクを一度に保存し、どれかが失敗すると全体を保存しません。進捗もメモも変更なしのタスクはスキップします。完了時は店舗数を入力（0可）してください。')}</p>
 {!view.items.length&&<p>{t(view.backfill?'该日无日报，仅补录点数；任务进度不能补录。':'暂无可一起修改的车站进度。',view.backfill?'この日の日報はありません。ポイントのみ補完でき、タスク進捗は追加できません。':'一緒に編集できる駅進捗はありません。')}</p>}
 {view.items.map(task=>{const current=draft.find(row=>row.taskId===task.taskId);if(!current)return null;return <UIFieldset key={task.taskId} disabled={disabled||!task.editable}><legend>{task.label}</legend><p>{t('员工原值 / 当前值：','元申告 / 適用値：')}{task.reportedPct??'—'}% / {task.pct}%</p>{!task.editable&&<p>{t('该进度暂不可修改；不变的任务不会阻止点数保存。','この進捗は現在編集できません。変更なしならポイント保存は可能です。')}</p>}<div className="self-task-inputs"><label>{t('进度 ','進捗 ')+task.label}<UIInput type="number" inputMode="numeric" min={0} max={100} step={1} value={current.pct} onChange={event=>edit(task.taskId,'pct',event.target.value)}/></label><label>{t('店铺数 ','店舗数 ')+task.label}<UIInput type="number" inputMode="numeric" min={0} max={2147483647} step={1} value={current.storeCount} onChange={event=>edit(task.taskId,'storeCount',event.target.value)}/></label><label>{t('任务备注 ','タスクメモ ')+task.label}<UIInput value={current.note} maxLength={4000} onChange={event=>edit(task.taskId,'note',event.target.value)}/></label></div></UIFieldset>;})}
 </section>;
}
