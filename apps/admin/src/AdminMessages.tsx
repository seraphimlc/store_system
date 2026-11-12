import {Tabs,TabsList,TabsTrigger,TabsContent} from '@appica/ui-react/tabs';
import {Button} from '@appica/ui-react/button';
import {MessageCenter,type MessageMailboxProps} from '../../../packages/client/src/MessageCenter';
import type {Session} from '../../../packages/contracts/src';
import {api} from './api';
function Mailbox({view,onChange,unread,canSend,children,language}:MessageMailboxProps&{language:string}){
 const t=(zh:string,ja:string)=>language==='ja'?ja:zh;
 return <Tabs className="admin-message-layout" variant="line" size="sm" value={view} onValueChange={value=>onChange(value as MessageMailboxProps['view'])}><div className="admin-message-toolbar"><TabsList aria-label={t('消息信箱','メールボックス')}><TabsTrigger value="inbox">{t('收件箱','受信トレイ')}{unread>0&&<span className="message-unread-count">{unread}</span>}</TabsTrigger>{canSend&&<TabsTrigger value="sent">{t('已发送','送信済み')}</TabsTrigger>}</TabsList>{canSend&&<Button type="button" variant={view==='write'?'outline':'primary'} onClick={()=>onChange('write')}>{t('发送消息','メッセージ送信')}</Button>}</div><TabsContent className="admin-message-content" value={view}>{children}</TabsContent></Tabs>;
}
export function AdminMessages({session,language,onUnread}:{session:Session;language:string;onUnread:(count:number)=>void}){return <MessageCenter detailBase="/admin/messages" session={session} language={language} request={api} onUnread={onUnread} renderMailbox={props=><Mailbox {...props} language={language}/>}/>;}
