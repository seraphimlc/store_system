import {useEffect,useMemo,useRef,useState,type FormEvent} from 'react';
import {Button} from '@appica/ui-react/button';
import {Input} from '@appica/ui-react/input';
import {Form} from '@appica/ui-react/form';
import {Alert} from '@appica/ui-react/alert';
import {ShieldCheck,ArrowLeft} from 'lucide-react';
import type {Session} from '../../../packages/contracts/src';
import {api,ApiFailure} from './api';
import {useAppLanguage} from '../../../packages/client/src/language';
type Consent={client:{name:string;redirectUri:string};scope:string[];user:{username:string;displayName:string;role:string};resource:string};
export function OAuthAuthorize(){
 const [language,setLanguage]=useAppLanguage(),t=(zh:string,ja:string)=>language==='ja'?ja:zh;
 const [session,setSession]=useState<Session|null>(null),[loading,setLoading]=useState(true),[consent,setConsent]=useState<Consent|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);const writing=useRef(false);
 const request=useMemo(()=>{const params=new URLSearchParams(location.search),data:Record<string,string>={};let duplicate=false;for(const [key,value]of params){if(Object.hasOwn(data,key))duplicate=true;data[key]=value;}return {data,duplicate};},[]);
 useEffect(()=>{let alive=true;void api<Session>('/auth/session').then(value=>{if(alive)setSession(value);}).catch(cause=>{if(alive&&!(cause instanceof ApiFailure&&cause.code==='UNAUTHENTICATED'))setError(cause.message);}).finally(()=>{if(alive)setLoading(false);});return()=>{alive=false;};},[]);
 useEffect(()=>{if(!session||session.user.mustChangePassword)return;let alive=true;setConsent(null);setError('');if(request.duplicate){setError(t('授权参数重复，请从客户端重新连接。','認可パラメータが重複しています。クライアントから接続し直してください。'));return;}
  void api<Consent>('/mcp-oauth/context?'+new URLSearchParams(request.data)).then(value=>{if(alive)setConsent(value);}).catch(cause=>{if(alive)setError(cause.message);});return()=>{alive=false;};},[session,request,language]);
 async function login(event:FormEvent<HTMLFormElement>){event.preventDefault();if(writing.current)return;writing.current=true;setBusy(true);setError('');const values=Object.fromEntries(new FormData(event.currentTarget));try{setSession(await api<Session>(session?'/auth/password':'/auth/login',session,values));}catch(cause){setError((cause as Error).message);}finally{writing.current=false;setBusy(false);}}
 async function decide(approved:boolean){if(!session||!consent||writing.current)return;writing.current=true;setBusy(true);setError('');try{const value=await api<{redirect:string}>('/mcp-oauth/authorize',session,{request:request.data,approved});
   // Server validates the exact registered callback again; never navigate to a URL from query parameters.
   location.assign(value.redirect);
  }catch(cause){setError((cause as Error).message);writing.current=false;setBusy(false);}}
 async function switchAccount(){if(!session||writing.current)return;writing.current=true;setBusy(true);try{await api('/auth/logout',session,{});setSession(null);setConsent(null);}catch(cause){setError((cause as Error).message);}finally{writing.current=false;setBusy(false);}}
 return <main className="oauth-page"><section className="oauth-panel"><header className="oauth-header"><a href="/admin/" className="oauth-brand">{t('巡店系统','巡回システム')}</a><Button variant="outline" className="language-button" onClick={()=>setLanguage(language==='zh'?'ja':'zh')} aria-label="中文 / 日本語">{language==='zh'?'日本語':'中文'}</Button></header>
  <ShieldCheck aria-hidden="true" size={28}/><h1>{t('连接巡店系统','巡回システムに接続')}</h1>
  {loading?<p role="status">{t('正在检查登录状态…','ログイン状態を確認中…')}</p>:!session||session.user.mustChangePassword?<>
   <p>{session?t('先更新初始密码，再确认本次授权。','初期パスワードを更新してから認可を確認してください。'):t('使用你的巡店账号登录，然后确认客户端可访问的内容。','巡回アカウントでログインし、クライアントのアクセス範囲を確認してください。')}</p>
   <Form onSubmit={login} className="oauth-form">{session?<><label>{t('当前密码','現在のパスワード')}<Input name="currentPassword" type="password" required autoComplete="current-password" maxLength={512}/></label><label>{t('新密码','新しいパスワード')}<Input name="newPassword" type="password" required autoComplete="new-password" minLength={6} maxLength={128}/></label></>:<><label>{t('账号','アカウント')}<Input name="username" autoComplete="username" required maxLength={128}/></label><label>{t('密码','パスワード')}<Input name="password" type="password" required autoComplete="current-password" maxLength={512}/></label></>}
   <Button type="submit" disabled={busy}>{busy?t('正在验证…','確認中…'):session?t('更新密码并继续','パスワードを更新して続行'):t('登录并继续','ログインして続行')}</Button></Form>
  </>:consent?<>
   <p>{t('当前账号','現在のアカウント')}：<strong>{consent.user.displayName||consent.user.username}</strong> <span className="muted">({consent.user.username})</span></p>
   <dl className="oauth-details"><div><dt>{t('请求连接的客户端','接続を要求しているクライアント')}</dt><dd>{consent.client.name}</dd></div><div><dt>{t('授权后返回','認可後の移動先')}</dt><dd>{consent.client.redirectUri}</dd></div><div><dt>{t('访问系统','アクセス先')}</dt><dd>{consent.resource}</dd></div></dl>
   <div className="oauth-scope"><h2>{t('允许的访问','許可するアクセス')}</h2><ul><li>{t('读取账号权限范围内的巡店与结算数据。','アカウント権限内の巡回・精算データを読み取ります。')}</li>{consent.scope.includes('write')&&<li>{t('可提交填报或执行业务操作。发薪、配置等仍受角色和确认规则限制。','報告の送信や業務操作が可能です。給与・設定などは役割と確認ルールで制限されます。')}</li>}</ul><p className="muted">{t('客户端不会获得超出当前账号的权限。','現在のアカウントを超える権限は付与されません。')}</p></div>
   <div className="oauth-actions"><Button disabled={busy} onClick={()=>decide(true)}>{busy?t('正在处理…','処理中…'):t('允许连接','接続を許可')}</Button><Button variant="outline" disabled={busy} onClick={()=>decide(false)}>{t('拒绝','拒否')}</Button></div><Button variant="outline" disabled={busy} onClick={switchAccount}>{t('切换账号','アカウントを切替')}</Button>
  </>:!error?<p role="status">{t('正在读取授权信息…','認可情報を読み込み中…')}</p>:null}
  {error&&<Alert role="alert">{error}</Alert>}<a className="oauth-return" href="/"><ArrowLeft size={16} aria-hidden="true"/>{t('返回巡店系统','巡回システムに戻る')}</a>
 </section></main>;
}
