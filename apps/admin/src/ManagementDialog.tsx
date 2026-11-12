import type {ReactNode} from 'react';
import {Dialog,DialogContent,DialogHeader,DialogTitle,DialogBody} from '@appica/ui-react/dialog';
export function ManagementDialog({title,language,busy=false,onClose,children,wide=false}:{title:string;language:string;busy?:boolean;onClose:()=>void;children:ReactNode;wide?:boolean}){
 return <Dialog open onOpenChange={open=>{if(!open&&!busy)onClose();}}><DialogContent className={'management-dialog'+(wide?' management-dialog-wide':'')} closeLabel={language==='ja'?'閉じる':'关闭'} closeButton={!busy} frame={false}><DialogHeader><DialogTitle>{title}</DialogTitle></DialogHeader><DialogBody>{children}</DialogBody></DialogContent></Dialog>;
}
