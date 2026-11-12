export interface ReconDay {code:string;name:string;date:string;points:number;count:number}
export interface ReconSystem {code:string;date:string;points:number;count:number}
export function reconcileDays(report:ReconDay[],system:ReconSystem[]){
 const reported=new Map(report.map(row=>[row.code+'\0'+row.date,row])),local=new Map(system.map(row=>[row.code+'\0'+row.date,row]));
 return [...new Set([...reported.keys(),...local.keys()])].sort().flatMap(key=>{
  const r=reported.get(key),s=local.get(key),source=(r??s)!;const side=r&&s?'both':s?'local_only':'report_only',diff=(s?.points??0)-(r?.points??0);
  if(side==='both'&&diff===0)return [];
  return [{person_code:source.code,ref_date:source.date,sys_points:s?.points??0,rep_points:r?.points??0,sys_count:s?.count??0,rep_count:r?.count??0,diff,side,note:side==='both'?'':r?.name??''}];
 });
}
export function aggregateReconDays(days:ReconDay[]){const grouped=new Map<string,ReconDay>();for(const row of days){const key=row.code+'\0'+row.date,current=grouped.get(key);if(current){current.points+=row.points;current.count+=row.count;}else grouped.set(key,{...row});}return [...grouped.values()].sort((a,b)=>a.code.localeCompare(b.code)||a.date.localeCompare(b.date));}
export function integerPoints(value:unknown){const text=String(value??'').trim();if(!text)return null;const number=Number(text);if(!Number.isFinite(number))return null;const integer=Math.trunc(number);return Number.isSafeInteger(integer)&&Math.abs(integer)<=2147483647?integer:null;}
