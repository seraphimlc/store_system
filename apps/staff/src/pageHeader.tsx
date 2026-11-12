import {createContext,useContext,useEffect,type Dispatch,type SetStateAction} from 'react';
export interface PageHeader {title:string;back:string}
export const PageHeaderContext=createContext<Dispatch<SetStateAction<PageHeader|null>>>(()=>{});
/** Page owns only its title and parent route; the shell owns the single NavBar. */
export function usePageHeader(title:string,back:string){const set=useContext(PageHeaderContext);useEffect(()=>{set({title,back});return()=>set(null);},[set,title,back]);}
