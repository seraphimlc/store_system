export type Language = 'zh' | 'ja';
export type Role = 'admin' | 'leader' | 'staff';
export interface CurrentUser {
  id: number;
  username: string;
  displayName: string;
  personCode: string | null;
  role: Role;
  status: 'active' | 'leave';
  language: Language;
  languagePreference?: '' | Language;
  mustChangePassword: boolean;
}
export interface Session { user: CurrentUser; csrfToken: string; landingPath?: string }
export interface ApiError { code: string; message: string; requestId: string }
export interface Dashboard {
  month: string | null;
  staff: number;
  people: number;
  records: number;
  points: number;
  salary: number;
  batches: number;
  openAppeals: number;
}
export interface BatchSummary {
  id: number;
  title: string;
  status: string;
  progress: number;
  regions: number;
  grids: number;
  totalStores: number;
  completedStores: number;
}

export interface Employee { id:number;username:string;displayName:string;personCode:string|null;role:Role;status:'active'|'leave'|'disabled'|'resigned';isActive:boolean;language:string;mustChangePassword:boolean }
export interface EmployeePage { items:Employee[];total:number;page:number;pageSize:number }
export interface DailyReport {
 id:number;personCode:string;name?:string;date:string;area:string;p1:number;p2:number;total:number;points:number;
 scoreShare:number|null;storeShare:number|null;source:string;submittedAt:string;
}
export interface DailyReportList {
 items:DailyReport[];total:number;page:number;pageSize:number;today?:string;canViewTeam?:boolean;
 summary:{p1:number;p2:number;total:number;points:number};coverage:{start:string|null;end:string|null};
}
export interface PlanDay {date:string;state:string;mark:string;reported:boolean;off:boolean;available:boolean;past:boolean;before_start:boolean;assumed:boolean;assumed_off:boolean;editable:boolean;lock:string;wd:number}
export interface PlanPeriod {key:string;start:string;end:string;deadline:string;openAt:string;closeAt:string;today:string;window_state:string;window_open:boolean;late_join:boolean;roster_start:string|null;submitted:boolean;days:PlanDay[];off_cnt:number;free_cnt:number;done_cnt:number;editable_cnt:number}
export interface MyPlan extends PlanPeriod {previous:string;next:string;pending:string|null;leave:{id:number;start_date:string;end_date:string|null;reason:string;status:string}|null}
export interface PlanMatrix {key:string;start:string;end:string;today:string;total?:number;unsubmitted?:string[];summary?:{date:string;free:number;unregistered:number;defaultCount:number;planned:number;actual:number|null}[];rows:(PlanPeriod&{code:string;name:string;status:string})[]}

export interface TeamDailyReportList extends DailyReportList {people:{code:string;name:string}[];selected:string}

export interface PayrollHalf {points:number;amount:number;bonus:number;records:number;p1:number;p2:number;adjustment:number;payable:number}
export interface PayrollPayment {id:number;person_code:string;seq:number;amount:number;points:number;bonus:number;adjust_applied:number;paid_at:string;adjust_leftover:number|null}
export interface PayrollRow {version?:string|null;adjustedAmount?:number;code:string;name:string;half1:PayrollHalf;half2:PayrollHalf;settlePoints:number;settleAmount:number;diffPoints:number;diffAmount:number;carryPoints:number;carryAmount:number;unrecovered:number;recoveredThisMonth:number;payments:PayrollPayment[]}
export interface PayrollData {duplicates?:Record<string,number>|null;month:string;configuration:{bonus:{group:number;amount:number;perPoint:number};staffVisibleFrom:string};rows:PayrollRow[];performance:{code:string;name:string;records:number;p1:number;p2:number;points:number;salary:number;perPoint:number;rate37:number;pass37:boolean}[];carry:{code:string;points:number;amount:number;remaining:number;recovered:number}[];summary:{people:number;half1Payable:number;half2Payable:number;registeredAmount:number;carryAmount:number}}
export interface PaymentPreview {month:string;half:number;token:string;registrationSemantics:string;rows:{personCode:string;name:string;points:number;grossAmount:number;bonus:number;adjustment:number;payable:number;registered:boolean;paymentId:number|null;registeredAmount:number|null;registeredAdjustment:number|null}[]}
export interface SettlementImport {id:number;fileName:string;status:string;uploadedAt:string;parsedRows:number;formalRows:number;pendingAppeals:number;unjudgedRows:number}
export interface SettlementRecordView {id:number;importId:number;name:string;storeId:string;modified:string;code:string;visible:string;deploy:string;status:string;confirmation:string;appealId:number|null;appealStatus:string|null;appealReason:string|null}
export interface SettlementRecordPage {total:number;items:SettlementRecordView[];page:number;pageSize:number}

export interface PerformanceDay {code:string|null;name:string|null;date:string;records:number;p1:number;p2:number;points:number}
export interface PerformanceTotals {records:number;p1:number;p2:number;points:number}
export interface PerformanceDaily {month:string;source:'formal_records'|'person_daily_stats';total:number;items:PerformanceDay[];summary:PerformanceTotals;monthSummary:PerformanceTotals;dates:string[];selectedDate:string|null;page:number;pageSize:number;people:{code:string;name:string}[]}
