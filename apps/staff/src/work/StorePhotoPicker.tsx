import {useEffect,useRef,useState} from 'react';
import {ActionSheet,Button} from 'antd-mobile';
import {Upload} from 'lucide-react';

export function StorePhotoPicker({files,onChange,onError,language,label,maxCount=3,existingCount=0,disabled=false}:{files:File[];onChange:(files:File[])=>void;onError:(message:string)=>void;language:string;label:string;maxCount?:number;existingCount?:number;disabled?:boolean}){
 const t=(zh:string,ja:string)=>language==='ja'?ja:zh;
 const camera=useRef<HTMLInputElement>(null),gallery=useRef<HTMLInputElement>(null);
 const [actions,setActions]=useState(false),[previews,setPreviews]=useState<string[]>([]);
 useEffect(()=>{const urls=files.map(file=>URL.createObjectURL(file));setPreviews(urls);return()=>urls.forEach(url=>URL.revokeObjectURL(url));},[files]);
 function add(list:File[]){
  if(disabled||!list.length)return;
  if(existingCount+files.length+list.length>maxCount||list.some(file=>file.size>5*1024*1024||!['image/jpeg','image/png','image/webp'].includes(file.type))){onError(t(`最多${maxCount}张JPEG、PNG或WebP照片，每张不超过5MB。`,`JPEG・PNG・WebPを最大${maxCount}枚、各5MBまで選択してください。`));return;}
  onError('');onChange([...files,...list]);
 }
 return <section className="work-photo-picker" aria-label={label}>
  <div className="work-photos">{files.map((file,index)=><div key={index}><img src={previews[index]} alt={file.name}/><Button type="button" disabled={disabled} onClick={()=>{onError('');onChange(files.filter((_,i)=>i!==index));}}>{t('移除','削除')}</Button></div>)}</div>
  <div className="work-photo-actions"><Button type="button" disabled={disabled||existingCount+files.length>=maxCount} onClick={()=>setActions(true)}><span className="work-upload-label"><Upload size={18} aria-hidden="true"/><span>{t('上传照片','写真追加')}</span></span></Button>
   <input hidden aria-label={label+' · '+t('拍照','撮影')} ref={camera} disabled={disabled} type="file" capture="environment" accept="image/jpeg,image/png,image/webp" onChange={event=>{add(Array.from(event.target.files??[]));event.target.value='';}}/>
   <input hidden aria-label={label+' · '+t('选择图片','写真を選択')} ref={gallery} disabled={disabled} type="file" multiple={maxCount>1} accept="image/jpeg,image/png,image/webp" onChange={event=>{add(Array.from(event.target.files??[]));event.target.value='';}}/>
   <ActionSheet visible={actions} cancelText={t('取消','キャンセル')} onClose={()=>setActions(false)} actions={[{key:'camera',text:t('拍照','撮影'),onClick:()=>{setActions(false);if(!disabled)camera.current?.click();}},{key:'gallery',text:t('选择图片','写真を選択'),onClick:()=>{setActions(false);if(!disabled)gallery.current?.click();}}]}/>
  </div>
 </section>;
}
