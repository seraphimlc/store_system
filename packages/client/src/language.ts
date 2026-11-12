import {useEffect,useRef,useState} from 'react';
import type {Language} from '../../contracts/src';
function valid(value:string|undefined|null):value is Language{return value==='zh'||value==='ja';}
export function resolveLanguage(input:{query?:string|null;cookie?:string|null;saved?:string|null;account?:string|null;browser?:string}):Language{
 for(const value of [input.query,input.cookie,input.saved,input.account])if(valid(value))return value;
 return input.browser?.toLowerCase().startsWith('ja')?'ja':'zh';
}
function clientPreferences(){return {query:new URLSearchParams(location.search).get('lang'),cookie:document.cookie.split(';').map(value=>value.trim()).find(value=>value.startsWith('lang='))?.slice(5),saved:localStorage.getItem('store-language'),browser:navigator.language};}
export function useAppLanguage(account?:string):[Language,(value:Language)=>void]{
 const initial=useRef(clientPreferences());const explicit=useRef([initial.current.query,initial.current.cookie,initial.current.saved].some(valid));
 const [language,setCurrent]=useState<Language>(()=>resolveLanguage(initial.current));
 useEffect(()=>{if(!explicit.current)setCurrent(resolveLanguage({...initial.current,account}));},[account]);
 useEffect(()=>{document.documentElement.lang=language==='ja'?'ja':'zh-CN';},[language]);
 function select(value:Language){explicit.current=true;setCurrent(value);localStorage.setItem('store-language',value);document.cookie=`lang=${value};path=/;max-age=31536000;SameSite=Lax`;const url=new URL(location.href);if(url.searchParams.has('lang')){url.searchParams.set('lang',value);history.replaceState({},'',url);}}
 return [language,select];
}
