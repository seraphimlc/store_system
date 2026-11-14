import type {ReactNode} from 'react';
import type {Store} from './types';
export function storeResultHref(store:Store,query:string){return '/my/work/batch-grids/'+store.gridTaskId+'?'+new URLSearchParams({q:query,store:String(store.id),...(store.searchLogId?{originSearch:String(store.searchLogId)}:{})});}
/** Cross-grid results navigate into their own permission/execution context rather than editing under the anchor grid. */
export function StoreResultAction({store,currentGrid,query,disabled,onSelect,children}:{store:Store;currentGrid:number;query:string;disabled:boolean;onSelect:(id:number)=>void;children:ReactNode}){
 if(store.gridTaskId&&store.gridTaskId!==currentGrid)return <a href={storeResultHref(store,query)} aria-disabled={disabled||undefined} onClick={e=>{if(disabled)e.preventDefault();}}>{children}</a>;
 return <button disabled={disabled} onClick={()=>onSelect(store.id)}>{children}</button>;
}
