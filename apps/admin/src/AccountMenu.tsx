import {useToastManager} from '@appica/ui-react/toast';
import {useState,useRef,type FormEvent} from 'react';
import {DropdownMenu,DropdownMenuTrigger,DropdownMenuContent,DropdownMenuItem,DropdownMenuSeparator,DropdownMenuGroup,DropdownMenuGroupLabel} from '@appica/ui-react/dropdown-menu';
import {Button} from '@appica/ui-react/button';
import {Form} from '@appica/ui-react/form';
import {Input} from '@appica/ui-react/input';
import {Alert} from '@appica/ui-react/alert';
import {LockKeyhole,LogOut} from 'lucide-react';
import type {Session} from '../../../packages/contracts/src';
import {ManagementDialog} from './ManagementDialog';
import {api} from './api';
export function AccountMenu({session,language,onSession,onLogout}:{session:Session;language:string;onSession:(session:Session)=>void;onLogout:()=>Promise<void>}){
 const toast=useToastManager();
 const t=(zh:string,ja:string)=>language==='ja'?ja:zh;
 const name=session.user.displayName||session.user.username;
 const [open,setOpen]=useState(false),[passwordOpen,setPasswordOpen]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');const writing=useRef(false);
 async function save(event:FormEvent<HTMLFormElement>){event.preventDefault();if(writing.current)return;const form=event.currentTarget,values=new FormData(form);setError('');if(values.get('newPassword')!==values.get('confirmPassword')){setError(t('两次输入的新密码不一致','新しいパスワードが一致しません'));return;}writing.current=true;setBusy(true);try{const next=await api<Session>('/auth/password',session,{currentPassword:values.get('currentPassword'),newPassword:values.get('newPassword')});form.reset();onSession(next);toast.add({title:t('密码已修改','パスワードを変更しました'),type:'success'});setPasswordOpen(false);}catch(e){setError((e as Error).message);}finally{writing.current=false;setBusy(false);}}
 return <><DropdownMenu open={open} onOpenChange={setOpen}><DropdownMenuTrigger className="user-avatar" aria-label={t('账号菜单','アカウントメニュー')} title={name}>{Array.from(name.trim())[0]?.toLocaleUpperCase()||'U'}</DropdownMenuTrigger><DropdownMenuContent className="account-menu" align="end" sideOffset={8}><DropdownMenuGroup><DropdownMenuGroupLabel><strong>{name}</strong><span>{session.user.username}</span></DropdownMenuGroupLabel><DropdownMenuSeparator/><DropdownMenuItem onClick={()=>{setOpen(false);setError('');setPasswordOpen(true);}}><LockKeyhole size={16}/>{t('修改密码','パスワードを変更')}</DropdownMenuItem><DropdownMenuItem disabled={busy} onClick={()=>{setOpen(false);void onLogout();}}><LogOut size={16}/>{t('退出登录','ログアウト')}</DropdownMenuItem></DropdownMenuGroup></DropdownMenuContent></DropdownMenu>
 {passwordOpen&&<ManagementDialog title={t('修改密码','パスワードを変更')} language={language} busy={busy} onClose={()=>setPasswordOpen(false)}><Form className="account-password-form" onSubmit={save}><label>{t('当前密码','現在のパスワード')}<Input name="currentPassword" type="password" autoComplete="current-password" required maxLength={512}/></label><label>{t('新密码','新しいパスワード')}<Input aria-label={t('新密码','新しいパスワード')} name="newPassword" type="password" autoComplete="new-password" required minLength={6} maxLength={128}/><small>{t('至少6个字符','6文字以上')}</small></label><label>{t('确认新密码','新しいパスワードを確認')}<Input name="confirmPassword" type="password" autoComplete="new-password" required minLength={6} maxLength={128}/></label>{error&&<Alert role="alert">{error}</Alert>}<div className="dialog-actions"><Button type="button" variant="outline" disabled={busy} onClick={()=>setPasswordOpen(false)}>{t('取消','キャンセル')}</Button><Button type="submit" disabled={busy}>{t(busy?'正在保存…':'保存密码',busy?'保存中…':'パスワードを保存')}</Button></div></Form></ManagementDialog>}
 </>;
}
