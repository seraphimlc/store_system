import type {AttendanceEvent} from '../../contracts/src/attendance';
/** Do not mutate the activity feed or pair coordinates from a different event. */
export function attendanceSummary(events:AttendanceEvent[]){
 const ordered=[...events].sort((a,b)=>Date.parse(a.at)-Date.parse(b.at)||a.id-b.id);
 const firstIn=ordered.find(event=>event.kind==='in')??null;
 const lastOut=ordered.filter(event=>event.kind==='out').at(-1)??null;
 return {firstIn,lastOut,outWithoutIn:!!lastOut&&!firstIn};
}
