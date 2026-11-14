import {useEffect,useId,useRef,useState,type CompositionEvent,type KeyboardEvent} from 'react';
import type {Session} from '../../../../packages/contracts/src';import type {StoreCompletionPage} from '../../../../packages/contracts/src/store-search';
import {normalizeStoreQuery} from '../../../../packages/domain/src/store-search';import {api} from '../api';import {SearchText} from './SearchText';
export function StoreNameInput({completionEnabled=true,value,onChange,onChooseCompletion,gridId,state,businessStatus,session,language,label,placeholder,composing,onCompositionStart,onCompositionEnd,onKeyDown}: {completionEnabled?:boolean;value:string;onChange:(value:string)=>void;onChooseCompletion?:(name:string,receipt:string)=>void;gridId:number;state:string;businessStatus:string;session:Session;language:string;label:string;placeholder:string;composing:boolean;onCompositionStart:()=>void;onCompositionEnd:(event:CompositionEvent<HTMLInputElement>)=>void;onKeyDown:(event:KeyboardEvent<HTMLInputElement>)=>void}){
 const t=(zh:string,ja:string)=>language==='ja'?ja:zh,id=useId(),input=useRef<HTMLInputElement>(null),sequence=useRef(0);
 const [focused,setFocused]=useState(false),[dismissed,setDismissed]=useState<string|null>(null),[result,setResult]=useState<{key:string;data:StoreCompletionPage}|null>(null),[active,setActive]=useState(-1);
 const key=JSON.stringify([gridId,value,state,businessStatus]),normalized=normalizeStoreQuery(value),eligible=[...normalized].length>=2&&[...normalized].length<=80&&!/^\p{N}+$/u.test(normalized);
 const enabled=completionEnabled&&focused&&!composing&&eligible&&dismissed!==key,items=enabled&&result?.key===key&&result.data.status==='ready'?result.data.items:[],open=items.length>0;
 useEffect(()=>{const generation=++sequence.current,controller=new AbortController();setActive(-1);if(!enabled)return;
  const timer=setTimeout(()=>{void api<StoreCompletionPage>('/batch-work/grids/'+gridId+'/completions?'+new URLSearchParams({q:value,state,businessStatus}),session,undefined,controller.signal).then(data=>{if(generation===sequence.current&&!controller.signal.aborted)setResult({key,data});}).catch(()=>{if(generation===sequence.current&&!controller.signal.aborted)setResult(null);});},250);
  return()=>{sequence.current++;clearTimeout(timer);controller.abort();};
 },[key,enabled,session]);
 function choose(name:string){if(result?.data.receipt)onChooseCompletion?.(name,result.data.receipt);setDismissed(JSON.stringify([gridId,name,state,businessStatus]));setResult(null);setActive(-1);onChange(name);input.current?.focus();}
 function keyboard(event:KeyboardEvent<HTMLInputElement>){if(composing||event.nativeEvent.isComposing||event.keyCode===229){onKeyDown(event);return;}
  if(event.key==='Escape'){event.preventDefault();setDismissed(key);setResult(null);setActive(-1);return;}
  if(open&&(event.key==='ArrowDown'||event.key==='ArrowUp')){event.preventDefault();setActive(previous=>event.key==='ArrowDown'?(previous+1)%items.length:(previous<=0?items.length:previous)-1);return;}
  if(open&&event.key==='Enter'&&active>=0){event.preventDefault();choose(items[active].name);return;}if(event.key==='Enter'){setDismissed(key);setResult(null);}onKeyDown(event);
 }
 return <div className="work-name-input" onBlur={event=>{if(!event.currentTarget.contains(event.relatedTarget as Node|null))setFocused(false);}}>
  <input ref={input} role="combobox" aria-label={label} aria-autocomplete="list" aria-expanded={open} aria-controls={open?id:undefined} aria-activedescendant={open&&active>=0?id+'-'+active:undefined} autoComplete="off" value={value} placeholder={placeholder} onFocus={()=>setFocused(true)} onChange={event=>{setDismissed(null);onChange(event.target.value);}} onCompositionStart={()=>{setResult(null);onCompositionStart();}} onCompositionEnd={onCompositionEnd} onKeyDown={keyboard}/>
  {open&&<div className="work-completion-panel"><p>{t('当前网格名称补全','現在のグリッドの店舗名候補')} · {result!.data.total}{t(' 个名称，显示前8个',' 件・先頭8件を表示')}</p><ul id={id} role="listbox" aria-label={t('名称补全','店舗名の補完')}>{items.map((item,index)=><li key={item.name}><button type="button" role="option" tabIndex={-1} id={id+'-'+index} aria-selected={active===index} onPointerDown={event=>event.preventDefault()} onClick={()=>choose(item.name)}><span><SearchText text={item.name} query={value}/></span><small>{item.candidateCount+t(' 家候选',' 店の候補')}</small></button></li>)}</ul></div>}
 </div>;
}
