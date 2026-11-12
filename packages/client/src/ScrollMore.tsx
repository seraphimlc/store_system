import {useEffect,useRef} from 'react';
import {UIButton as Button} from './ui-controls';
export function ScrollMore({hasMore,loading,error,onLoad,language}:{hasMore:boolean;loading?:boolean;error?:string;onLoad:()=>void|Promise<void>;language:string}){
 const sentinel=useRef<HTMLDivElement>(null),latest=useRef({hasMore,loading,error,onLoad});latest.current={hasMore,loading,error,onLoad};
 useEffect(()=>{const node=sentinel.current;if(!node)return;let root:HTMLElement|null=node.parentElement;while(root&&root!==document.body&&!/(auto|scroll)/.test(getComputedStyle(root).overflowY))root=root.parentElement;const observer=new IntersectionObserver(entries=>{const current=latest.current;if(entries[0]?.isIntersecting&&current.hasMore&&!current.loading&&!current.error)void current.onLoad();},{root:root===document.body?null:root,rootMargin:'120px'});observer.observe(node);return()=>observer.disconnect();},[hasMore,loading,error]);
 if(error)return <div className="h5-scroll-status" role="alert"><p>{error}</p><Button onClick={()=>void onLoad()}>{language==='ja'?'再試行':'重试加载'}</Button></div>;
 return <div ref={sentinel} className="h5-scroll-status" role="status">{loading?(language==='ja'?'読み込み中…':'加载中…'):hasMore?(language==='ja'?'スクロールして続きを表示':'向下滚动加载更多'):(language==='ja'?'すべて表示しました':'已显示全部')}</div>;
}
