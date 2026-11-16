export type AttendanceKind='in'|'out';
export interface AttendanceEvent {
 id:number;code:string;kind:AttendanceKind;at:string;source:'onsite'|'makeup';
 latitude:number|null;longitude:number|null;accuracy:number|null;locatedAt:string|null;createdAt:string;
}
export interface AttendanceMakeup {
 id:number;code:string;kind:AttendanceKind;at:string;reason:string;status:'pending'|'approved'|'rejected';
 note:string;createdAt:string;reviewedAt:string|null;reviewer:string|null;
}
export interface AttendanceData {
 day:string;today:string;scope:'mine'|'team'|'all';page:number;pageSize:number;total:number;
 people:{code:string;name:string;team:string}[];events:AttendanceEvent[];requests:AttendanceMakeup[];teams:{id:number;name:string}[];
}
