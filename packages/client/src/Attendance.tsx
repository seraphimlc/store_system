import {useEffect,useRef,useState,type FormEvent,type ComponentType,type ComponentPropsWithoutRef,type ReactNode} from 'react';
import type {Language,Session} from '../../contracts/src';
import type {AttendanceData,AttendanceEvent,AttendanceKind,AttendanceMakeup} from '../../contracts/src/attendance';
import {attendanceSummary} from '../../domain/src/attendance';
import {api} from './request';
import {UIButton,UIInput,UISelect,UITextarea,UISuccessNotice,UITable,UITableHeader,UITableBody,UITableRow,UITableHead,UITableCell} from './ui-controls';
import './attendance.css';

type Punch={kind:AttendanceKind;requestKey:string;latitude:number;longitude:number;accuracy:number;locatedAt:string};
type Makeup={kind:AttendanceKind;requestKey:string;occurredAt:string;reason:string};
export type AttendanceSheetProps={visible:boolean;title:string;busy:boolean;onClose:()=>void;children:ReactNode};
export type AttendanceDateProps={visible:boolean;value:string;language:Language;onConfirm:(day:string)=>void;onClose:()=>void};
type Props={session:Session;language:Language;mobile?:boolean;compact?:boolean;onSuccess?:(message:string)=>void;mobileButton?:ComponentType<ComponentPropsWithoutRef<'button'>>;mobileSheet?:ComponentType<AttendanceSheetProps>;mobileDatePicker?:ComponentType<AttendanceDateProps>};
const dictionary:Record<string,string>={
 '选择日期':'日付を選択','查看记录':'記録を表示','取消':'キャンセル','当天记录':'この日の記録','本页':'このページ','待审核补卡':'承認待ちの修正申請','状态':'状態','已出勤':'出勤済み','今天':'今日','历史记录':'過去の記録','前一天':'前日','后一天':'翌日','打卡规则':'打刻ルール','日本时间 · 每日05:00换日':'日本時間・毎日05:00に日付切替','最早出勤':'最初の出勤','最晚退勤':'最後の退勤','今天还没有打卡':'本日の打刻はまだありません','打卡后，时间和位置会显示在这里。':'打刻すると、時刻と位置がここに表示されます。','当天没有打卡记录':'この日の打刻はありません','可选择其他日期，或申请补卡。':'別の日を選ぶか、修正打刻を申請できます。','当天动态':'この日の履歴','补卡申请':'修正申請','正在获取位置':'位置情報を取得中','正在保存打卡':'打刻を保存中','请稍候，不要关闭页面':'画面を閉じずにお待ちください','定位成功后自动记录，无需再次确认。':'位置の取得後に自動で記録します。','所有打卡都会保留，汇总取最早出勤、最晚退勤。':'すべての打刻を保存し、最初の出勤・最後の退勤を採用します。','关闭':'閉じる','暂无可用记录，请重试':'記録を確認できません。再試行してください','返回今日打卡':'本日の打刻に戻る','今日出退勤':'本日の出退勤','出退勤记录':'出退勤記録','我的记录':'自分の記録','本队动态':'チームの履歴','出勤打卡':'出勤打刻','退勤打卡':'退勤打刻',
 '出勤':'出勤','退勤':'退勤','未打卡':'未打刻','业务日期':'勤務日','团队':'チーム','全部团队':'すべてのチーム','姓名或编号':'氏名・番号','查询':'検索','刷新':'更新',
 '正在加载…':'読み込み中…','加载失败，请重试':'読み込みに失敗しました。再試行してください','重试':'再試行','定位中…':'位置情報を取得中…','提交中…':'送信中…',
 '打卡已记录':'打刻を記録しました','查看记录与补卡':'記録・打刻修正へ','查看本队动态':'チームの履歴へ','暂无记录':'記録はありません','暂无成员':'対象のメンバーはいません',
 '定位失败，未打卡。请检查系统定位和网络后重试。':'位置を取得できず、打刻されていません。位置情報と通信を確認してください。',
 '定位权限被拒绝，未打卡。请在浏览器或系统设置中允许定位。':'位置情報が許可されていないため打刻できません。ブラウザ・端末の設定を確認してください。',
 '定位超时，未打卡。请到信号较好的位置重试。':'位置情報の取得がタイムアウトしました。電波の良い場所で再試行してください。',
 '当前环境不支持定位，不能打卡。请使用支持定位的 HTTPS 浏览器。':'この環境では位置情報を利用できません。位置情報に対応した HTTPS ブラウザをご利用ください。',
 '出勤取最早，退勤取最晚；日本时间每天05:00换日。':'最初の出勤・最後の退勤を採用。日本時間05:00に勤務日が切り替わります。',
 '仅打卡时获取位置，不限制地点，不持续追踪。':'打刻時のみ位置を取得。場所の制限・継続追跡はありません。',
 '仅有退勤，无出勤':'退勤のみ・出勤なし','未退勤':'退勤未打刻','已完成':'出退勤あり','动态明细':'打刻履歴','现场打卡':'現地打刻','补卡':'修正打刻',
 '查看位置':'位置を表示','精度约':'精度約','米':'m','无现场定位':'現地の位置情報なし','已采用':'採用','申请补卡':'打刻修正を申請',
 '补卡时间（日本时间）':'修正日時（日本時間）','补卡原因':'修正理由','提交申请':'申請する','补卡申请已提交':'修正申請を送信しました',
 '补卡不含现场定位，须由管理员处理。':'修正打刻には現地の位置情報はなく、管理者の承認が必要です。',
 '待处理':'承認待ち','已通过':'承認済み','已拒绝':'却下','审核备注':'審査メモ','通过':'承認','拒绝':'却下','处理结果已保存':'審査結果を保存しました',
 '上一页':'前へ','下一页':'次へ','人':'人','申请与审核':'申請・審査','保存时间':'記録日時','处理人':'審査者','处理时间':'審査日時',
 '重试上次出勤提交':'前回の出勤送信を再試行','重试上次退勤提交':'前回の退勤送信を再試行','重新定位打卡':'位置を再取得して打刻',
 '上次提交结果待确认，请先重试确认，避免重复打卡。':'前回の送信結果を確認できません。再試行して結果を確認してください。',
};
export function Attendance({session,language,mobile=false,compact=false,onSuccess,mobileButton,mobileSheet,mobileDatePicker}:Props){
 const t=(value:string)=>language==='ja'?dictionary[value]??value:value;
 const admin=session.user.role==='admin',leader=session.user.role==='leader';
 const [scope]=useState<'mine'|'team'|'all'>(()=>admin?'all':!compact&&location.pathname.includes('/team-attendance')&&leader?'team':'mine');
 const [teams,setTeams]=useState<AttendanceData['teams']>([]);
 const [day,setDay]=useState(''),[team,setTeam]=useState(''),[q,setQ]=useState(''),[search,setSearch]=useState(''),[page,setPage]=useState(1);
 const [data,setData]=useState<AttendanceData|null>(null),[loading,setLoading]=useState(true),[error,setError]=useState('');
 const [busy,setBusy]=useState(''),[refresh,setRefresh]=useState(0),[notice,setNotice]=useState<{text:string;id:number}|null>(null);
 const [dateOpen,setDateOpen]=useState(false);
 const [makeupOpen,setMakeupOpen]=useState(false),[makeupError,setMakeupError]=useState('');
 const ActionButton=mobileButton??UIButton,Sheet=mobileSheet,DateSelector=mobileDatePicker;
 const [retry,setRetry]=useState<Punch|null>(null),[reviewError,setReviewError]=useState('');
 const pendingPunch=useRef<Punch|null>(null),pendingMakeup=useRef<Makeup|null>(null),lock=useRef(false),serial=useRef(0);
 const mounted=useRef(true);
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
 useEffect(()=>{
  const controller=new AbortController();setLoading(true);setError('');setData(null);
  const query=new URLSearchParams({scope,page:String(page),q:search,...(day?{day}:{}),...(team?{team}:{})});
  api<AttendanceData>('/attendance?'+query,session,undefined,controller.signal).then(result=>{if(!controller.signal.aborted){setData(result);setTeams(result.teams);}}).catch(e=>{if(!controller.signal.aborted)setError(e.message);}).finally(()=>{if(!controller.signal.aborted)setLoading(false);});
  return()=>controller.abort();
 },[scope,day,team,search,page,refresh,session]);
 // Re-evaluate the current business date after returning from a background tab or crossing 05:00 JST.
 useEffect(()=>{const update=()=>{if(!document.hidden)reload();};const rollover=()=>{if(!document.hidden&&data&&data.today!==new Date(Date.now()+4*3600_000).toISOString().slice(0,10))reload();};const timer=window.setInterval(rollover,60_000);document.addEventListener('visibilitychange',update);return()=>{window.clearInterval(timer);document.removeEventListener('visibilitychange',update);};},[data]);
 function success(text:string){if(onSuccess)onSuccess(t(text));else setNotice({text:t(text),id:++serial.current});}
 function reload(){setRefresh(v=>v+1);}
 async function punch(kind:AttendanceKind,retryBody?:Punch){
  if(lock.current)return;lock.current=true;setBusy(retryBody?'submit':'location');setError('');
  try{
   let body=retryBody;
   if(!body){
    if(!window.isSecureContext||!navigator.geolocation)throw new Error(t('当前环境不支持定位，不能打卡。请使用支持定位的 HTTPS 浏览器。'));
    const position=await new Promise<GeolocationPosition>((resolve,reject)=>navigator.geolocation.getCurrentPosition(resolve,e=>reject(new Error(t(e.code===1?'定位权限被拒绝，未打卡。请在浏览器或系统设置中允许定位。':e.code===3?'定位超时，未打卡。请到信号较好的位置重试。':'定位失败，未打卡。请检查系统定位和网络后重试。'))),{enableHighAccuracy:true,maximumAge:0,timeout:15000}));
    if(!mounted.current)return;
    body={kind,requestKey:crypto.randomUUID(),latitude:position.coords.latitude,longitude:position.coords.longitude,accuracy:position.coords.accuracy,locatedAt:new Date(position.timestamp).toISOString()};
   }
   pendingPunch.current=body;setBusy('submit');
   const result=await api<{day:string}>('/attendance/punch',session,body);
   if(!compact){setDay('');setPage(1);}
   pendingPunch.current=null;setRetry(null);if(mounted.current){success('打卡已记录');reload();}
  }catch(e){if(mounted.current){setError((e as Error).message);setRetry(pendingPunch.current);}}
  finally{lock.current=false;if(mounted.current)setBusy('');}
 }
 async function makeup(event:FormEvent<HTMLFormElement>){
  event.preventDefault();if(lock.current)return;const form=event.currentTarget,values=new FormData(form);
  // datetime-local is explicitly JST, not the browser's local timezone.
  const occurredAt=new Date(String(values.get('at'))+':00+09:00').toISOString(),kind=String(values.get('kind')) as AttendanceKind,reason=String(values.get('reason')).trim();
  const previous=pendingMakeup.current,body=previous&&previous.kind===kind&&previous.occurredAt===occurredAt&&previous.reason===reason?previous:{kind,occurredAt,reason,requestKey:crypto.randomUUID()};
  lock.current=true;setBusy('makeup');setMakeupError('');pendingMakeup.current=body;
  try{const result=await api<{day:string}>('/attendance/makeup',session,body);pendingMakeup.current=null;form.reset();setMakeupOpen(false);setDay(result.day);setPage(1);success('补卡申请已提交');reload();}
  catch(e){if(mobile)setMakeupError((e as Error).message);else setError((e as Error).message);}finally{lock.current=false;setBusy('');}
 }
 async function review(event:FormEvent<HTMLFormElement>,request:AttendanceMakeup){
  event.preventDefault();if(lock.current)return;const form=event.currentTarget,values=new FormData(form),submitter=(event.nativeEvent as SubmitEvent).submitter as HTMLButtonElement;
  lock.current=true;setBusy('review-'+request.id);setReviewError('');
  try{await api('/attendance/makeup/'+request.id+'/review',session,{decision:submitter.value,note:String(values.get('note'))});success('处理结果已保存');reload();}
  catch(e){setReviewError((e as Error).message);}finally{lock.current=false;setBusy('');}
 }
 function at(value:string){return new Intl.DateTimeFormat(language==='ja'?'ja-JP':'zh-CN',{timeZone:'Asia/Tokyo',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(value));}
 function position(event:AttendanceEvent){return event.source==='makeup'?<span>{t('补卡')} · {t('无现场定位')}</span>:<span><a href={'https://www.google.com/maps?q='+event.latitude+','+event.longitude} target="_blank" rel="noopener noreferrer">{t('查看位置')}</a> · {event.latitude?.toFixed(5)}, {event.longitude?.toFixed(5)} · {t('精度约')}{Math.round(event.accuracy!)}{t('米')}</span>;}
 function summaryTime(event:AttendanceEvent|null){if(!data)return <span className="attendance-muted">{t(loading?'正在加载…':'暂无可用记录，请重试')}</span>;return event?<><time dateTime={event.at}>{at(event.at)}</time><small>{event.source==='makeup'?t('补卡'):<><a href={'https://www.google.com/maps?q='+event.latitude+','+event.longitude} target="_blank" rel="noopener noreferrer">{t('查看位置')}</a> · {t('精度约')}{Math.round(event.accuracy!)}{t('米')}</>}</small></>:<span className="attendance-muted">{t('未打卡')}</span>;}
 function activity(code:string,expanded=false){
  const events=data!.events.filter(e=>e.code===code),requests=data!.requests.filter(r=>r.code===code),summary=attendanceSummary(events),pending=requests.filter(r=>r.status==='pending').length;
  const Container=expanded?'section':'details';
  return <Container className={expanded?'attendance-feed':'attendance-detail'}>{expanded?<div className="attendance-section-heading"><h2>{t('当天动态')}</h2><span>{events.length}</span></div>:<summary>{t('动态明细')} · {events.length}{requests.length>0&&<span className={pending?'attendance-pending':'attendance-muted'}> · {t(pending?'待审核补卡':'补卡申请')} {pending||requests.length}</span>}</summary>}
   {!events.length&&(expanded?<div className="attendance-empty"><p>{t(isToday?'今天还没有打卡':'当天没有打卡记录')}</p><small>{t(isToday?'打卡后，时间和位置会显示在这里。':'可选择其他日期，或申请补卡。')}</small></div>:!requests.length&&<p>{t('暂无记录')}</p>)}
   <ol className="attendance-activity">{[...events].sort((a,b)=>Date.parse(b.at)-Date.parse(a.at)||b.id-a.id).map(e=><li key={e.id}>
    <div><strong>{t(e.kind==='in'?'出勤':'退勤')}</strong> · <time dateTime={e.at}>{at(e.at)}</time> · {t(e.source==='onsite'?'现场打卡':'补卡')}{(summary.firstIn?.id===e.id||summary.lastOut?.id===e.id)&&<span className="attendance-label">{t('已采用')}</span>}</div>
    <div>{position(e)}</div><small>{t('保存时间')}：{at(e.createdAt)}</small>
   </li>)}</ol>
   {expanded&&requests.length>0&&<h3 className="attendance-request-heading">{t('补卡申请')} · {requests.length}</h3>}
   {requests.map(r=><section key={r.id} className="attendance-request"><p><strong>{t('补卡')} · {t(r.kind==='in'?'出勤':'退勤')}</strong> · {at(r.at)} · {t(r.status==='pending'?'待处理':r.status==='approved'?'已通过':'已拒绝')}</p>
    <p className="attendance-prewrap">{r.reason}</p><small>{t('无现场定位')} · {t('保存时间')}：{at(r.createdAt)}</small>
    {r.reviewedAt&&<p>{t('处理人')}：{r.reviewer||'—'} · {t('处理时间')}：{at(r.reviewedAt)}</p>}{r.note&&<p className="attendance-prewrap">{t('审核备注')}：{r.note}</p>}
    {admin&&r.status==='pending'&&<form onSubmit={e=>void review(e,r)}><label>{t('审核备注')}<UIInput name="note" maxLength={1000}/></label><div className="attendance-actions"><UIButton name="decision" value="approved" disabled={!!busy}>{t('通过')}</UIButton><UIButton name="decision" value="rejected" disabled={!!busy}>{t('拒绝')}</UIButton></div></form>}
   </section>)}
  </Container>;
 }
 const isToday=compact||!day||day===data?.today;
 const mine=scope==='mine',own=attendanceSummary(data?.events.filter(e=>e.code===session.user.personCode)??[]);
 function moveDay(delta:number){const value=day||data?.day||new Date(Date.now()+4*3600_000).toISOString().slice(0,10);setDay(new Date(Date.parse(value+'T12:00:00Z')+delta*86400_000).toISOString().slice(0,10));setPage(1);}
 function recordStatus(code:string){const summary=attendanceSummary(data!.events.filter(e=>e.code===code));return <span className={summary.outWithoutIn?'attendance-state attendance-state-error':'attendance-state'}>{t(summary.outWithoutIn?'仅有退勤，无出勤':summary.firstIn&&summary.lastOut?'已完成':summary.firstIn?'已出勤':'未打卡')}</span>;}
 function clock(value:string){return new Intl.DateTimeFormat(language==='ja'?'ja-JP':'zh-CN',{timeZone:'Asia/Tokyo',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value));}
 const selectedDay=day||data?.day;
 const dateTitle=selectedDay?new Intl.DateTimeFormat(language==='ja'?'ja-JP':'zh-CN',{timeZone:'Asia/Tokyo',month:'long',day:'numeric',weekday:'short'}).format(new Date(selectedDay+'T12:00:00+09:00')):'—';
 if(mobile&&mine)return <section className={'attendance attendance-mobile attendance-personal'+(compact?' attendance-compact':'')} aria-label={t(compact?'今日出退勤':'出退勤记录')}>
  {!compact&&leader&&<nav className="attendance-tabs" aria-label={t('出退勤记录')}><a href="/my/attendance" aria-current="page">{t('我的记录')}</a><a href="/my/team-attendance">{t('本队动态')}</a></nav>}
  <header className="attendance-daybar">
   <div><h2>{isToday?t('今天'):dateTitle}{isToday&&<span>{dateTitle}</span>}</h2><small>{t('日本时间 · 每日05:00换日')}</small></div>
   {!compact&&<div className="attendance-header-actions"><ActionButton type="button" className="attendance-date-button" aria-haspopup="dialog" aria-expanded={dateOpen} onClick={()=>setDateOpen(true)}>{t('历史记录')}</ActionButton><ActionButton type="button" className="attendance-date-button" disabled={loading||!!busy} onClick={()=>{setMakeupError('');setMakeupOpen(true);}}>{t('申请补卡')}</ActionButton></div>}
  </header>
  {!compact&&DateSelector&&<DateSelector visible={dateOpen} value={selectedDay||new Date(Date.now()+4*3600_000).toISOString().slice(0,10)} language={language} onClose={()=>setDateOpen(false)} onConfirm={value=>{setDay(value);setPage(1);setDateOpen(false);}}/>}
  {!isToday&&!compact&&<nav className="attendance-history-nav" aria-label={t('历史记录')}><ActionButton type="button" onClick={()=>moveDay(-1)}>{t('前一天')}</ActionButton><ActionButton type="button" onClick={()=>{setDay('');setPage(1);}}>{t('返回今日打卡')}</ActionButton><ActionButton type="button" onClick={()=>moveDay(1)}>{t('后一天')}</ActionButton></nav>}
  <div className="attendance-clock-panel" aria-busy={loading}>
   <dl className="attendance-time-pair">{([['in',own.firstIn],['out',own.lastOut]] as const).map(([kind,event])=><div key={kind}>
    <dt>{t(kind==='in'?'出勤':'退勤')}<span>{t(kind==='in'?'最早出勤':'最晚退勤')}</span></dt>
    <dd>{!data?<span className="attendance-time-placeholder">—</span>:event?<time dateTime={event.at}>{clock(event.at)}</time>:<span className="attendance-time-placeholder">--:--</span>}</dd>
    <small>{!data?t(loading?'正在加载…':'暂无可用记录，请重试'):event?<><span>{at(event.at).split(' ')[0]}</span> · {t(event.source==='onsite'?'现场打卡':'补卡')}{event.source==='onsite'&&<a href={'https://www.google.com/maps?q='+event.latitude+','+event.longitude} target="_blank" rel="noopener noreferrer">{t('查看位置')}</a>}</>:t('未打卡')}</small>
   </div>)}</dl>
   {error&&<div role="alert" className="attendance-error attendance-inline-error">{error}{!retry&&<ActionButton type="button" disabled={!!busy} onClick={reload}>{t('重试')}</ActionButton>}</div>}
   {isToday&&(retry?<div className="attendance-retry"><p>{t('上次提交结果待确认，请先重试确认，避免重复打卡。')}</p><ActionButton className="attendance-primary" disabled={!!busy} onClick={()=>void punch(retry.kind,retry)}>{t(busy==='submit'?'提交中…':retry.kind==='in'?'重试上次出勤提交':'重试上次退勤提交')}</ActionButton><ActionButton disabled={!!busy} onClick={()=>{pendingPunch.current=null;setRetry(null);void punch(retry.kind);}}>{t('重新定位打卡')}</ActionButton></div>:<div className="attendance-punch-actions">
    <ActionButton type="button" className={!own.firstIn?'attendance-primary':''} disabled={!!busy||loading||!data} onClick={()=>void punch('in')}>{t('出勤打卡')}</ActionButton>
    <ActionButton type="button" className={own.firstIn?'attendance-primary':''} disabled={!!busy||loading||!data} onClick={()=>void punch('out')}>{t('退勤打卡')}</ActionButton>
   </div>)}
   {busy&&busy!=='makeup'&&<div className="attendance-progress" role="status"><strong>{t(busy==='location'?'正在获取位置':'正在保存打卡')}</strong><span>{t('请稍候，不要关闭页面')}</span></div>}
   {isToday&&!busy&&<p className="attendance-location-note">{t('定位成功后自动记录，无需再次确认。')}</p>}
  </div>
  {compact?<div className="attendance-links"><a href="/my/attendance">{t('查看记录与补卡')}</a>{leader&&<a href="/my/team-attendance">{t('查看本队动态')}</a>}</div>:<>
   {data&&activity(session.user.personCode!,true)}
   <details className="attendance-rules"><summary>{t('打卡规则')}</summary><p>{t('所有打卡都会保留，汇总取最早出勤、最晚退勤。')}</p><p>{t('仅打卡时获取位置，不限制地点，不持续追踪。')}</p></details>
   {Sheet&&<Sheet visible={makeupOpen} title={t('申请补卡')} busy={!!busy} onClose={()=>setMakeupOpen(false)}><div className="attendance attendance-mobile attendance-sheet-content">
    <p className="attendance-sheet-explanation">{t('补卡不含现场定位，须由管理员处理。')}</p><form className="attendance-makeup" onSubmit={e=>void makeup(e)}>
     <label>{t('补卡')}<UISelect name="kind"><option value="in">{t('出勤')}</option><option value="out">{t('退勤')}</option></UISelect></label>
     <label>{t('补卡时间（日本时间）')}<UIInput type="datetime-local" name="at" required/></label>
     <label>{t('补卡原因')}<UITextarea name="reason" required maxLength={1000} rows={3}/></label>
     {makeupError&&<p role="alert" className="attendance-error">{makeupError}</p>}
     <ActionButton type="submit" className="attendance-primary" disabled={!!busy}>{t(busy==='makeup'?'提交中…':'提交申请')}</ActionButton>
    </form></div></Sheet>}
  </>}
 </section>;
 return <section className={'attendance attendance-overview'+(mobile?' attendance-mobile':'')+(compact?' attendance-compact':'')} aria-label={t(compact?'今日出退勤':'出退勤记录')}>
  {notice&&<UISuccessNotice key={notice.id}>{notice.text}</UISuccessNotice>}
  {compact&&<div className="attendance-heading"><h2>{t('今日出退勤')}</h2><span>{data?.day??'—'}</span></div>}
  {!compact&&<>
   {leader&&<nav className="attendance-tabs" aria-label={t('出退勤记录')}><a href="/my/attendance" aria-current={mine?'page':undefined}>{t('我的记录')}</a><a href="/my/team-attendance" aria-current={!mine?'page':undefined}>{t('本队动态')}</a></nav>}
   <form className="attendance-filters" onSubmit={e=>{e.preventDefault();setSearch(q);setPage(1);reload();}}>
    <label>{t('业务日期')}{mobile&&DateSelector?<ActionButton type="button" aria-haspopup="dialog" aria-expanded={dateOpen} onClick={()=>setDateOpen(true)}>{day||data?.day||t('选择日期')}</ActionButton>:<UIInput type="date" value={day||data?.day||''} onChange={e=>{setDay(e.target.value);setPage(1);}}/>}</label>
    {!mine&&<><label>{t('团队')}<UISelect aria-label={t('团队')} value={team} onChange={e=>{setTeam(e.target.value);setPage(1);}}><option value="">{t('全部团队')}</option>{teams.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</UISelect></label><label>{t('姓名或编号')}<UIInput value={q} onChange={e=>setQ(e.target.value)} maxLength={100}/></label><ActionButton type="submit" disabled={loading}>{t('查询')}</ActionButton></>}
    <ActionButton type="button" onClick={reload} disabled={loading}>{t('刷新')}</ActionButton>
   </form>
  </>}
  {mobile&&DateSelector&&<DateSelector visible={dateOpen} value={day||data?.day||new Date(Date.now()+4*3600_000).toISOString().slice(0,10)} language={language} onClose={()=>setDateOpen(false)} onConfirm={value=>{setDay(value);setPage(1);setDateOpen(false);}}/>}
  <p className="attendance-muted">{t('出勤取最早，退勤取最晚；日本时间每天05:00换日。')}</p>
  {error&&<div role="alert" className="attendance-error">{error} {!retry&&<UIButton type="button" disabled={!!busy} onClick={reload}>{t('重试')}</UIButton>}</div>}
  {loading&&<p role="status">{t('正在加载…')}</p>}
  {mine&&<>
   <dl className="attendance-summary"><div><dt>{t('出勤')}</dt><dd>{summaryTime(own.firstIn)}</dd></div><div><dt>{t('退勤')}</dt><dd>{summaryTime(own.lastOut)}</dd></div></dl>
   {isToday?<div className="attendance-actions">
    {retry?<><p>{t('上次提交结果待确认，请先重试确认，避免重复打卡。')}</p><UIButton disabled={!!busy} onClick={()=>void punch(retry.kind,retry)}>{t(busy==='submit'?'提交中…':retry.kind==='in'?'重试上次出勤提交':'重试上次退勤提交')}</UIButton><UIButton disabled={!!busy} onClick={()=>{pendingPunch.current=null;setRetry(null);void punch(retry.kind);}}>{t('重新定位打卡')}</UIButton></>:<>
     <UIButton type="button" disabled={!!busy} onClick={()=>void punch('in')}>{t(busy==='location'?'定位中…':busy==='submit'?'提交中…':'出勤打卡')}</UIButton>
     <UIButton type="button" disabled={!!busy} onClick={()=>void punch('out')}>{t(busy==='location'?'定位中…':busy==='submit'?'提交中…':'退勤打卡')}</UIButton>
    </>}
   </div>:<UIButton type="button" onClick={()=>{setDay('');setPage(1);}}>{t('返回今日打卡')}</UIButton>}<small className="attendance-muted">{t('仅打卡时获取位置，不限制地点，不持续追踪。')}</small>
   {compact?<div className="attendance-links"><a href="/my/attendance">{t('查看记录与补卡')}</a>{leader&&<a href="/my/team-attendance">{t('查看本队动态')}</a>}</div>:<>
    {data&&activity(session.user.personCode!)}
    <details className="attendance-detail"><summary>{t('申请补卡')}</summary><p>{t('补卡不含现场定位，须由管理员处理。')}</p><form className="attendance-makeup" onSubmit={e=>void makeup(e)}>
     <label>{t('补卡')}<UISelect name="kind"><option value="in">{t('出勤')}</option><option value="out">{t('退勤')}</option></UISelect></label>
     <label>{t('补卡时间（日本时间）')}<UIInput type="datetime-local" name="at" required/></label>
     <label>{t('补卡原因')}<UITextarea name="reason" required maxLength={1000} rows={3}/></label>
     <UIButton disabled={!!busy}>{t(busy==='makeup'?'提交中…':'提交申请')}</UIButton>
    </form></details>
   </>}
  </>}
  {!mine&&data&&<>
   <div className="attendance-list-heading"><p>{data.day} · {data.total} {t('人')}</p><span>{t('本页')}：{t('仅有退勤，无出勤')} {data.people.filter(p=>attendanceSummary(data.events.filter(e=>e.code===p.code)).outWithoutIn).length} · {t('待审核补卡')} {data.requests.filter(r=>r.status==='pending').length}</span></div>
   {reviewError&&<p role="alert" className="attendance-error">{reviewError}</p>}
   {!data.people.length?<p>{t('暂无成员')}</p>:mobile?<div className="attendance-members">{data.people.map(person=>{const s=attendanceSummary(data.events.filter(e=>e.code===person.code));return <article key={person.code}><h2>{person.name} <small>{person.code} · {person.team}</small></h2>{recordStatus(person.code)}<dl className="attendance-summary"><div><dt>{t('出勤')}</dt><dd>{summaryTime(s.firstIn)}</dd></div><div><dt>{t('退勤')}</dt><dd>{summaryTime(s.lastOut)}</dd></div></dl>{activity(person.code)}</article>;})}</div>:<div className="attendance-table"><UITable><UITableHeader><UITableRow>{['姓名或编号','团队','出勤','退勤','状态','动态明细'].map(title=><UITableHead key={title}>{t(title)}</UITableHead>)}</UITableRow></UITableHeader><UITableBody>{data.people.map(person=>{const s=attendanceSummary(data.events.filter(e=>e.code===person.code));return <UITableRow key={person.code}><UITableCell><strong>{person.name}</strong><small>{person.code}</small></UITableCell><UITableCell>{person.team||'—'}</UITableCell><UITableCell>{summaryTime(s.firstIn)}</UITableCell><UITableCell>{summaryTime(s.lastOut)}</UITableCell><UITableCell>{recordStatus(person.code)}</UITableCell><UITableCell>{activity(person.code)}</UITableCell></UITableRow>;})}</UITableBody></UITable></div>}
   {data.total>data.pageSize&&<nav className="attendance-actions" aria-label={t('出退勤记录')}><UIButton disabled={page===1||loading} onClick={()=>setPage(v=>v-1)}>{t('上一页')}</UIButton><span>{page} / {Math.ceil(data.total/data.pageSize)}</span><UIButton disabled={page*data.pageSize>=data.total||loading} onClick={()=>setPage(v=>v+1)}>{t('下一页')}</UIButton></nav>}
  </>}
 </section>;
}
