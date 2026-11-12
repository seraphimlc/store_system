import { jstToday, monthRange, validDate } from './business-dates';
export interface PlanRow {date:string;available:boolean;reported:boolean;source:string;leave_id:number|null;updated_at?:string}
export function addDays(value:string,days:number){return new Date(Date.parse(value+'T00:00:00Z')+days*86400000).toISOString().slice(0,10);}
export function periodOf(day:string){if(!validDate(day))throw new Error('Invalid date');return day.slice(0,7)+(Number(day.slice(8))<=15?'-H1':'-H2');}
export function periodBounds(value:string){
 const match=/^(\d{4})-(\d{1,2})-(H[12])$/.exec(value.trim().toUpperCase());if(!match)throw new Error('Invalid period');
 const month=match[1]+'-'+match[2].padStart(2,'0');const range=monthRange(month);const key=month+'-'+match[3];
 const start=month+(match[3]==='H1'?'-01':'-16'),end=match[3]==='H1'?month+'-15':range.end;
 const deadline=month+(match[3]==='H1'?'-03':'-18');return {key,start,end,deadline,openAt:addDays(start,-7),closeAt:deadline};
}
export function shiftPeriod(key:string,halves=1){let period=periodBounds(key);for(let i=0;i<Math.abs(halves);i++){const day=halves>0?addDays(period.end,1):addDays(period.start,-1);period=periodBounds(periodOf(day));}return period.key;}
export function periodDays(key:string){const {start,end}=periodBounds(key);const days:string[]=[];for(let day=start;day<=end;day=addDays(day,1))days.push(day);return days;}
export function windowState(key:string,today=jstToday(),rosterStart:string|null=null){const period=periodBounds(key);const base=today<period.openAt?'before':today>period.closeAt?'closed':'open';if(base==='open')return base;if(today<=period.end&&rosterStart&&rosterStart>period.closeAt&&today>=rosterStart)return 'open';return base;}
export function planCell(available:boolean|null,reported:boolean,options:{past?:boolean;beforeStart?:boolean;inactive?:boolean;assumed?:boolean;assumedOff?:boolean;plannedOn?:boolean}={}){
 if(options.beforeStart)return 'na';if(reported)return 'done';if(options.past)return options.plannedOn?'miss':'off';if(options.inactive)return 'off';if(available===null){if(options.assumedOff)return 'off';return options.assumed?'on':'none';}return available?'on':'off';
}
export function planView(key:string,rows:PlanRow[],rosterStart:string|null,today=jstToday(),inactive=false,employee=true){
 const bounds=periodBounds(key);const window=windowState(key,today,rosterStart);const map=new Map(rows.map(row=>[row.date,row]));
 const defaultMode=today<=bounds.deadline?'':rosterStart&&rosterStart>bounds.closeAt?'on':'off';
 const marks:Record<string,string>={on:'○',off:'×',done:'□',none:'–',na:'',miss:'△'};
 const days=periodDays(key).map(date=>{
  const row=map.get(date);const before=!!rosterStart&&date<rosterStart;const past=date<today;
  const plannedOn=!!row?.available&&!row.leave_id&&!['leave','report'].includes(row.source);
  const state=planCell(row?.available??null,!!row?.reported,{past,beforeStart:before,inactive,assumed:defaultMode==='on'&&date>=today,assumedOff:defaultMode==='off'&&date>=today,plannedOn});
  const lock=row?.reported?'reported':past?'past':'';const editable=window==='open'&&!before&&!lock&&!inactive;
  return {date,wd:(new Date(date+'T00:00:00Z').getUTCDay()+6)%7,state,mark:employee&&editable&&state==='none'?marks.on:marks[state],reported:!!row?.reported,off:row?.available===false,available:row?.available!==false,past,before_start:before,assumed:defaultMode==='on'&&date>=today&&!row&&!before,assumed_off:defaultMode==='off'&&date>=today&&!row&&!before,editable,lock};
 });
 return {...bounds,today,window_state:window,window_open:window==='open',late_join:window==='open'&&!(today>=bounds.openAt&&today<=bounds.closeAt),roster_start:rosterStart,submitted:rows.some(row=>row.source!=='report'),last_at:rows.map(row=>row.updated_at).filter(Boolean).sort().at(-1)??null,days,off_cnt:days.filter(day=>day.off).length,free_cnt:days.filter(day=>!day.off).length,done_cnt:days.filter(day=>day.reported).length,editable_cnt:days.filter(day=>day.editable).length};
}

export function landingPath(role:string,_pendingPlan:boolean,afterPassword=false){if(role==='admin')return afterPassword?'/admin/perf':'/admin/';return '/my/work';}
