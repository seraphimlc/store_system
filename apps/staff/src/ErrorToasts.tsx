import {useEffect} from 'react';
import {Toast} from 'antd-mobile';
/** Keep contextual errors/validation available, but announce failures with the employee Toast first. */
export function ErrorToasts(){
 useEffect(()=>{
  const recent=new Map<string,number>();
  const show=(message:string)=>{const text=message.trim();if(!text)return;const now=Date.now();if(now-(recent.get(text)??0)<2500)return;recent.set(text,now);for(const [key,at] of recent)if(now-at>10000)recent.delete(key);Toast.show({content:text,duration:3000});};
  const onError=(event:Event)=>show((event as CustomEvent<string>).detail);
  window.addEventListener('store:api-error',onError);
  const observer=new MutationObserver(records=>{
   const errors=new Set<Element>();
   for(const record of records){const root=record.target instanceof Element?record.target:record.target.parentElement;const nearest=root?.closest('.error,[role="alert"],.attendance-error');if(nearest)errors.add(nearest);for(const node of record.addedNodes)if(node instanceof Element){if(node.matches('.error,[role="alert"],.attendance-error'))errors.add(node);node.querySelectorAll('.error,[role="alert"],.attendance-error').forEach(el=>errors.add(el));}}
   for(const element of errors)if(element.isConnected&&!element.closest('.adm-toast-mask,.adm-toast-main')&&element.getClientRects().length)show(element.textContent??'');
  });
  observer.observe(document.body,{childList:true,subtree:true,characterData:true});
  return()=>{observer.disconnect();window.removeEventListener('store:api-error',onError);};
 },[]);
 return null;
}
