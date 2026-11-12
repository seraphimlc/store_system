import {useCallback,useEffect,useState} from 'react';
export function useDetailRoute(base:string){
 const read=useCallback(()=>{const match=location.pathname.match(new RegExp('^'+base+'/(\\d+)/?$'));return match?Number(match[1]):null;},[base]);
 const [id,setId]=useState<number|null>(read);
 useEffect(()=>{const pop=()=>setId(read());window.addEventListener('popstate',pop);return()=>window.removeEventListener('popstate',pop);},[read]);
 const navigate=useCallback((value:number|null)=>{setId(value);history.pushState({},'',value===null?base:base+'/'+value);window.dispatchEvent(new PopStateEvent('popstate'));},[base]);
 return [id,navigate] as const;
}
