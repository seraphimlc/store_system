import '@fontsource-variable/geist/index.css';
import {PageHeaderContext,type PageHeader} from './pageHeader';
import {ChevronLeft as LeftIcon} from 'lucide-react';
import {WorkRouter} from './work/WorkRouter';
import {MyStationTasks} from './MyStationTasks';
import { StrictMode, useEffect, useState, type FormEvent } from 'react';
import { createRoot } from 'react-dom/client';
import { Badge, Button, ConfigProvider, List, NavBar, Popup } from 'antd-mobile';
import zhCN from 'antd-mobile/es/locales/zh-CN';
import jaJP from 'antd-mobile/es/locales/ja-JP';
import { ClipboardList, UserRound, ChevronRight, Globe2, LogOut, LockKeyhole, FileText, CalendarDays, Mail, Menu, MapPinned } from 'lucide-react';
import type { Session } from '../../../packages/contracts/src';
import { api, ApiFailure, SESSION_EXPIRED_EVENT } from './api';
import './style.css';
import { MyDailyReport } from './MyDailyReport';
import { MyReportFeedback } from './MyReportFeedback';
import { MyPlan } from './MyPlan';
import { MyPerformance } from './MyPerformance';
import { MySettlement } from './MySettlement';
import { MessageCenter } from '../../../packages/client/src/MessageCenter';
import { useAppLanguage } from '../../../packages/client/src/language';
import './theme.css';
const ja: Record<string,string> = {
  '欢迎回来':'おかえりなさい','登录':'ログイン','账号':'アカウント','密码':'パスワード',
  '正在连接…':'接続中…','登录中…':'ログイン中…','巡店任务':'巡回タスク','我的账号':'マイアカウント','退出登录':'ログアウト',
  '设置新密码':'新しいパスワードを設定','初始密码需要更新后才能继续':'初期パスワードを変更してください','当前密码':'現在のパスワード','新密码':'新しいパスワード',
  '至少6个字符':'6文字以上','保存并继续':'保存して続ける','正在保存…':'保存中…','你好':'こんにちは','今天的工作，从这里开始。':'今日の仕事をここから。',
  '我的巡店批次':'担当する巡回バッチ','暂无分配的巡店任务':'割り当てられたタスクはありません','队长分配任务后，会显示在这里。':'タスクが割り当てられると、ここに表示されます。',
  '已完成':'完了','进行中':'進行中','待开始':'開始待ち','已停用':'停止中','个网格':'グリッド','家店铺':'店舗','片区':'エリア',
  '当前提供批次摘要，巡店操作将在后续阶段接入。':'現在は進捗を表示しています。巡回操作は次の段階で追加されます。','日本业务时间':'日本時間',
  '判定与申诉':'判定と申立','我的绩效':'自分の実績','站内消息':'メッセージ','出勤计划':'出勤予定','我的日报':'マイ日報','修改密码':'パスワード変更','关闭':'閉じる','管理员':'管理者','队长':'チームリーダー','员工':'スタッフ','人员编号':'スタッフ番号',
  '我的任务':'自分のタスク','任务分派':'タスク割当','每日填报':'日次報告','核对结果':'照合結果','菜单':'メニュー','返回':'戻る',
  '管理端入口':'管理画面へ','加载失败，请稍后重试':'読み込みに失敗しました。再度お試しください','重试':'再試行',
};
function tabForPath(path: string) {
  if (path.includes('/my/tasks') || /^\/tasks\/\d+/.test(path)) return 'station';
  if (path.includes('/confirm') || path.includes('/appeal')) return 'settlement';
  if (path.includes('/perf')) return 'performance';
  if (path.includes('messages')) return 'messages';
  if (path.includes('/plan')) return 'plan';
  if (path.includes('/report/feedback')) return 'feedback';
  if (path.includes('/report') || path.includes('/self-report')) return 'report';
  if (path.includes('/password')) return 'account';
  return 'tasks';
}
const tabPaths: Record<string, string> = { station: '/my/tasks', tasks: '/my/work', performance: '/my/perf', report: '/my/report', plan: '/my/plan', messages: '/messages', feedback: '/my/report/feedback', account: '/my/password', settlement: '/my/confirm' };
function App() {
  const t = (value: string) => language === 'ja' ? ja[value] ?? value : value;
  const [session, setSession] = useState<Session | null>(null);
  useEffect(() => { const expired=()=>{setSession(null);};window.addEventListener(SESSION_EXPIRED_EVENT,expired);return()=>window.removeEventListener(SESSION_EXPIRED_EVENT,expired); }, []);
  const [language,setLanguage]=useAppLanguage(session?.user.languagePreference);
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [tab, setTab] = useState(() => tabForPath(location.pathname));
  const [routePath, setRoutePath] = useState(location.pathname);
  const [pageHeader,setPageHeader]=useState<PageHeader|null>(null);
  const mapPage=tab==='tasks'&&/^\/my\/work\/(?:batch-grids|grids)\/\d+/.test(routePath);
  const batchPage=tab==='tasks'&&/^\/my\/work\/batches\/\d+\/?$/.test(routePath);
  const regionPage=tab==='tasks'&&/^\/my\/work\/(?:batches\/\d+\/)?regions\/\d+\/?$/.test(routePath);
  const [menuOpen, setMenuOpen] = useState(false);
  const [passwordOpen, setPasswordOpen] = useState(false);
  useEffect(() => { const onPop = () => {setTab(tabForPath(location.pathname));setRoutePath(location.pathname);setMenuOpen(false);}; window.addEventListener('popstate', onPop); return () => window.removeEventListener('popstate', onPop); }, []);
  const [unread, setUnread] = useState(0);
  const [planPending,setPlanPending]=useState<string|null>(null),[planDismissed,setPlanDismissed]=useState('');
  useEffect(()=>{if(!session?.user.personCode||session.user.mustChangePassword||!['staff','leader'].includes(session.user.role))return;let live=true;const read=()=>api<{pending:string|null}>('/plans/mine').then(v=>{if(live)setPlanPending(v.pending);}).catch(()=>{});void read();window.addEventListener('h5-plan-updated',read);return()=>{live=false;window.removeEventListener('h5-plan-updated',read);};},[session]);

  useEffect(() => { if(session && !session.user.mustChangePassword) api<{unread:number}>('/messages?unread=true').then(value=>setUnread(value.unread)).catch(()=>{}); }, [session,tab]);
  function applySession(value:Session){setSession(value);if(!value.user.mustChangePassword&&['/','/login'].includes(location.pathname)){const next='/my/work';setTab(tabForPath(next));history.replaceState({},'',next);setRoutePath(next);}}
  useEffect(() => { api<Session>('/auth/session').then(value => { applySession(value); }).catch(e => { if (!(e instanceof ApiFailure && e.code === 'UNAUTHENTICATED')) setError(e.message); }).finally(() => setLoading(false)); }, []);
  function navigate(key: string) {
    setTab(key); setMenuOpen(false); setError('');
    history.pushState({}, '', tabPaths[key]);
    setRoutePath(location.pathname);
    window.dispatchEvent(new PopStateEvent('popstate'));
    window.scrollTo({top: 0});
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setPending(true); setError('');
    const isPassword = !!session;
    const data = Object.fromEntries(new FormData(event.currentTarget));
    try { const value=await api<Session>(isPassword ? '/auth/password' : '/auth/login', session, data);if(!value.user.mustChangePassword){const next=isPassword&&!session?.user.mustChangePassword?location.pathname:'/my/work';history.replaceState({},'',next);setRoutePath(next);setTab(tabForPath(next));}applySession(value); setPasswordOpen(false); }
    catch (e) { setError(e instanceof Error ? e.message : t('加载失败，请稍后重试')); }
    finally { setPending(false); }
  }
  async function logout() {
    try { await api('/auth/logout', session, {}); setSession(null); setMenuOpen(false); }
    catch (e) { setError((e as Error).message); }
  }
  function passwordForm() { return <form onSubmit={submit} className="auth-form"><label>{t('当前密码')}<input name="currentPassword" type="password" autoComplete="current-password" required maxLength={512}/></label><label>{t('新密码')}<input name="newPassword" aria-label={t('新密码')} type="password" autoComplete="new-password" required minLength={6} maxLength={128}/><small>{t('至少6个字符')}</small></label>{error && <p className="error" role="alert">{error}</p>}<Button color="primary" block size="large" type="submit" loading={pending}>{t(pending ? '正在保存…' : '保存并继续')}</Button></form>; }
  const localeButton = <button className="language-button" aria-label="中文 / 日本語" onClick={() => setLanguage(language === 'zh' ? 'ja' : 'zh')}><Globe2 size={17}/>{language === 'zh' ? '日本語' : '中文'}</button>;
  if (loading) return <div className="mobile-loading" role="status">{t('正在连接…')}</div>;
  return <ConfigProvider locale={language === 'ja' ? jaJP : zhCN}>
    {!session || session.user.mustChangePassword ? <main className="auth-page"><header className="auth-header"><span className="wordmark">巡店</span>{localeButton}</header><section className="auth-content">{session && <LockKeyhole size={28} className="auth-icon"/>}<h1>{t(session ? '设置新密码' : '欢迎回来')}</h1>{session && <p className="muted">{t('初始密码需要更新后才能继续')}</p>}{session ? passwordForm() : <form onSubmit={submit} className="auth-form"><label>{t('账号')}<input name="username" autoComplete="username" autoCapitalize="none" required maxLength={128}/></label><label>{t('密码')}<input name="password" type="password" autoComplete="current-password" required maxLength={512}/></label>{error && <p className="error" role="alert">{error}</p>}<Button color="primary" block size="large" type="submit" loading={pending}>{t(pending ? '登录中…' : '登录')}</Button></form>}</section><footer className="auth-footer">{t('日本业务时间')} · JST</footer></main> : <div className={"staff-shell"+((mapPage||batchPage||regionPage)?" is-grid-screen":"")}>
      <NavBar backIcon={pageHeader?.back||['messages','feedback','account','settlement'].includes(tab)?<LeftIcon size={22} aria-label={t('返回')}/>:false} onBack={() => {const path=pageHeader?.back||'/my/work';history.pushState({},'',path);window.dispatchEvent(new PopStateEvent('popstate'));window.scrollTo({top:0});}} right={<button className="staff-menu-trigger" type="button" aria-label={t('菜单')} aria-expanded={menuOpen} aria-controls="staff-menu" onClick={() => setMenuOpen(true)}><Menu size={22}/>{unread > 0 && <span className="menu-unread" aria-label={t('站内消息') + ': ' + unread}>{unread}</span>}</button>}>
        <h1>{pageHeader?.title||t(tab === 'station' ? session.user.role === 'leader' ? '任务分派' : '我的任务' : tab === 'tasks' ? '巡店任务' : tab === 'report' ? '每日填报' : tab === 'plan' ? '出勤计划' : tab === 'messages' ? '站内消息' : tab === 'feedback' ? '核对结果' : tab === 'performance' ? '我的绩效' : tab === 'settlement' ? '判定与申诉' : '我的账号')}</h1>
      </NavBar>
      <PageHeaderContext.Provider value={setPageHeader}><main className={"staff-content"+(mapPage?" staff-content-map":batchPage?" staff-content-batch":regionPage?" staff-content-region":"")}>
        {planPending&&tab==='tasks'&&['/','/my/work','/my/work/'].includes(routePath)&&planDismissed!==planPending&&<aside className="h5-plan-pending" role="status"><p>{language==='ja'?'出勤予定が未登録です':'出勤计划待登记'} · {planPending}</p><div><Button size="small" onClick={()=>navigate('plan')}>{language==='ja'?'登録する':'去登记'}</Button><Button size="small" fill="none" onClick={()=>setPlanDismissed(planPending)}>{language==='ja'?'後で':'稍后再说'}</Button></div></aside>}

        {tab === 'station' ? <MyStationTasks session={session} language={language}/> : tab === 'tasks' ? <WorkRouter path={routePath} session={session} language={language}/> : tab === 'report' ? <MyDailyReport session={session} language={language}/> : tab === 'feedback' ? <MyReportFeedback language={language}/> : tab === 'plan' ? <MyPlan session={session} language={language}/> : tab === 'messages' ? <MessageCenter mobile session={session} language={language} request={api} onUnread={setUnread}/> : tab === 'performance' ? <MyPerformance language={language}/> : tab === 'settlement' ? <MySettlement session={session} language={language}/> : <>
          <section className="account-heading"><UserRound size={32}/><h2>{session.user.displayName}</h2><p>{t(({ admin: '管理员', leader: '队长', staff: '员工' })[session.user.role])} · {session.user.username}</p></section>
          <List><List.Item onClick={() => navigate('settlement')} arrowIcon={<ChevronRight size={18}/>}>{t('判定与申诉')}</List.Item><List.Item extra={session.user.personCode || '—'}>{t('人员编号')}</List.Item><List.Item onClick={() => { setError(''); setPasswordOpen(true); }} arrowIcon={<ChevronRight size={18}/>}>{t('修改密码')}</List.Item>{session.user.role === 'admin' && <List.Item onClick={() => location.assign('/admin/')} arrowIcon={<ChevronRight size={18}/>}>{t('管理端入口')}</List.Item>}</List>
          <Button className="logout-button" fill="outline" block onClick={logout}><span className="h5-button-content"><LogOut size={16}/>{t('退出登录')}</span></Button>{error && <p className="error" role="alert">{error}</p>}
        </>}
      </main></PageHeaderContext.Provider>
      <Popup visible={menuOpen} position="right" onMaskClick={() => setMenuOpen(false)} bodyStyle={{width:'min(320px, 88vw)'}}>
        <section id="staff-menu" className="staff-menu" aria-label={t('菜单')}>
          <div className="sheet-title"><h2>{session.user.displayName}</h2><Button fill="none" onClick={() => setMenuOpen(false)}>{t('关闭')}</Button></div>
          <List>
            <List.Item prefix={<MapPinned size={20}/>} onClick={()=>navigate('tasks')}>{t('巡店任务')}</List.Item>
            <List.Item prefix={<ClipboardList size={20}/>} onClick={()=>navigate('station')}>{t(session.user.role==='leader'?'任务分派':'我的任务')}</List.Item>
            <List.Item prefix={<FileText size={20}/>} onClick={()=>navigate('report')}>{t('每日填报')}</List.Item>
            <List.Item prefix={<CalendarDays size={20}/>} extra={planPending?<Badge content={Badge.dot}/>:undefined} onClick={()=>navigate('plan')}>{t('出勤计划')}</List.Item>
            <List.Item prefix={<Mail size={20}/>} extra={unread || undefined} onClick={() => navigate('messages')}>{t('站内消息')}</List.Item><List.Item prefix={<ClipboardList size={20}/>} onClick={() => navigate('feedback')}>{t('核对结果')}</List.Item><List.Item prefix={<LockKeyhole size={20}/>} onClick={() => {navigate('account');setPasswordOpen(true);}}>{t('修改密码')}</List.Item><List.Item prefix={<UserRound size={20}/>} onClick={() => navigate('account')}>{t('我的账号')}</List.Item></List>
          {localeButton}<Button block fill="outline" onClick={logout}><span className="h5-button-content"><LogOut size={18}/>{t('退出登录')}</span></Button>{error && <p className="error" role="alert">{error}</p>}
        </section>
      </Popup>
      <Popup visible={passwordOpen} onMaskClick={() => setPasswordOpen(false)} bodyStyle={{ borderTopLeftRadius: 16, borderTopRightRadius: 16 }}><section className="password-sheet"><div className="sheet-title"><h2>{t('修改密码')}</h2><Button size="small" fill="none" onClick={() => setPasswordOpen(false)}>{t('关闭')}</Button></div>{passwordForm()}</section></Popup>
    </div>}
  </ConfigProvider>;
}
createRoot(document.getElementById('root')!).render(<StrictMode><App/></StrictMode>);
