import {useEffect,useState} from 'react';
import {Combobox,ComboboxTrigger,ComboboxValue,ComboboxContent,ComboboxInput,ComboboxList,ComboboxItem,ComboboxEmpty} from '@appica/ui-react/combobox';
import {Button} from '@appica/ui-react/button';
import {Alert} from '@appica/ui-react/alert';
import type {Session} from '../../../packages/contracts/src';
import {api} from './api';
type Option={value:string;label:string};
/** Fetch metadata only when opened; selecting an ID disambiguates identical station names. */
export function GridFilter({session,language,region,state,value,onChange}:{session:Session;language:string;region:string;state:string;value:string;onChange:(value:string)=>void}){
 const t=(zh:string,ja:string)=>language==='ja'?ja:zh;
 const [open,setOpen]=useState(false),[query,setQuery]=useState(''),[items,setItems]=useState<Option[]>([]),[selected,setSelected]=useState<Option|null>(null),[loading,setLoading]=useState(false),[error,setError]=useState(''),[total,setTotal]=useState(0),[retry,setRetry]=useState(0);
 useEffect(()=>{if(!open)return;let live=true;setLoading(true);setError('');setItems([]);setTotal(0);
  const timer=window.setTimeout(()=>{void api<{items:{id:number;name:string}[];total:number}>('/zone-assets/grids?'+new URLSearchParams({region,state,q:query.trim(),page:'1'}),session).then(result=>{if(live){setItems(result.items.map(grid=>({value:String(grid.id),label:`${grid.name} · #${grid.id}`})));setTotal(result.total);}}).catch(cause=>{if(live)setError((cause as Error).message);}).finally(()=>{if(live)setLoading(false);});},200);
  return()=>{live=false;window.clearTimeout(timer);};
 },[open,query,region,state,session,retry]);
 const all={value:'',label:t('全部网格','すべてのグリッド')},current=value?(selected?.value===value?selected:{value,label:'#'+value}):all;
 return <Combobox<Option> name="q" value={current} items={[all,...items]} filter={null} open={open} onOpenChange={next=>{setOpen(next);if(next)setQuery('');}} inputValue={query} onInputValueChange={setQuery} itemToStringLabel={item=>item.label} itemToStringValue={item=>item.value} isItemEqualToValue={(a,b)=>a.value===b.value} size="sm" icon={false} onValueChange={next=>{if(next){setSelected(next);setQuery('');onChange(next.value);}}}>
  <ComboboxTrigger className="grid-filter-trigger" aria-label={t('基础网格','基礎グリッド')} title={current.label}><ComboboxValue>{()=> <span className="grid-filter-value"><span className="grid-filter-name">{current.value?current.label.replace(/ · #\d+$/,''):current.label}</span>{current.value&&<span className="grid-filter-id">#{current.value}</span>}</span>}</ComboboxValue></ComboboxTrigger>
  <ComboboxContent className="admin-searchable-select grid-filter-popup">
   <div className="admin-select-search"><ComboboxInput aria-label={t('筛选：基础网格','絞り込み：基礎グリッド')} placeholder={t('输入名称或编号筛选','名前・番号で絞り込み')}/></div>
   {loading&&<p role="status">{t('正在加载网格…','グリッドを読み込み中…')}</p>}
   {error&&<Alert variant="error">{error}<Button type="button" variant="outline" onClick={()=>setRetry(n=>n+1)}>{t('重试','再試行')}</Button></Alert>}
   {!loading&&!error&&items.length===0&&<p role="status">{t('没有匹配网格','一致するグリッドはありません')}</p>}
   {/* {!loading&&!error&&total>items.length&&<p role="status">{t(`显示前${items.length}项，共${total}项；输入名称或编号缩小范围`,`全${total}件中${items.length}件を表示。名前・番号で絞り込んでください`)}</p>} */}
   <ComboboxEmpty>{t('没有匹配网格','一致するグリッドはありません')}</ComboboxEmpty>
   <ComboboxList>{(item:Option)=><ComboboxItem key={item.value} value={item}>{item.label}</ComboboxItem>}</ComboboxList>
  </ComboboxContent>
 </Combobox>;
}
