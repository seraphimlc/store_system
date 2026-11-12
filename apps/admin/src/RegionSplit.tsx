import {Alert} from '@appica/ui-react/alert';
import {useEffect,useRef,useState} from 'react';
import {Button} from '@appica/ui-react/button';
import type {Session} from '../../../packages/contracts/src';
import {api} from './api';
type Preview={id:number;name:string;revision:number;previewToken:string;gridCount:number;storeCount:number;grids:{id:number;name:string;state:string;revision:number}[]};
export function RegionSplit({region,session,language,onSplit,compact=false}:{region:{id:number;revision:number};session:Session;language:string;onSplit:()=>void;compact?:boolean}){
 const t=(zh:string,jp:string)=>language==='ja'?jp:zh;
 const [preview,setPreview]=useState<Preview>(),[busy,setBusy]=useState(false),[error,setError]=useState('');const generation=useRef(0),writing=useRef(false);
 useEffect(()=>{generation.current++;setPreview(undefined);setError('');return()=>{generation.current++;};},[region.id,region.revision]);
 async function prepare(){if(writing.current)return;writing.current=true;setBusy(true);setError('');setPreview(undefined);const version=generation.current;try{const result=await api<Preview>('/zone-assets/regions/'+region.id+'/split-preview',session,{revision:region.revision});if(version===generation.current)setPreview(result);}catch(cause){if(version===generation.current)setError((cause as Error).message);}finally{writing.current=false;setBusy(false);}}
 async function apply(){if(!preview||writing.current)return;writing.current=true;setBusy(true);setError('');try{await api('/zone-assets/regions/'+region.id+'/split',session,{revision:preview.revision,previewToken:preview.previewToken,confirm:'SPLIT_REGION'});setPreview(undefined);onSplit();}catch(cause){setError((cause as Error).message);setPreview(undefined);}finally{writing.current=false;setBusy(false);}}
 return <section className="batch-region region-split">{!compact&&<><h4>{t('拆分基础片区','基礎地域を分割')}</h4><p>{t('解除片区内完整网格的归属并归档此片区，保留边界、商户映射和已创建批次。已有派发或巡店历史时不可拆分。','全グリッドを地域から解除し、この地域をアーカイブします。境界・店舗の所属・作成済みバッチは保持されます。割当や訪問履歴がある場合は分割できません。')}</p></>}
 {error&&<Alert role="alert" className="error">{error}</Alert>}
 <Button type="button" variant="outline" disabled={busy} onClick={()=>void prepare()}>{t('预览片区拆分','地域分割をプレビュー')}</Button>
 {busy&&<p role="status">{t('正在处理片区拆分…','地域の分割を処理中…')}</p>}
 {preview&&<div><p>{preview.name} · {t('将释放网格：','解除するグリッド：')}{preview.gridCount} · {t('保留商户映射：','保持する店舗所属：')}{preview.storeCount}</p><ul>{preview.grids.map(grid=><li key={grid.id}>{grid.name} · #{grid.id} · {grid.state}</li>)}</ul>
 <p>{t('确认后此片区将归档，网格可用于重新组合片区。','確定すると地域をアーカイブし、グリッドを地域の再編成に利用できます。')}</p><Button type="button" disabled={busy} onClick={()=>void apply()}>{t('确认拆分并归档','分割とアーカイブを確定')}</Button><Button type="button" variant="outline" disabled={busy} onClick={()=>setPreview(undefined)}>{t('取消拆分','分割をキャンセル')}</Button></div>}
 </section>;
}
