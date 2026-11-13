import {useState} from 'react';
import {Button} from '@appica/ui-react/button';
import {Alert} from '@appica/ui-react/alert';
import {AdminSelect} from './AdminSelect';
import {api} from './api';
import type {Session} from '../../../packages/contracts/src';
interface Client{client_id:string;client_name:string;redirect_uris:string[];allowed_scopes:'read'|'read,write';approved:boolean}
export function OAuthClients({session,language}:{session:Session;language:string}){
 const ja=language==='ja',t=(zh:string,jp:string)=>ja?jp:zh;
 const [items,setItems]=useState<Client[]>([]),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 async function load(){try{setItems((await api<{items:Client[]}>('/mcp-oauth/clients',session)).items);setError('');}catch(e){setError((e as Error).message);}}
 async function update(client:Client,approved:boolean){if(busy)return;setBusy(true);setError('');try{await api('/mcp-oauth/clients/'+client.client_id,session,{approved,scope:client.allowed_scopes});await load();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 return <details onToggle={event=>{if(event.currentTarget.open)void load();}}><summary>{t('MCP 客户端准入','MCP クライアント承認')}</summary>
  <p>{t('批准前请核对回调地址与客户端来源。保存策略会撤销该客户端已有授权，需要重新连接。','承認前にコールバック先と発行元を確認してください。保存すると既存の認可は失効し、再接続が必要です。')}</p>
  {error&&<Alert role="alert">{error}</Alert>}
  {!items.length?<p>{t('暂无客户端','クライアントなし')}</p>:items.map(client=><section key={client.client_id}><strong>{client.client_name}</strong><p>{client.redirect_uris.join(' · ')}</p>
   <label>{t('允许权限','許可範囲')}<AdminSelect aria-label={client.client_name+' '+t('允许权限','許可範囲')} value={client.allowed_scopes} onChange={event=>setItems(items.map(item=>item.client_id===client.client_id?{...item,allowed_scopes:event.target.value as Client['allowed_scopes']}:item))}><option value="read">{t('只读','読み取り')}</option><option value="read,write">{t('读写','読み書き')}</option></AdminSelect></label>
   <p>{client.approved?t('已批准','承認済み'):t('待批准','承認待ち')}</p><Button disabled={busy} onClick={()=>void update(client,true)}>{t('批准并保存','承認して保存')}</Button> <Button variant="outline" disabled={busy} onClick={()=>void update(client,false)}>{t('拒绝并撤销','拒否して失効')}</Button>
  </section>)}
 </details>;
}
