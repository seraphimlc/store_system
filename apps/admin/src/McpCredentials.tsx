import {useEffect,useRef,useState,type FormEvent} from 'react';
import {Button} from '@appica/ui-react/button';
import {Input} from '@appica/ui-react/input';
import {Form} from '@appica/ui-react/form';
import {Alert} from '@appica/ui-react/alert';
import type {Session} from '../../../packages/contracts/src';
import {api} from './api';
import {AdminSelect} from './AdminSelect';
import {ManagementDialog} from './ManagementDialog';
import {SuccessNotice} from './SuccessNotice';
type Credential={id:number;name:string;token_prefix:string;scopes:string;status:string;expires_at:string|null;last_used_at:string|null};
export function McpCredentials({session,userId,username,language,onClose}:{session:Session;userId:number;username:string;language:string;onClose:()=>void}){
 const t=(zh:string,ja:string)=>language==='ja'?ja:zh;
 const [items,setItems]=useState<Credential[]>([]),[loading,setLoading]=useState(true),[error,setError]=useState(''),[notice,setNotice]=useState(''),[raw,setRaw]=useState(''),[busy,setBusy]=useState(false),[revokeId,setRevokeId]=useState<number|null>(null);const writing=useRef(false),generation=useRef(0);
 async function load(){const ticket=++generation.current;try{const result=await api<{items:Credential[]}>('/mcp-credentials/'+userId);if(ticket===generation.current)setItems(result.items);}catch(cause){if(ticket===generation.current)setError((cause as Error).message);}finally{if(ticket===generation.current)setLoading(false);}}
 useEffect(()=>{void load();return()=>{generation.current++;};},[userId]);
 async function issue(event:FormEvent<HTMLFormElement>){event.preventDefault();if(writing.current)return;writing.current=true;setBusy(true);setRaw('');setError('');setNotice('');const form=event.currentTarget,values=new FormData(form);try{const result=await api<{token:string}>('/mcp-credentials/'+userId,session,{name:values.get('name'),scopes:values.get('scopes'),days:values.get('days')==='permanent'?null:90});setRaw(result.token);form.reset();await load();setNotice(t('凭据已签发','認証情報を発行しました'));}catch(cause){setError((cause as Error).message);}finally{writing.current=false;setBusy(false);}}
 async function revoke(){if(revokeId===null||writing.current)return;writing.current=true;setBusy(true);setError('');setNotice('');try{await api(`/mcp-credentials/${userId}/${revokeId}/revoke`,session,{});setRaw('');setRevokeId(null);await load();setNotice(t('凭据已撤销','認証情報を失効しました'));}catch(cause){setError((cause as Error).message);}finally{writing.current=false;setBusy(false);}}
 return <ManagementDialog title={t('MCP 凭据','MCP 認証情報')+' · '+username} language={language} busy={busy} onClose={onClose}>
  <div className="mcp-credentials"><p>{t('凭据只提供该账号已有的权限。写权限允许填报或操作，但不会提升账号角色。','認証情報は既存のアカウント権限のみを使用します。書込権限で役割が昇格することはありません。')}</p>
  <Form className="oauth-form" onSubmit={issue}><label>{t('凭据名称','認証情報名')}<Input name="name" maxLength={128} required placeholder={t('例如：我的客户端','例：自分のクライアント')}/></label><label>{t('权限','権限')}<AdminSelect name="scopes"><option value="read">{t('只读','読取のみ')}</option><option value="read,write">{t('读写','読取・書込')}</option></AdminSelect></label><label>{t('有效期','有効期間')}<AdminSelect name="days"><option value="90">{t('90 天','90日')}</option><option value="permanent">{t('永久（撤销前有效）','無期限（失効まで有効）')}</option></AdminSelect></label><Button type="submit" disabled={busy}>{t('签发凭据','認証情報を発行')}</Button></Form>
  {raw&&<section className="mcp-token-once" aria-label={t('新凭据','新しい認証情報')}><h3>{t('请立即安全保存','安全な場所に保存してください')}</h3><p>{t('明文只显示这一次，关闭窗口后无法再次查看。','平文は今回のみ表示されます。閉じると再表示できません。')}</p><Input value={raw} readOnly aria-label={t('仅显示一次的 Token','今回のみ表示する Token')} onFocus={event=>event.currentTarget.select()}/><Button variant="outline" onClick={()=>setRaw('')}>{t('已保存，隐藏明文','保存済み・平文を隠す')}</Button></section>}
  {error&&<Alert role="alert">{error}</Alert>}{notice&&<SuccessNotice>{notice}</SuccessNotice>}
  <h3>{t('已有凭据','発行済み認証情報')}</h3>{loading?<p role="status">{t('正在读取…','読み込み中…')}</p>:!items.length?<p className="muted">{t('尚无凭据','認証情報はありません')}</p>:<ul className="mcp-credential-list">{items.map(item=><li key={item.id}><div><strong>{item.name}</strong><p className="muted">{item.token_prefix}… · {item.scopes} · {({active:t('有效','有効'),revoked:t('已撤销','失効済み'),expired:t('已过期','期限切れ'),user_inactive:t('账号不可用','アカウント無効')} as Record<string,string>)[item.status]??item.status}</p><p className="muted">{t('到期','有効期限')}：{item.expires_at?new Date(item.expires_at).toLocaleDateString(language==='ja'?'ja-JP':'zh-CN'):t('永久','無期限')}</p></div>{item.status!=='revoked'&&<Button variant="outline" disabled={busy} onClick={()=>{setRevokeId(item.id);setRaw('');}}>{t('撤销','失効')}</Button>}</li>)}</ul>}
  {revokeId!==null&&<section className="mcp-token-once"><p>{t('确认撤销此凭据？客户端将需要重新连接。OAuth 凭据关联的刷新授权也会失效。','この認証情報を失効しますか？クライアントは再接続が必要です。OAuth の更新認可も失効します。')}</p><div className="oauth-actions"><Button disabled={busy} onClick={revoke}>{t('确认撤销','失効を確定')}</Button><Button variant="outline" disabled={busy} onClick={()=>setRevokeId(null)}>{t('取消','キャンセル')}</Button></div></section>}
  <Button variant="outline" disabled={busy} onClick={onClose}>{t('关闭','閉じる')}</Button>
  </div></ManagementDialog>;
}
