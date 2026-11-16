import {useEffect,useState} from 'react';
import {Dialog} from 'antd-mobile';
import type {Language,Session} from '../../../packages/contracts/src';
import type {AttendanceData} from '../../../packages/contracts/src/attendance';
import {api} from './api';
const memory=new Map<string,string>();
function key(id:number,type:string){return 'store:daily-login:'+type+':'+id;}
function seen(id:number,type:string,day:string){const k=key(id,type);try{return localStorage.getItem(k)===day||memory.get(k)===day;}catch{return memory.get(k)===day;}}
function remember(id:number,type:string,day:string){const k=key(id,type);memory.set(k,day);try{localStorage.setItem(k,day);}catch{}}
type Reminder={userId:number;day:string;type:'attendance'|'plan'};
export function AttendanceReminder({session,language,planPending,onGo}:{session:Session|null;language:Language;planPending:string|null;onGo:(target:'attendance'|'plan')=>void}){
 const [attendance,setAttendance]=useState<Reminder|null>(null),[plan,setPlan]=useState<Reminder|null>(null),[checked,setChecked]=useState(false);
 const id=session?.user.id,code=session?.user.personCode,role=session?.user.role,mustChange=session?.user.mustChangePassword;
 const eligible=!!id&&!!code&&!mustChange&&['staff','leader'].includes(role??'');
 useEffect(()=>{
  setAttendance(null);setChecked(false);
  if(!eligible)return;
  const controller=new AbortController();
  void api<AttendanceData>('/attendance?scope=mine',session,undefined,controller.signal).then(data=>{
   if(controller.signal.aborted)return;
   if(!seen(id!,'attendance',data.today)){
    if(data.events.some(e=>e.code===code&&e.kind==='in'))remember(id!,'attendance',data.today);
    else setAttendance({userId:id!,day:data.today,type:'attendance'});
   }
  }).catch(()=>{}).finally(()=>{if(!controller.signal.aborted)setChecked(true);});
  return()=>controller.abort();
 },[id,code,role,mustChange]);
 useEffect(()=>{
  setPlan(null);
  if(!eligible||!planPending)return;
  // Date-plan registration retains its existing JST calendar-day semantics.
  const day=new Date(Date.now()+9*3600_000).toISOString().slice(0,10);
  if(!seen(id!,'plan',day))setPlan({userId:id!,day,type:'plan'});
 },[id,eligible,planPending]);
 const current=eligible?(attendance?.userId===id?attendance:checked&&plan?.userId===id?plan:null):null;
 function dismiss(go=false){if(!current)return;remember(current.userId,current.type,current.day);if(current.type==='attendance')setAttendance(null);else setPlan(null);if(go)onGo(current.type);}
 const ja=language==='ja',isPlan=current?.type==='plan';
 return <Dialog visible={!!current} bodyClassName="attendance-reminder-dialog" title={isPlan?(ja?'出勤予定を登録してください':'请登记出勤计划'):(ja?'出勤打刻をお忘れなく':'请先完成出勤打卡')} closeOnMaskClick={false} actions={[
  [{key:'go',text:isPlan?(ja?'登録する':'去登记'):(ja?'打刻する':'去打卡'),bold:true,onClick:()=>dismiss(true)}],
  [{key:'later',text:ja?'後で':'稍后再说',onClick:()=>dismiss()}],
 ]}/>;
}
