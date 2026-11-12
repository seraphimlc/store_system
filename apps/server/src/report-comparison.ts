import { Inject, Injectable } from '@nestjs/common';
import type { PoolClient } from 'pg';
import { Database } from './database';
import { reportFingerprint } from './report-model';
import { KIND_BOTH, KIND_MISSING_REPORT, KIND_MISSING_SYSTEM } from './report-kinds';
export interface DailySide { person_code:string;date:string;p1:number;p2:number;total:number;name:string }
export const reportPoints=(p1:number,p2:number)=>p1+2*p2;
export const scoreShare=(p1:number,p2:number)=>reportPoints(p1,p2) ? 2*p2/reportPoints(p1,p2) : null;
export const reportAccuracy=(absolute:number,total:number)=>Math.max(0,1-absolute/Math.max(total,1));
export interface ComparisonDay {
 person_code:string;name:string;date:string;kind:string;
 sys_p1:number|null;sys_p2:number|null;sys_total:number|null;rep_p1:number|null;rep_p2:number|null;rep_total:number|null;
 sys_points:number|null;rep_points:number|null;sys_rate:number|null;rep_rate:number|null;
 d1:number|null;d2:number|null;dt:number|null;acc:number|null;
}
export interface ComparisonPerson {
 person_code:string;name:string;sys_p1:number;sys_p2:number;sys_total:number;rep_p1:number;rep_p2:number;rep_total:number;
 days_system:number;days_filled:number;days_both:number;abs_d1:number;abs_d2:number;abs_dt:number;
 both_sys_p1:number;both_sys_p2:number;both_sys_total:number;both_consistent:number;kinds:Record<string,number>;
 sys_points:number;rep_points:number;d_points:number;sys_rate:number|null;rep_rate:number|null;acc:number|null;d_total:number;d1:number;d2:number;gaps:number;
}
function counts(){return {[KIND_BOTH]:0,[KIND_MISSING_REPORT]:0,[KIND_MISSING_SYSTEM]:0};}
export function compareDaily(system:DailySide[],reports:DailySide[],start:string,end:string,sort='acc'){
 const systemMap=new Map(system.map(row=>[row.person_code+'\0'+row.date,row]));
 const reportMap=new Map(reports.map(row=>[row.person_code+'\0'+row.date,row]));
 const keys=[...new Set([...systemMap.keys(),...reportMap.keys()])].sort();
 const days:ComparisonDay[]=[];const per=new Map<string,ComparisonPerson>();const totals=counts();
 for(const key of keys){
  const s=systemMap.get(key),r=reportMap.get(key),origin=(s??r)!;
  const kind=s&&r?KIND_BOTH:s?KIND_MISSING_REPORT:KIND_MISSING_SYSTEM;
  totals[kind]++;
  const d1=s&&r?s.p1-r.p1:null,d2=s&&r?s.p2-r.p2:null,dt=s&&r?s.total-r.total:null;
  days.push({person_code:origin.person_code,name:origin.name,date:origin.date,kind,
   sys_p1:s?.p1??null,sys_p2:s?.p2??null,sys_total:s?.total??null,rep_p1:r?.p1??null,rep_p2:r?.p2??null,rep_total:r?.total??null,
   sys_points:s?reportPoints(s.p1,s.p2):null,rep_points:r?reportPoints(r.p1,r.p2):null,sys_rate:s?scoreShare(s.p1,s.p2):null,rep_rate:r?scoreShare(r.p1,r.p2):null,
   d1,d2,dt,acc:s&&r?reportAccuracy(Math.abs(dt!),s.total):null});
  let p=per.get(origin.person_code);
  if(!p){p={person_code:origin.person_code,name:origin.name,sys_p1:0,sys_p2:0,sys_total:0,rep_p1:0,rep_p2:0,rep_total:0,days_system:0,days_filled:0,days_both:0,abs_d1:0,abs_d2:0,abs_dt:0,both_sys_p1:0,both_sys_p2:0,both_sys_total:0,both_consistent:0,kinds:counts(),sys_points:0,rep_points:0,d_points:0,sys_rate:null,rep_rate:null,acc:null,d_total:0,d1:0,d2:0,gaps:0};per.set(origin.person_code,p);}
  p.kinds[kind]++;
  if(s){p.sys_p1+=s.p1;p.sys_p2+=s.p2;p.sys_total+=s.total;p.days_system++;}
  if(r){p.rep_p1+=r.p1;p.rep_p2+=r.p2;p.rep_total+=r.total;p.days_filled++;}
  if(s&&r){p.days_both++;p.abs_d1+=Math.abs(d1!);p.abs_d2+=Math.abs(d2!);p.abs_dt+=Math.abs(dt!);p.both_sys_p1+=s.p1;p.both_sys_p2+=s.p2;p.both_sys_total+=s.total;if(d1===0&&d2===0&&dt===0)p.both_consistent++;}
 }
 const persons=[...per.values()];
 for(const p of persons){p.sys_points=reportPoints(p.sys_p1,p.sys_p2);p.rep_points=reportPoints(p.rep_p1,p.rep_p2);p.d_points=p.sys_points-p.rep_points;p.sys_rate=scoreShare(p.sys_p1,p.sys_p2);p.rep_rate=scoreShare(p.rep_p1,p.rep_p2);p.acc=p.days_both?reportAccuracy(p.abs_dt,p.both_sys_total):null;p.d_total=p.sys_total-p.rep_total;p.d1=p.sys_p1-p.rep_p1;p.d2=p.sys_p2-p.rep_p2;p.gaps=p.kinds[KIND_MISSING_REPORT];}
 persons.sort((a,b)=>sort==='abs'?b.abs_dt-a.abs_dt||(a.acc??1)-(b.acc??1):(a.acc??1)-(b.acc??1)||b.abs_dt-a.abs_dt||b.days_both-a.days_both);
 const consistent=persons.reduce((n,p)=>n+p.both_consistent,0),matched=totals[KIND_BOTH];
 return {start,end,summary:{checkin_cnt:reports.length,formal_cnt:system.length,persons:persons.length,matched_cnt:matched,consistent_cnt:consistent,consistent_rate:matched?consistent/matched:null,counts:totals,days_filled:persons.reduce((n,p)=>n+p.days_filled,0),days_system:persons.reduce((n,p)=>n+p.days_system,0)},persons,daily:days,counts:totals,sort};
}
export type Comparison=ReturnType<typeof compareDaily>;
@Injectable()
export class ReportComparisonService {
 constructor(@Inject(Database)private readonly db:Database){}
 async compare(start:string,end:string,personCode='',sort='acc',client?:PoolClient){
  const values=[start,end,personCode];
  const sysSql=`SELECT s.person_code,s.ref_date::text AS date,coalesce(s.p1,0)::int AS p1,coalesce(s.p2,0)::int AS p2,(coalesce(s.p1,0)+coalesce(s.p2,0))::int AS total,coalesce(p.display_name,s.person_code) AS name FROM person_daily_stats s LEFT JOIN persons p ON p.code=s.person_code WHERE ref_date BETWEEN $1::date AND $2::date AND ($3='' OR s.person_code=$3) ORDER BY s.person_code,s.ref_date`;
  const repSql=`SELECT r.person_code,r.report_date::text AS date,r.p1_cnt AS p1,r.p2_cnt AS p2,r.total_cnt AS total,coalesce(p.display_name,r.person_code) AS name FROM staff_daily_reports r LEFT JOIN persons p ON p.code=r.person_code WHERE report_date BETWEEN $1::date AND $2::date AND ($3='' OR r.person_code=$3) ORDER BY r.person_code,r.report_date`;
  const sys=client?await client.query<DailySide>(sysSql,values):await this.db.query<DailySide>(sysSql,values);
  const rep=client?await client.query<DailySide>(repSql,values):await this.db.query<DailySide>(repSql,values);
  return compareDaily(sys.rows,rep.rows,start,end,sort);
 }
 async materialize(client:PoolClient,id:number,result:Comparison){
  await client.query('DELETE FROM staff_report_compare_day WHERE analysis_id=$1',[id]);
  await client.query('DELETE FROM staff_report_compare_person WHERE analysis_id=$1',[id]);
  const people=result.persons.map(p=>({...p,analysis_id:id}));
  const days=result.daily.map(d=>({...d,analysis_id:id,ref_date:d.date}));
  await client.query(`INSERT INTO staff_report_compare_person(analysis_id,person_code,name,sys_p1,sys_p2,sys_total,rep_p1,rep_p2,rep_total,d1,d2,d_total,acc,days_filled,days_system,days_both,gaps,abs_dt,created_at)
   SELECT analysis_id,person_code,name,sys_p1,sys_p2,sys_total,rep_p1,rep_p2,rep_total,d1,d2,d_total,acc,days_filled,days_system,days_both,gaps,abs_dt,now() FROM json_populate_recordset(NULL::staff_report_compare_person,$1::json)`,[JSON.stringify(people)]);
  await client.query(`INSERT INTO staff_report_compare_day(analysis_id,person_code,ref_date,kind,sys_p1,sys_p2,sys_total,rep_p1,rep_p2,rep_total,d1,d2,dt,acc,created_at)
   SELECT analysis_id,person_code,ref_date,kind,sys_p1,sys_p2,sys_total,rep_p1,rep_p2,rep_total,d1,d2,dt,acc,now() FROM json_populate_recordset(NULL::staff_report_compare_day,$1::json)`,[JSON.stringify(days)]);
 }
 async refresh(client:PoolClient,day:string){return this.refreshRange(client,day,day);}
 async refreshRange(client:PoolClient,start:string,end:string){
  const analyses=await client.query<{id:number;start:string;end:string;status:string;summary:Record<string,unknown>}>(`SELECT id,period_start::text AS start,period_end::text AS end,status,summary FROM staff_report_analyses WHERE status IN ('done','pending','running','failed') AND period_start<=$2::date AND period_end>=$1::date ORDER BY id FOR UPDATE`,[start,end]);
  for(const analysis of analyses.rows){
   const result=await this.compare(analysis.start,analysis.end,'','acc',client);await this.materialize(client,analysis.id,result);
   if(analysis.summary?.fingerprint&&analysis.summary.fingerprint!==reportFingerprint(result)){
    await client.query("UPDATE staff_report_analyses SET status='failed',summary=$1,payload='{}',ai_error='数据已变化，请重新生成',finished_at=now() WHERE id=$2",[JSON.stringify({...result.summary,fingerprint:reportFingerprint(result)}),analysis.id]);
   }
  }
 }
}
