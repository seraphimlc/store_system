import type {ReactNode,MouseEvent} from 'react';
export function AdminLink({href,onNavigate,children,className='management-detail-link'}:{href:string;onNavigate?:()=>void;children:ReactNode;className?:string}){
 function click(event:MouseEvent<HTMLAnchorElement>){if(onNavigate&&event.button===0&&!event.metaKey&&!event.ctrlKey&&!event.shiftKey&&!event.altKey){event.preventDefault();onNavigate();}}
 return <a href={href} className={className} onClick={click}>{children}</a>;
}
