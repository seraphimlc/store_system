import {AdminCheckbox} from './AdminCheckbox';
import {Alert} from '@appica/ui-react/alert';
import {Input} from '@appica/ui-react/input';
import {useEffect,useRef,useState} from 'react';
import {Button} from '@appica/ui-react/button';
import type {Session} from '../../../packages/contracts/src';
import {api} from './api';
export type GridSelection={id:number;revision:number;zoneId:number|null;zoneRevision:number|null};
type Preview={previewToken:string;gridCount:number;storeCount:number;areaKm2:number;shapeValid:boolean;archivedZoneIds:number[];remainingZones:{id:number;name:string;grids:number;stores:number}[];riverFiles:string[]};
export function GridMerge({selections,session,language,onMerged,onClear}:{selections:GridSelection[];session:Session;language:string;onMerged:()=>void;onClear:()=>void}){
 const t=(zh:string,jp:string)=>language==='ja'?jp:zh;
 const [name,setName]=useState(''),[mergeBoundaries,setMergeBoundaries]=useState(true),[preview,setPreview]=useState<Preview>(),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const writing=useRef(false),generation=useRef(0),signature=JSON.stringify(selections);
 useEffect(()=>{generation.current++;setPreview(undefined);setError('');return()=>{generation.current++;};},[signature,name,mergeBoundaries]);
 const body=()=>({name,selections,mergeBoundaries});
 async function prepare(){if(writing.current)return;writing.current=true;setBusy(true);setPreview(undefined);setError('');const version=generation.current;try{const value=await api<Preview>('/zone-assets/merge-preview',session,body());if(version===generation.current)setPreview(value);}catch(cause){if(version===generation.current)setError((cause as Error).message);}finally{writing.current=false;setBusy(false);}}
 async function save(){if(writing.current||!preview)return;writing.current=true;setBusy(true);setError('');try{await api('/zone-assets/merge',session,{...body(),previewToken:preview.previewToken,confirm:'MERGE_GRIDS'});setPreview(undefined);onMerged();}catch(cause){setError((cause as Error).message);setPreview(undefined);}finally{writing.current=false;setBusy(false);}}
 return <section className="batch-region grid-merge"><h4>{t('组合基础片区','基礎地域の再編成')}</h4><p>{t('已选择网格：','選択中のグリッド：')}{selections.length}</p>{selections.length>0&&<p>{selections.map(g=>'#'+g.id).join('、')}</p>}
 <label>{t('新片区名称','新しい地域名')}<Input value={name} disabled={busy} onChange={event=>setName(event.target.value)}/></label><label><AdminCheckbox  checked={mergeBoundaries} disabled={busy} onChange={event=>setMergeBoundaries(event.target.checked)}/>{t('合并显示边界','表示境界を結合')}</label>
 
 {error&&<Alert role="alert" className="error">{error}</Alert>}
 <Button type="button" variant="outline" disabled={busy||!name.trim()||!selections.length} onClick={()=>void prepare()}>{t('预览网格合并','グリッド結合をプレビュー')}</Button><Button type="button" variant="outline" disabled={busy||!selections.length} onClick={onClear}>{t('清空合并选择','結合選択をクリア')}</Button>
 {busy&&<p role="status">{t('正在校验网格合并…','グリッドの結合を検証中…')}</p>}
 {preview&&<div><p>{t('所选网格 / 商户映射：','選択グリッド / 店舗所属：')}{preview.gridCount} / {preview.storeCount} · {preview.areaKm2.toFixed(4)} km²</p>{!preview.shapeValid&&<p>{t('形状未达到建议标准，请核对后再确认。','推奨の形状基準を満たしていません。内容を確認してから確定してください。')}</p>}<p>{t('将归档片区：','アーカイブする地域：')}{preview.archivedZoneIds.join('、')||'—'}</p><ul>{preview.remainingZones.filter(row=>row.grids>0).map(row=><li key={row.id}>{row.name} · {row.grids} {t('个剩余网格','残存グリッド')} · {row.stores} {t('家商户','店舗')}</li>)}</ul>
 <Button type="button" disabled={busy} onClick={()=>void save()}>{t('确认组合片区','地域の再編成を確定')}</Button><Button type="button" variant="outline" disabled={busy} onClick={()=>setPreview(undefined)}>{t('取消合并','結合をキャンセル')}</Button></div>}
 </section>;
}
