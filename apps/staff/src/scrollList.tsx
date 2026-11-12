import {useCallback,useEffect,useRef,useState} from 'react';
import {Button} from 'antd-mobile';
import {api} from './api';
export {ScrollMore} from '../../../packages/client/src/ScrollMore';
export function useProgressiveItems<T>(items:T[],batch=20){const [count,setCount]=useState(batch);useEffect(()=>setCount(batch),[items,batch]);return {items:items.slice(0,count),hasMore:count<items.length,loadMore:()=>setCount(v=>v+batch)};}
export interface PageResult<T>{items:T[];total:number;pageSize?:number}
/** Never request an already in-flight page. Old filter responses cannot append to a new list. */
export function useScrollPage<T,P extends PageResult<T>>(path:string,keyOf:(item:T)=>string|number,defaultPageSize=20,resetKey:unknown=0){
 const [items,setItems]=useState<T[]>([]),[data,setData]=useState<P|null>(null),[loading,setLoading]=useState(true),[error,setError]=useState('');
 const state=useRef({path,page:0,loaded:0,total:Infinity,busy:false,generation:0});const key=useRef(keyOf);key.current=keyOf;
 const loadMore=useCallback(async()=>{const s=state.current;if(s.path!==path||s.busy||s.loaded>=s.total)return;s.busy=true;setLoading(true);setError('');const generation=s.generation,next=s.page+1;try{const value=await api<P>(path+(path.includes('?')?'&':'?')+'page='+next);if(state.current!==s||s.generation!==generation)return;const size=value.pageSize??defaultPageSize;s.page=next;s.loaded+=value.items.length;s.total=value.items.length<size?s.loaded:value.total;setData(value);setItems(prev=>{const seen=new Set(prev.map(key.current));return [...prev,...value.items.filter(item=>{const id=key.current(item);if(seen.has(id))return false;seen.add(id);return true;})];});}catch(e){if(state.current===s)setError((e as Error).message);}finally{if(state.current===s){s.busy=false;setLoading(false);}}},[path,defaultPageSize]);
 useEffect(()=>{state.current={path,page:0,loaded:0,total:Infinity,busy:false,generation:state.current.generation+1};setItems([]);setData(null);setError('');void loadMore();return()=>{state.current.generation++;};},[path,loadMore,resetKey]);
 const hasMore=state.current.path===path&&state.current.loaded<state.current.total;
 return {items,data,loading,error,hasMore,loadMore};
}
