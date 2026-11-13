import {OAuthAuthorize} from './OAuthAuthorize';
import '@fontsource-variable/geist';
import {ThemeProvider} from '@appica/ui-react/providers/theme-provider';
import {ToastProvider,Toaster} from '@appica/ui-react/toast';
import {ClientControlsProvider} from '../../../packages/client/src/ui-controls';
import {adminClientControls} from './client-controls';
import {AccountMenu} from './AccountMenu';
import {Badge} from '@appica/ui-react/badge';
import {Navigation,NavigationLink} from '@appica/ui-react/navigation';
import {Alert} from '@appica/ui-react/alert';
import {Form} from '@appica/ui-react/form';
import {Table,TableHeader,TableRow,TableHead,TableBody,TableCell} from '@appica/ui-react/table';

import {Input} from '@appica/ui-react/input';






import { Suspense, lazy, StrictMode, useEffect, useState, type FormEvent } from 'react';
import { createRoot } from 'react-dom/client';
import { Button } from '@appica/ui-react/button';
import { ScrollText, Globe2, LayoutDashboard, ClipboardList, LogOut, ArrowUpRight, LockKeyhole, ChevronRight, UsersRound, FileText, CalendarDays, Mail, WalletCards, FileCheck2, Scale, Settings, Store } from 'lucide-react';
import type { Session, Dashboard, BatchSummary } from '../../../packages/contracts/src';
import { api, ApiFailure, SESSION_EXPIRED_EVENT } from './api';
import './style.css';









import {AdminMessages} from './AdminMessages';
import { useAppLanguage } from '../../../packages/client/src/language';
const MerchantMap=lazy(()=>import('./MerchantMap').then(module=>({default:module.MerchantMap})));
const ZoneAssets=lazy(()=>import('./ZoneAssets').then(module=>({default:module.ZoneAssets})));
const BatchWork=lazy(()=>import('./BatchWork').then(module=>({default:module.BatchWork})));
const StationAssets=lazy(()=>import('./StationAssets').then(module=>({default:module.StationAssets})));
const StationTasks=lazy(()=>import('./StationTasks').then(module=>({default:module.StationTasks})));
const Teams=lazy(()=>import('./Teams').then(module=>({default:module.Teams})));
const OperationsLogs=lazy(()=>import('./OperationsLogs').then(module=>({default:module.OperationsLogs})));
const Employees=lazy(()=>import('./Employees').then(module=>({default:module.Employees})));
const DailyReports=lazy(()=>import('./DailyReports').then(module=>({default:module.DailyReports})));
const Plans=lazy(()=>import('./Plans').then(module=>({default:module.Plans})));
const Overview=lazy(()=>import('./Overview').then(module=>({default:module.Overview})));
const Payroll=lazy(()=>import('./Payroll').then(module=>({default:module.Payroll})));
const PayrollSettlement=lazy(()=>import('./PayrollSettlement').then(module=>({default:module.PayrollSettlement})));
const PerformanceReview=lazy(()=>import('./PerformanceReview').then(module=>({default:module.PerformanceReview})));
const Settlement=lazy(()=>import('./Settlement').then(module=>({default:module.Settlement})));
const Reconciliation=lazy(()=>import('./Reconciliation').then(module=>({default:module.Reconciliation})));
const Configuration=lazy(()=>import('./Configuration').then(module=>({default:module.Configuration})));
const Stores=lazy(()=>import('./Stores').then(module=>({default:module.Stores})));
const JA: Record<string, string> = {"结算与作业": "精算と業務", "管理工作台": "管理ワークスペース", "把每天的工作，": "毎日の業務を、", "理清楚。": "わかりやすく。", "从巡店任务到绩效结算，": "巡回タスクから実績精算まで、", "在同一个工作台掌握进展。": "ひとつの画面で進捗を確認。", "管理工作台 · 日本业务时间": "管理画面 · 日本時間", "设置你的新密码": "新しいパスワードを設定", "登录管理工作台": "管理画面にログイン", "初始密码需要更新后才能继续。": "初期パスワードを変更してください。", "使用现有管理员账号继续。": "既存の管理者アカウントをご利用ください。", "当前密码": "現在のパスワード", "新密码": "新しいパスワード", "至少 6 个字符": "6文字以上", "账号": "アカウント", "密码": "パスワード", "正在验证…": "確認中…", "更新密码并继续": "パスワードを変更", "登录": "ログイン", "前往员工端": "スタッフ画面へ", "请使用员工工作台": "スタッフ画面をご利用ください", "你的账号没有管理端访问权限。": "管理画面へのアクセス権限がありません。", "退出登录": "ログアウト", "数据看板": "業務概要", "巡店批次": "巡回バッチ", "业务工作台": "業務ワークスペース", "日本时间": "日本時間", "查看最新结算月与巡店作业进展。": "最新の精算月と巡回進捗を確認できます。", "按批次查看片区、网格和店铺完成情况。": "バッチごとのエリア・グリッド・店舗の完了状況。", "结算月": "精算月", "在岗员工": "在籍スタッフ", "有效巡店": "有効な巡回", "结算点数": "精算ポイント", "工资毛额": "給与総額", "人": "名", "家": "店舗", "点": "ポイント", "円": "円", "巡店作业": "巡回業務", "个批次": "バッチ", "正在读取数据…": "データを読み込み中…", "暂无可查看的巡店批次": "表示できる巡回バッチはありません", "批次建立后，会在这里显示作业进展。": "バッチ作成後に進捗が表示されます。", "批次名称": "バッチ名", "进度": "進捗", "片区 / 网格": "エリア / グリッド", "已完成店铺": "完了店舗", "状态": "状態", "批次": "バッチ", "待开始": "開始待ち", "进行中": "進行中", "已完成": "完了", "已停用": "停止中", "新版基础预览 · 已接入概览、批次摘要和员工状态，其他业务正在迁移。": "新バージョンのプレビュー · 現在は概要とバッチ進捗を表示しています。", "正在连接工作台…": "接続中…"};


const nf = new Intl.NumberFormat('zh-CN');
function App() {
  const t = (value: string) => language === 'ja' ? JA[value] ?? value : value;
  useEffect(() => { const onPop = () => {setPathname(location.pathname);setView(/^\/admin\/logs(?:\/|$)/.test(location.pathname) ? 'logs' : location.pathname.includes('/merchant-map') ? 'merchantMap' : location.pathname.includes('/zones') ? 'zones' : location.pathname.includes('/stations') ? 'assets' : location.pathname.includes('/station-tasks') ? 'station' : location.pathname.includes('/teams') ? 'teams' : location.pathname.includes('/stores') ? 'stores' : location.pathname.includes('/config') ? 'configuration' : location.pathname.includes('/recon') ? 'reconciliation' : location.pathname.includes('/files') ? 'settlement' : location.pathname.includes('payroll-settle') ? 'payrollSettlement' : location.pathname.includes('/perf') ? 'payroll' : location.pathname.includes('messages') ? 'messages' : location.pathname.includes('staff-reports/review') ? 'review' : location.pathname.includes('staff-plans') ? 'plans' : location.pathname.includes('staff-reports') ? 'reports' : location.pathname.includes('employees') ? 'employees' : location.pathname.includes('zone-tasks') || location.pathname.includes('batch-grids') ? 'tasks' : 'overview');}; window.addEventListener('popstate', onPop); return () => window.removeEventListener('popstate', onPop); }, []);

  const [pathname,setPathname]=useState(location.pathname);const [session, setSession] = useState<Session | null>(null);
  useEffect(() => { const expired=()=>{setSession(null);setDashboard(null);setBatches([]);};window.addEventListener(SESSION_EXPIRED_EVENT,expired);return()=>window.removeEventListener(SESSION_EXPIRED_EVENT,expired); }, []);
  const [language,setLanguage]=useAppLanguage(session?.user.languagePreference);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [unread, setUnread] = useState(0);

  const [pending, setPending] = useState(false);
  const [view, setView] = useState(/^\/admin\/logs(?:\/|$)/.test(location.pathname) ? 'logs' : location.pathname.includes('/merchant-map') ? 'merchantMap' : location.pathname.includes('/zones') ? 'zones' : location.pathname.includes('/stations') ? 'assets' : location.pathname.includes('/station-tasks') ? 'station' : location.pathname.includes('/teams') ? 'teams' : location.pathname.includes('/stores') ? 'stores' : location.pathname.includes('/config') ? 'configuration' : location.pathname.includes('/recon') ? 'reconciliation' : location.pathname.includes('/files') ? 'settlement' : location.pathname.includes('payroll-settle') ? 'payrollSettlement' : location.pathname.includes('/perf') ? 'payroll' : location.pathname.includes('messages') ? 'messages' : location.pathname.includes('staff-reports/review') ? 'review' : location.pathname.includes('staff-plans') ? 'plans' : location.pathname.includes('staff-reports') ? 'reports' : location.pathname.includes('employees') ? 'employees' : location.pathname.includes('zone-tasks') || location.pathname.includes('batch-grids') ? 'tasks' : 'overview');
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [batches, setBatches] = useState<BatchSummary[]>([]);
  useEffect(() => { if(session && !session.user.mustChangePassword) api<{unread:number}>('/messages?unread=true').then(value=>setUnread(value.unread)).catch(()=>{}); }, [session,view]);
  useEffect(() => { api<Session>('/auth/session').then(setSession).catch(e => { if (!(e instanceof ApiFailure && e.code === 'UNAUTHENTICATED')) setError(e.message); }).finally(() => setLoading(false)); }, []);
  useEffect(() => {
    if (!session || session.user.mustChangePassword || session.user.role !== 'admin' || view === 'employees' || view === 'reports' || view === 'review' || view === 'plans' || view === 'messages' || view === 'payroll' || view === 'settlement' || view === 'reconciliation' || view === 'configuration' || view === 'stores' || view === 'teams' || view === 'station' || view === 'assets') return;
    Promise.all([api<Dashboard>('/dashboard'), api<{ items: BatchSummary[] }>('/work/batches')])
      .then(([summary, tasks]) => { setDashboard(summary); setBatches(tasks.items); }).catch(e => setError(e.message));
  }, [session, view]);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(''); setPending(true);
    const values = Object.fromEntries(new FormData(event.currentTarget));
    try { setSession(await api<Session>(session ? '/auth/password' : '/auth/login', session, values)); }
    catch (e) { setError(e instanceof Error ? e.message : '连接失败，请重试'); }
    finally { setPending(false); }
  }
  async function logout() {
    try { await api('/auth/logout', session, {}); setSession(null); setDashboard(null); }
    catch (e) { setError((e as Error).message); }
  }
  if (loading) return <main className="loading" role="status">{t('正在连接工作台…')}</main>;
  if (!session || session.user.mustChangePassword) return <main className="login-layout">
    <section className="login-intro"><a href="/admin/" className="brand">巡店<span>{t('结算与作业')}</span></a><div><h1>{t('把每天的工作，')}<br/>{t('理清楚。')}</h1><p>{t('从巡店任务到绩效结算，')}<br/>{t('在同一个工作台掌握进展。')}</p></div><p className="intro-foot">{t('管理工作台 · 日本业务时间')}</p></section>
    <section className="login-form"><Button className="language-button" aria-label="中文 / 日本語" onClick={() => setLanguage(language === 'zh' ? 'ja' : 'zh')}>{language === 'zh' ? '日本語' : '中文'}</Button><LockKeyhole size={26}/><h2>{t(session ? '设置你的新密码' : '登录管理工作台')}</h2><p className="muted">{t(session ? '初始密码需要更新后才能继续。' : '使用现有管理员账号继续。')}</p>
      <Form onSubmit={submit}>
        {session ? <><label>{t('当前密码')}<Input name="currentPassword" type="password" autoComplete="current-password" required maxLength={512}/></label><label>{t('新密码')}<Input name="newPassword" aria-label={t('新密码')} type="password" autoComplete="new-password" required minLength={6} maxLength={128}/><span className="field-hint">{t('至少 6 个字符')}</span></label></> : <><label>{t('账号')}<Input name="username" autoComplete="username" required maxLength={128}/></label><label>{t('密码')}<Input name="password" type="password" autoComplete="current-password" required maxLength={512}/></label></>}
        {error && <Alert className="error" role="alert">{error}</Alert>}<Button className="primary-button" type="submit" disabled={pending}>{t(pending ? '正在验证…' : session ? '更新密码并继续' : '登录')}<ChevronRight size={18}/></Button>
      </Form><a className="secondary-link" href="/">{t('前往员工端')}<ArrowUpRight size={15}/></a>
    </section>
  </main>;
  if (session.user.role !== 'admin') return <main className="access-message"><h1>{t('请使用员工工作台')}</h1><p>{t('你的账号没有管理端访问权限。')}</p><a href="/">{t('前往员工端')}</a><Button onClick={logout}>{t('退出登录')}</Button></main>;
  function navigate(next: string) { setView(next); history.pushState({}, '', next === 'logs' ? '/admin/logs' : next === 'merchantMap' ? '/admin/merchant-map' : next === 'zones' ? '/admin/zones' : next === 'assets' ? '/admin/stations' : next === 'station' ? '/admin/station-tasks' : next === 'teams' ? '/admin/teams' : next === 'stores' ? '/admin/stores' : next === 'configuration' ? '/admin/config' : next === 'reconciliation' ? '/admin/recon' : next === 'settlement' ? '/admin/files' : next === 'payrollSettlement' ? '/admin/payroll-settle' : next === 'payroll' ? '/admin/perf' : next === 'messages' ? '/admin/messages' : next === 'review' ? '/admin/staff-reports/review' : next === 'plans' ? '/admin/staff-plans' : next === 'reports' ? '/admin/staff-reports' : next === 'employees' ? '/admin/employees' : next === 'tasks' ? '/admin/zone-tasks' : '/admin/');window.dispatchEvent(new PopStateEvent('popstate')); }
  const pageTitle = view==='review'?(language==='ja'?'進捗確認':'进展复核'):view==='payrollSettlement'?(language==='ja'?'給与調整':'薪资找平'):view==='logs'?(language==='ja'?'操作ログ':'日志'):view==='merchantMap'?(language==='ja'?'店舗分布地図':'商户分布地图'):view==='zones'?(language==='ja'?'地域管理':'片区管理'):view==='assets'?(language==='ja'?'駅・路線資産':'车站'):view === 'station' ? (language === 'ja' ? '駅タスク管理' : '任务') : view === 'teams' ? (language === 'ja' ? 'チーム管理' : '团队管理') : view === 'stores' ? (language === 'ja' ? '店舗マスター' : '店铺主档') : view === 'configuration' ? (language === 'ja' ? '設定と締め' : '系统配置') : view === 'reconciliation' ? (language === 'ja' ? '月次照合' : '月度对账') : view === 'settlement' ? (language === 'ja' ? '判定と入表' : '判定与入表') : view === 'payroll' ? (language === 'ja' ? '実績と支給' : '绩效与发薪') : view === 'messages' ? (language === 'ja' ? 'メッセージ' : '站内消息') : view === 'plans' ? (language === 'ja' ? '出勤予定' : '出勤计划') : view === 'reports' ? (language === 'ja' ? 'スタッフ日報' : '员工日报') : view === 'employees' ? (language === 'ja' ? 'スタッフ管理' : '员工管理') : view==='tasks'?t('巡店批次'):t(view==='overview'?'数据看板':'管理工作台');
  return <div className="admin-shell">
    <aside className="sidebar"><a href="/admin/" className="brand">巡店<span>{t('管理工作台')}</span></a><Navigation aria-label={language === 'ja' ? 'メインナビゲーション' : '主导航'}>
<section className="nav-group" aria-labelledby="nav-group-0"><h2 id="nav-group-0" className="nav-group-title">{language === 'ja' ? '概要' : '概览'}</h2><div className="nav-group-items">
<NavigationLink href="/admin/" value="overview" active={view==='overview'} onClick={event=>{if(event.button===0&&!event.metaKey&&!event.ctrlKey&&!event.shiftKey&&!event.altKey){event.preventDefault();navigate('overview');}}} orientation="vertical"><LayoutDashboard size={19}/>{t('数据看板')}</NavigationLink>
<NavigationLink href="/admin/messages" value="messages" active={view==='messages'} onClick={event=>{if(event.button===0&&!event.metaKey&&!event.ctrlKey&&!event.shiftKey&&!event.altKey){event.preventDefault();navigate('messages');}}} orientation="vertical"><Mail size={19}/>{language === 'ja' ? 'メッセージ' : '站内消息'}{unread>0&&<span className="nav-unread">{unread}</span>}</NavigationLink>
</div>
</section>
<section className="nav-group" aria-labelledby="nav-group-1"><h2 id="nav-group-1" className="nav-group-title">{language === 'ja' ? '精算' : '结算'}</h2><div className="nav-group-items">
<NavigationLink href="/admin/files" value="settlement" active={view==='settlement'} onClick={event=>{if(event.button===0&&!event.metaKey&&!event.ctrlKey&&!event.shiftKey&&!event.altKey){event.preventDefault();navigate('settlement');}}} orientation="vertical"><FileCheck2 size={19}/>{language === 'ja' ? '判定と入表' : '判定与入表'}</NavigationLink>
<NavigationLink href="/admin/perf" value="payroll" active={view==='payroll'} onClick={event=>{if(event.button===0&&!event.metaKey&&!event.ctrlKey&&!event.shiftKey&&!event.altKey){event.preventDefault();navigate('payroll');}}} orientation="vertical"><WalletCards size={19}/>{language === 'ja' ? '実績と支給' : '绩效与发薪'}</NavigationLink>
<NavigationLink href="/admin/payroll-settle" value="payrollSettlement" active={view==='payrollSettlement'} onClick={event=>{if(event.button===0&&!event.metaKey&&!event.ctrlKey&&!event.shiftKey&&!event.altKey){event.preventDefault();navigate('payrollSettlement');}}} orientation="vertical"><Scale size={19}/>{language==='ja'?'給与調整':'薪资找平'}</NavigationLink>
<NavigationLink href="/admin/recon" value="reconciliation" active={view==='reconciliation'} onClick={event=>{if(event.button===0&&!event.metaKey&&!event.ctrlKey&&!event.shiftKey&&!event.altKey){event.preventDefault();navigate('reconciliation');}}} orientation="vertical"><Scale size={19}/>{language === 'ja' ? '月次照合' : '月度对账'}</NavigationLink>
</div>
</section>
<section className="nav-group" aria-labelledby="nav-group-2"><h2 id="nav-group-2" className="nav-group-title">{language === 'ja' ? 'スタッフ' : '员工'}</h2><div className="nav-group-items">
<NavigationLink href="/admin/employees" value="employees" active={view==='employees'} onClick={event=>{if(event.button===0&&!event.metaKey&&!event.ctrlKey&&!event.shiftKey&&!event.altKey){event.preventDefault();navigate('employees');}}} orientation="vertical"><UsersRound size={19}/>{language === 'ja' ? 'スタッフ管理' : '员工管理'}</NavigationLink>
<NavigationLink href="/admin/staff-reports" value="reports" active={view==='reports'} onClick={event=>{if(event.button===0&&!event.metaKey&&!event.ctrlKey&&!event.shiftKey&&!event.altKey){event.preventDefault();navigate('reports');}}} orientation="vertical"><FileText size={19}/>{language === 'ja' ? 'スタッフ日報' : '员工日报'}</NavigationLink>
<NavigationLink href="/admin/staff-reports/review" value="review" active={view==='review'} onClick={event=>{if(event.button===0&&!event.metaKey&&!event.ctrlKey&&!event.shiftKey&&!event.altKey){event.preventDefault();navigate('review');}}} orientation="vertical"><FileCheck2 size={19}/>{language==='ja'?'進捗確認':'进展复核'}</NavigationLink>
<NavigationLink href="/admin/staff-plans" value="plans" active={view==='plans'} onClick={event=>{if(event.button===0&&!event.metaKey&&!event.ctrlKey&&!event.shiftKey&&!event.altKey){event.preventDefault();navigate('plans');}}} orientation="vertical"><CalendarDays size={19}/>{language === 'ja' ? '出勤予定' : '出勤计划'}</NavigationLink>
</div>
</section>
<section className="nav-group" aria-labelledby="nav-group-3"><h2 id="nav-group-3" className="nav-group-title">{language === 'ja' ? '業務' : '作业'}</h2><div className="nav-group-items">
<NavigationLink href="/admin/teams" value="teams" active={view==='teams'} onClick={event=>{if(event.button===0&&!event.metaKey&&!event.ctrlKey&&!event.shiftKey&&!event.altKey){event.preventDefault();navigate('teams');}}} orientation="vertical"><UsersRound size={19}/>{language === 'ja' ? 'チーム管理' : '团队管理'}</NavigationLink>
<NavigationLink href="/admin/zones" value="zones" active={view==='zones'} onClick={event=>{if(event.button===0&&!event.metaKey&&!event.ctrlKey&&!event.shiftKey&&!event.altKey){event.preventDefault();navigate('zones');}}} orientation="vertical"><Store size={19}/>{language==='ja'?'地域管理':'片区管理'}</NavigationLink>
<NavigationLink href="/admin/zone-tasks" value="tasks" active={view==='tasks'} onClick={event=>{if(event.button===0&&!event.metaKey&&!event.ctrlKey&&!event.shiftKey&&!event.altKey){event.preventDefault();navigate('tasks');}}} orientation="vertical"><ClipboardList size={19}/>{t('巡店批次')}</NavigationLink>
<NavigationLink href="/admin/merchant-map" value="merchantMap" active={view==='merchantMap'} onClick={event=>{if(event.button===0&&!event.metaKey&&!event.ctrlKey&&!event.shiftKey&&!event.altKey){event.preventDefault();navigate('merchantMap');}}} orientation="vertical"><Store size={19}/>{language==='ja'?'店舗分布地図':'商户分布地图'}</NavigationLink>
<NavigationLink href="/admin/logs" value="logs" active={view==='logs'} onClick={event=>{if(event.button===0&&!event.metaKey&&!event.ctrlKey&&!event.shiftKey&&!event.altKey){event.preventDefault();navigate('logs');}}} orientation="vertical"><ScrollText size={19}/>{language==='ja'?'操作ログ':'日志'}</NavigationLink>
</div>
<h3 className="nav-subgroup-title">{language === 'ja' ? '旧版業務' : '旧版作业'}</h3><div className="nav-group-items"><NavigationLink href="/admin/stations" value="assets" active={view==='assets'} onClick={event=>{if(event.button===0&&!event.metaKey&&!event.ctrlKey&&!event.shiftKey&&!event.altKey){event.preventDefault();navigate('assets');}}} orientation="vertical"><ClipboardList size={19}/>{language==='ja'?'駅・路線資産':'车站'}</NavigationLink><NavigationLink href="/admin/station-tasks" value="station" active={view==='station'} onClick={event=>{if(event.button===0&&!event.metaKey&&!event.ctrlKey&&!event.shiftKey&&!event.altKey){event.preventDefault();navigate('station');}}} orientation="vertical"><ClipboardList size={19}/>{language === 'ja' ? '駅タスク管理' : '任务'}</NavigationLink></div>
</section>
<section className="nav-group" aria-labelledby="nav-group-4"><h2 id="nav-group-4" className="nav-group-title">{language === 'ja' ? 'システム' : '系统'}</h2><div className="nav-group-items">

<NavigationLink href="/admin/stores" value="stores" active={view==='stores'} onClick={event=>{if(event.button===0&&!event.metaKey&&!event.ctrlKey&&!event.shiftKey&&!event.altKey){event.preventDefault();navigate('stores');}}} orientation="vertical"><Store size={19}/>{language === 'ja' ? '店舗マスター' : '店铺主档'}</NavigationLink>
<NavigationLink href="/admin/config" value="configuration" active={view==='configuration'} onClick={event=>{if(event.button===0&&!event.metaKey&&!event.ctrlKey&&!event.shiftKey&&!event.altKey){event.preventDefault();navigate('configuration');}}} orientation="vertical"><Settings size={19}/>{language === 'ja' ? '設定と締め' : '系统配置'}</NavigationLink>
</div>
</section>
</Navigation></aside>
    <main className="workspace"><header className="workspace-topbar"><h1>{pageTitle}</h1><div className="topbar-actions"><Button className="language-button" aria-label="中文 / 日本語" onClick={() => setLanguage(language === 'zh' ? 'ja' : 'zh')}><Globe2 size={16} aria-hidden="true"/>{language === 'zh' ? '日本語' : '中文'}</Button><AccountMenu session={session} language={language} onSession={setSession} onLogout={logout}/></div></header><Suspense fallback={<p role="status">{language==='ja'?'ページを読込中…':'正在读取页面…'}</p>}>

      {error && <Alert className="error" role="alert">{error}</Alert>}
      {view === 'overview'&&<Overview session={session} language={language} onSelected={row=>{if(row)setDashboard(current=>current?{...current,month:row.month,people:row.employees,records:row.records,points:row.points,salary:row.amount}:current);}}/>}
      {view==='logs'?<OperationsLogs language={language}/>:view==='merchantMap'?<MerchantMap session={session} language={language}/>:view==='zones'?<ZoneAssets session={session} language={language}/>:view==='assets'?<StationAssets session={session} language={language}/>:view === 'station' ? <StationTasks session={session} language={language}/> : view === 'teams' ? <Teams session={session} language={language}/> : view === 'employees' ? <Employees session={session} language={language}/> : view === 'reports' ? <DailyReports session={session} language={language}/> : view === 'review' ? <PerformanceReview session={session} language={language}/> : view === 'payrollSettlement' ? <PayrollSettlement session={session} language={language}/> : view === 'plans' ? <Plans language={language}/> : view === 'messages' ? <AdminMessages session={session} language={language} onUnread={setUnread}/> : view === 'payroll' ? <Payroll session={session} language={language}/> : view === 'settlement' ? <Settlement session={session} language={language}/> : view === 'reconciliation' ? <Reconciliation session={session} language={language}/> : view === 'configuration' ? <Configuration session={session} language={language}/> : view === 'stores' ? <Stores session={session} language={language}/> : view==='tasks'?<BatchWork session={session} language={language}/>:null}
      
    </Suspense></main></div>;
}
createRoot(document.getElementById('root')!).render(<StrictMode><ThemeProvider defaultTheme="system" storageKey="store-admin-theme" disableTransitionOnChange><ToastProvider><ClientControlsProvider components={adminClientControls}>{location.pathname==='/admin/mcp-authorize'?<OAuthAuthorize/>:<App/>}<Toaster position="top-right"/></ClientControlsProvider></ToastProvider></ThemeProvider></StrictMode>);
