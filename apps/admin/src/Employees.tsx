import {OAuthClients} from './OAuthClients';
import {McpCredentials} from './McpCredentials';
import {SuccessNotice} from './SuccessNotice';
import {AdminPagination} from './AdminPagination';
import {Dialog,DialogContent,DialogTitle} from '@appica/ui-react/dialog';
import {Alert} from '@appica/ui-react/alert';
import {Form} from '@appica/ui-react/form';
import {AdminSelect} from './AdminSelect';
import {Table,TableHeader,TableRow,TableHead,TableBody,TableCell} from '@appica/ui-react/table';
import {Input} from '@appica/ui-react/input';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Button } from '@appica/ui-react/button';
import { Download, Plus, Search } from 'lucide-react';
import type { Employee, EmployeePage, Session } from '../../../packages/contracts/src';
import { api } from './api';

const statusLabels = { active: '在岗', leave: '请假', disabled: '停用', resigned: '离职' };
const ja: Record<string, string> = {
  '删除':'削除','删除员工':'スタッフを削除','确认删除':'削除する','正在删除…':'削除中…','员工已删除':'スタッフを削除しました',
  '确认删除此员工？删除后账号立即停用，员工不再显示在列表和账号导出中，历史业务记录保留。':'このスタッフを削除しますか？アカウントは直ちに無効になり、一覧とアカウント出力から除外されます。過去の業務記録は保持されます。',
  '搜索姓名、账号或人员编号':'氏名・アカウント・番号を検索','搜索':'検索','全部状态':'すべての状態',
  '在岗':'在籍','请假':'休暇','停用':'無効','离职':'退職','姓名 / 账号':'氏名 / アカウント',
  '人员编号':'スタッフ番号','账号角色':'アカウント権限','状态':'状態','操作':'操作','队长端权限':'リーダー権限',
  '员工端权限':'スタッフ権限','编辑':'編集','重置口令':'パスワード再設定','没有符合条件的员工':'該当するスタッフがいません',
  '正在读取员工…':'読み込み中…','上一页':'前へ','下一页':'次へ','取消':'キャンセル','保存':'保存',
  '正在保存…':'保存中…','已保存员工资料':'スタッフ情報を保存しました','新建员工':'スタッフ追加','编辑员工':'スタッフ編集',
  '姓名':'氏名','登录名':'ログイン名','初始口令':'初期パスワード','留空生成独立临时口令':'空欄で個別の一時パスワードを生成',
  '生成登录名':'ログイン名を生成','语言':'言語','自动':'自動','中文':'中国語','队员':'メンバー','队长':'リーダー',
  '人员编号创建后不能修改。':'スタッフ番号は作成後に変更できません。','账号角色决定端权限，团队职务由团队管理维护。':'アカウント権限とチーム内役割は別に管理されます。',
  '请假可登录；停用或离职后不能登录，历史数据始终保留。':'休暇中もログインできます。無効・退職時はログインできません。履歴は保持されます。',
  '新口令':'新しいパスワード','至少6位；重置后旧会话失效，下次登录必须改密。':'6文字以上。再設定すると既存セッションは失効し、次回ログイン時に変更が必要です。',
  '导出账号':'アカウント出力','账号清单仅向管理员提供，请妥善交付。':'アカウント一覧は管理者専用です。適切に配布してください。',
};
type Mode = 'create' | 'edit' | 'reset' | 'delete';
interface Editor { mode: Mode; employee?: Employee }

export function Employees({ session, language }: { session: Session; language: string }) {
  const t = (key: string) => language === 'ja' ? ja[key] ?? key : key;
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('');
  const [data, setData] = useState<EmployeePage | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [credentialUser,setCredentialUser]=useState<Employee|null>(null);
  const [temporaryCredential,setTemporaryCredential]=useState<{username:string;temporaryPassword:string}|null>(null);
  
  const form = useRef<HTMLFormElement>(null);
  const writing = useRef(false);
  const requestGeneration = useRef(0);
  async function load() {
    const generation = ++requestGeneration.current;
    setLoading(true); setError('');
    try {
      const query = new URLSearchParams({ page: String(page), search });
      if (status) query.set('status', status);
      const result = await api<EmployeePage>('/employees?' + query);
      if (generation === requestGeneration.current) setData(result);
    } catch (error) { if (generation === requestGeneration.current) setError((error as Error).message); }
    finally { if (generation === requestGeneration.current) setLoading(false); }
  }
  useEffect(() => { void load(); return () => { requestGeneration.current++; }; }, [page, search, status]);
  
  function open(mode: Mode, employee?: Employee) { setError(''); setEditor({ mode, employee }); }
  function filter(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const values = new FormData(event.currentTarget);
    setSearch(String(values.get('search') ?? '')); setStatus(String(values.get('status') ?? '')); setPage(1);
  }
  async function suggest() {
    if (!form.current || writing.current) return;
    const values = new FormData(form.current);
    try {
      const result = await api<{ username: string }>('/employees/suggest-username', session, { name: values.get('name'), code: values.get('code') });
      const input = form.current.elements.namedItem('username') as HTMLInputElement;
      input.value = result.username; setError('');
    } catch (error) { setError((error as Error).message); }
  }
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!editor || writing.current) return;
    writing.current = true; setSaving(true); setError('');
    const values = Object.fromEntries(new FormData(event.currentTarget));
    try {
      if (editor.mode === 'create') setTemporaryCredential(await api<{username:string;temporaryPassword:string}>('/employees/create', session, values));
      if (editor.mode === 'edit') {
        const employee = editor.employee!;
        await api(`/employees/${employee.id}/edit`, session, { ...values,
          expected: { username: employee.username, name: employee.displayName, status: employee.status, language: employee.language || '', role: employee.role } });
      }
      if (editor.mode === 'reset') setTemporaryCredential(await api<{username:string;temporaryPassword:string}>(`/employees/${editor.employee!.id}/reset`, session, values));

      if (editor.mode === 'delete') await api(`/employees/${editor.employee!.id}/delete`, session, {confirm:'DELETE_EMPLOYEE',expectedStatus:editor.employee!.status});
      setEditor(null); setNotice(t(editor.mode === 'delete' ? '员工已删除' : '已保存员工资料'));
      if (editor.mode === 'delete' && page > 1 && data?.items.length === 1) setPage(page - 1); else await load();
    } catch (error) { setError((error as Error).message); }
    finally { writing.current = false; setSaving(false); }
  }
  const employee = editor?.employee;
  const isAccount = editor?.mode === 'create' || editor?.mode === 'edit';
  const title = editor?.mode === 'delete' ? '删除员工' : editor?.mode === 'create' ? '新建员工' : editor?.mode === 'edit' ? '编辑员工' : '重置口令';
  return <section className="employee-section">
    <OAuthClients session={session} language={language}/>
    <Dialog open={!!temporaryCredential} onOpenChange={open=>{if(!open)setTemporaryCredential(null);}}><DialogContent>
      <DialogTitle>{language==='ja'?'一時パスワードを安全に渡す':'请安全交付临时口令'}</DialogTitle>
      <p>{language==='ja'?'この画面を閉じると再表示できません。初回ログイン時に変更が必要です。':'关闭后无法再次查看。请单独交付给本人，首次登录必须修改。'}</p>
      <p>{temporaryCredential?.username}</p><code>{temporaryCredential?.temporaryPassword}</code>
      <Button onClick={()=>setTemporaryCredential(null)}>{language==='ja'?'保存しました':'已记录，关闭'}</Button>
    </DialogContent></Dialog>
    {credentialUser&&<McpCredentials session={session} userId={credentialUser.id} username={credentialUser.username} language={language} onClose={()=>setCredentialUser(null)}/>}
    
    <Form className="employee-filters admin-filter-panel" onSubmit={filter}>
      <label className="search-field admin-search-field"><Search size={17} aria-hidden="true"/><Input name="search" aria-label={t('搜索姓名、账号或人员编号')} placeholder={t('搜索姓名、账号或人员编号')} maxLength={80}/></label>
      <AdminSelect name="status" aria-label={t('状态')}><option value="">{t('全部状态')}</option>{Object.entries(statusLabels).map(([key,label]) => <option value={key} key={key}>{t(label)}</option>)}</AdminSelect>
      
    <div className="admin-filter-submit"><Button type="submit">{t('搜索')}</Button>
      <Button onClick={() => open('create')}><Plus size={16}/>{t('新建员工')}</Button>
      <a className="toolbar-download" href="/api/employees/accounts-export" download><Download size={16}/>{t('导出账号')}</a>

    </div></Form>
    {notice && <SuccessNotice className="success-notice" role="status">{notice}</SuccessNotice>}
    {error && !editor && <Alert className="error" role="alert">{error}</Alert>}
    <div className="employee-result-count">{language === 'ja' ? `${data?.total ?? 0} 名` : `共 ${data?.total ?? 0} 位员工`}</div>
    {loading ? <p role="status">{t('正在读取员工…')}</p> : !data?.items.length ? <div className="empty-state"><h3>{t('没有符合条件的员工')}</h3></div> :
      <div className="table-scroll"><Table><TableHeader><TableRow>{['姓名 / 账号','人员编号','账号角色','状态','操作'].map(label => <TableHead key={label}>{t(label)}</TableHead>)}</TableRow></TableHeader><TableBody>{data.items.map(employee =>
        <TableRow key={employee.id}><TableCell><strong>{employee.displayName || employee.username}</strong><span className="row-id">{employee.username}</span></TableCell><TableCell>{employee.personCode || '—'}</TableCell><TableCell>{t(employee.role === 'leader' ? '队长端权限' : '员工端权限')}</TableCell><TableCell><span className={'status-label state-' + employee.status}>{t(statusLabels[employee.status])}</span></TableCell><TableCell><div className="row-actions"><Button variant="outline" onClick={() => open('edit', employee)}>{t('编辑')}</Button><Button variant="outline" onClick={() => open('reset', employee)}>{t('重置口令')}</Button><Button variant="outline" onClick={()=>setCredentialUser(employee)}>{language==='ja'?'MCP 認証情報':'MCP 凭据'}</Button><Button variant="outline" onClick={() => open('delete', employee)}>{t('删除')}</Button></div></TableCell></TableRow>)}</TableBody></Table></div>}
    <AdminPagination className="pagination"><Button variant="outline" disabled={loading || page <= 1} onClick={() => setPage(page - 1)}>{t('上一页')}</Button><span>{page} / {Math.max(1, Math.ceil((data?.total ?? 0) / 30))}</span><Button variant="outline" disabled={loading || page * 30 >= (data?.total ?? 0)} onClick={() => setPage(page + 1)}>{t('下一页')}</Button></AdminPagination>
    <p className="muted">{t('账号清单仅向管理员提供，请妥善交付。')}</p>
    <Dialog open={!!editor} onOpenChange={open=>{if(!open&&!saving)setEditor(null);}}><DialogContent className="management-dialog employee-editor-dialog" frame={false} closeButton={false}>
      {editor && <Form className="employee-editor-form" ref={form} key={`${editor.mode}-${employee?.id ?? ''}`} onSubmit={save}>
        <DialogTitle className="full-width" id="employee-editor-title">{t(title)}</DialogTitle>
        {employee && <p className="full-width">{employee.displayName} · {employee.username}<br/>{t('人员编号')}：{employee.personCode || '—'}</p>}
        {isAccount && <>
          {editor.mode === 'create' && <label>{t('人员编号')}<Input name="code" maxLength={32} required autoComplete="off"/></label>}
          <label>{t('姓名')}<Input name="name" defaultValue={employee?.displayName} maxLength={128} required/></label>
          <div className="employee-login-field full-width"><label>{t('登录名')}<Input name="username" defaultValue={employee?.username} maxLength={64} required={editor.mode === 'edit'} autoCapitalize="none" autoComplete="off"/></label>{editor.mode === 'create' && <Button variant="outline" type="button" onClick={suggest}>{t('生成登录名')}</Button>}</div>
          <label>{t('账号角色')}<AdminSelect name="role" defaultValue={employee?.role ?? 'staff'}><option value="staff">{t('队员')}</option><option value="leader">{t('队长')}</option></AdminSelect></label>
          <p className="muted full-width">{t('账号角色决定端权限，团队职务由团队管理维护。')}</p>
          {editor.mode === 'create' ? <label>{t('初始口令')}<Input name="password" aria-label={t('初始口令')} type="password" minLength={6} maxLength={128} autoComplete="new-password"/><small>{t('留空生成独立临时口令')}</small></label> : <>
            <label>{t('状态')}<AdminSelect name="status" defaultValue={employee!.status}>{Object.entries(statusLabels).map(([key,label]) => <option value={key} key={key}>{t(label)}</option>)}</AdminSelect></label>
            <label>{t('语言')}<AdminSelect name="language" defaultValue={employee?.language || ''}><option value="">{t('自动')}</option><option value="zh">{t('中文')}</option><option value="ja">日本語</option></AdminSelect></label>
          </>}
          <p className="muted full-width">{t('人员编号创建后不能修改。')}{t('请假可登录；停用或离职后不能登录，历史数据始终保留。')}</p>
        </>}
        {editor.mode === 'delete' && <Alert className="full-width" role="alert">{t('确认删除此员工？删除后账号立即停用，员工不再显示在列表和账号导出中，历史业务记录保留。')}</Alert>}
        {!isAccount && editor.mode !== 'delete' && <><label>{t('新口令')}<Input name="password" type="password" minLength={6} maxLength={128} autoComplete="new-password"/></label><p className="muted full-width">{t('留空生成独立临时口令')}。{t('至少6位；重置后旧会话失效，下次登录必须改密。')}</p></>}
        {error && <Alert className="error full-width" role="alert">{error}</Alert>}
        <div className="dialog-actions"><Button variant="outline" type="button" disabled={saving} onClick={() => setEditor(null)}>{t('取消')}</Button><Button type="submit" disabled={saving}>{t(editor.mode === 'delete' ? (saving ? '正在删除…' : '确认删除') : (saving ? '正在保存…' : '保存'))}</Button></div>
      </Form>}
    </DialogContent></Dialog>
  </section>;
}
