import {UISuccessNotice} from './ui-controls';
import {ScrollMore} from './ScrollMore';
import {UIFieldset} from './ui-controls';
import {UIForm,UIInput,UIButton} from './ui-controls';
import {useEffect,useId,useRef,useState,type FormEvent,type ReactNode} from 'react';
import type {Session} from '../../contracts/src';
import {api,upload} from './request';
import {googleMaps} from './google-maps';
interface Proposal{id:number;state:string;revision:number;reason:string;submittedBy:number;name:string;address:string;lat:number;lon:number;placeLookupState?:string|null;googlePlaceId?:string|null}
interface ProposalPage{items:Proposal[];total:number;canSubmit:boolean;canReview:boolean;canApprove:boolean}
const blank={name:'',address:'',lat:'',lon:''};
export function StoreProposals({gridId,mainId,session,language,onReviewed,mobile=false,defaultOpen=false,submissionOnly=false,renderSubmit,renderPhoto,onBusy}:{gridId:number;mainId:number;session:Session;language:string;onReviewed:()=>Promise<void>;mobile?:boolean;defaultOpen?:boolean;submissionOnly?:boolean;renderSubmit?:(button:ReactNode)=>ReactNode;renderPhoto?:(props:{file:File|undefined;disabled:boolean;onChange:(file:File|undefined)=>void;onError:(message:string)=>void})=>ReactNode;onBusy?:(busy:boolean)=>void}){
 const formId=useId();
 const t=(zh:string,ja:string)=>language==='ja'?ja:zh;
 const [open,setOpen]=useState(defaultOpen||submissionOnly),[page,setPage]=useState(1),[data,setData]=useState<ProposalPage|null>(null),[draft,setDraft]=useState(blank),[editing,setEditing]=useState<number>(),[file,setFile]=useState<File>(),[selected,setSelected]=useState<number[]>([]),[reason,setReason]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false),[locating,setLocating]=useState(false),[loading,setLoading]=useState(false),[refresh,setRefresh]=useState(0);
 useEffect(()=>{onBusy?.(busy);},[busy,onBusy]);
 const writing=useRef(false),retry=useRef<{key:string;file:File;id:string}|null>(null),fileInput=useRef<HTMLInputElement>(null);
 useEffect(()=>{if(!open)return;let live=true;setLoading(true);setError('');if(!mobile||page===1)setData(null);setSelected([]);api<ProposalPage>(`/batch-work/grids/${gridId}/proposals?page=${page}`).then(result=>{if(live)setData(previous=>mobile&&page>1&&previous?{...result,items:[...previous.items,...result.items.filter(row=>!previous.items.some(p=>p.id===row.id))]}:result);}).catch(cause=>{if(live)setError(cause.message);}).finally(()=>{if(live)setLoading(false);});return()=>{live=false;};},[open,page,gridId,refresh]);
 const update=(key:keyof typeof blank,value:string)=>setDraft(previous=>({...previous,[key]:value}));
 function reset(){setDraft(blank);setEditing(undefined);setFile(undefined);if(fileInput.current)fileInput.current.value='';retry.current=null;}
 async function run(action:()=>Promise<void>,message:string){if(writing.current)return;writing.current=true;setBusy(true);setError('');setNotice('');try{await action();setPage(1);setRefresh(n=>n+1);setNotice(message);}catch(cause){setError((cause as Error).message);}finally{writing.current=false;setBusy(false);}}
 function submit(event:FormEvent){event.preventDefault();if(!file)return;if(!draft.lat.trim()||!draft.lon.trim()){setError(t('请先使用当前位置获取定位','先に現在地を取得してください'));return;}
  const payload={name:draft.name,address:draft.address,lat:Number(draft.lat),lon:Number(draft.lon),...(editing?{proposalId:editing}:{})},key=JSON.stringify(payload);
  if(!retry.current||retry.current.key!==key||retry.current.file!==file)retry.current={key,file,id:crypto.randomUUID()};
  const form=new FormData();form.append('result',JSON.stringify({...payload,requestId:retry.current.id}));form.append('photos',file);
  void run(async()=>{await upload(`/batch-work/grids/${gridId}/proposals`,session,form);reset();setPage(1);},t('申请已提交，等待队长核实','申請を提出しました。リーダーの確認をお待ちください'));
 }
 function review(action:'approve'|'reject'){const selections=(data?.items??[]).filter(row=>selected.includes(row.id)&&row.state==='pending').map(({id,revision})=>({id,revision}));if(!selections.length)return;
  void run(async()=>{await api(`/batch-work/batches/${mainId}/proposal-review`,session,{action,reason,selections});setReason('');await onReviewed();},t('核实结果已保存','確認結果を保存しました'));
 }
 async function locate(){if(locating||busy)return;if(!navigator.geolocation){setError(t('无法取得定位，请重试','位置を取得できません。再試行してください'));return;}setLocating(true);setError('');try{
  const position=await new Promise<GeolocationPosition>((resolve,reject)=>navigator.geolocation.getCurrentPosition(resolve,()=>reject(Error('geo')),{enableHighAccuracy:true,timeout:10000,maximumAge:0}));
  const lat=position.coords.latitude,lon=position.coords.longitude;let address='';
  try{const config=await api<{enabled:boolean;key:string|null}>(`/batch-work/grids/${gridId}/map-config`,session);if(config.enabled&&config.key){const maps=await googleMaps(config.key,language);let timer=0;const response=await Promise.race([new maps.Geocoder().geocode({location:{lat,lng:lon},language:language==='ja'?'ja':'zh-CN'}).finally(()=>window.clearTimeout(timer)),new Promise<null>(resolve=>{timer=window.setTimeout(()=>resolve(null),8000);})]);const text=response&&'results' in response?response.results[0]?.formatted_address?.trim():'';if(text)address=text.slice(0,1000);}}catch{/* coordinates still apply */}
  setDraft(previous=>({...previous,lat:String(lat),lon:String(lon),...address?{address}: {}}));if(!address)setError(t('地址未能自动识别，请手动填写','住所を特定できないため、手入力してください'));
 }catch{setError(t('无法取得定位，请重试','位置を取得できません。再試行してください'));}finally{setLocating(false);}}
 const placeText=(state?:string|null)=>({candidate:t('候选地点，尚未确认身份','候補・本人確認前'),unconfigured:t('未配置地点查询','地点検索は未設定'),ambiguous:t('多个候选，未自动关联','複数候補'),no_match:t('未找到匹配','一致なし'),failed:t('查询失败，可重试','失敗・再試行可')} as Record<string,string>)[state??'']??'';
 return <details className="store-proposals" open={open} onToggle={event=>setOpen(event.currentTarget.open)}><summary>{t('新店申请与核实','新規店舗の申請・確認')}</summary>{open&&<>
 {error&&<p role="alert" className="error">{error}</p>}{notice&&<UISuccessNotice role="status" className="success-notice">{notice}</UISuccessNotice>}{loading&&<p role="status">{t('读取申请…','申請を読み込み中…')}</p>}
 {data?.canSubmit&&<UIForm id={formId} onSubmit={submit}><UIFieldset disabled={busy}>
 {/* <legend>{editing?t('修改并重新提交','修正して再提出'):t('申请新增门店','新規店舗を申請')}</legend> */}
 <label>{t('申请店名','申請店舗名')}<UIInput required maxLength={255} value={draft.name} onChange={e=>update('name',e.target.value)}/></label>
 <label>{t('申请地址','申請住所')}<UIInput required maxLength={1000} value={draft.address} onChange={e=>update('address',e.target.value)}/></label>
 <UIButton type="button" disabled={locating||busy} onClick={()=>void locate()}>{locating?t('正在定位…','位置を取得中…'):t('使用当前位置','現在地を使う')}</UIButton>
 {renderPhoto?<div className="proposal-photo-field"><span>{t('新店照片','新規店舗の写真')}</span>{renderPhoto({file,disabled:busy,onChange:setFile,onError:setError})}</div>:<label>{t('新店照片','新規店舗の写真')}<UIInput ref={fileInput} type="file" required accept="image/jpeg,image/png,image/webp" onChange={e=>{const photo=e.target.files?.[0];if(photo&&photo.size>5*1024*1024){e.target.value='';setFile(undefined);setError(t('照片须不超过 5 MB','写真は5 MB以下です'));}else{setError('');setFile(photo);}}}/></label>}
 {!(submissionOnly&&renderSubmit)&&<UIButton type="submit" disabled={busy||!file}>{t('提交新店申请','新規店舗の申請を提出')}</UIButton>}{editing&&<UIButton type="button" onClick={reset}>{t('取消修改','修正を取り消す')}</UIButton>}
 </UIFieldset></UIForm>}
 {submissionOnly&&renderSubmit&&data?.canSubmit&&renderSubmit(<UIButton type="submit" form={formId} disabled={busy||!file}>{busy?t('正在提交…','提出中…'):t('提交新店申请','新規店舗の申請を提出')}</UIButton>)}
 {submissionOnly&&data&&!data.canSubmit&&<p role="alert">{t('当前不能录入新店，请返回任务确认权限或状态。','現在は新規店舗を申請できません。権限とタスク状態を確認してください。')}</p>}
 {!submissionOnly&&data&&<><p>{t('申请记录','申請記録')} · {data.total}</p>{data.items.map(row=><article className="proposal-row" key={row.id}>
 <strong>{row.name}</strong><p>{row.address}</p>{placeText(row.placeLookupState)&&<p>{t('地点查询','地点検索')}：{placeText(row.placeLookupState)}{row.googlePlaceId?' · '+row.googlePlaceId:''}</p>}<p>{({pending:t('待核实','確認待ち'),approved:t('已通过','承認済み'),rejected:t('已驳回','却下済み')} as Record<string,string>)[row.state]||row.state}{row.reason?' · '+row.reason:''}</p>
 <a href={`/api/batch-work/proposals/${row.id}/photo`} target="_blank" rel="noreferrer">{t('查看申请照片','申請写真を表示')}</a>
 {data.canReview&&row.state==='pending'&&<label className="proposal-check"><UIInput type="checkbox" checked={selected.includes(row.id)} disabled={busy} onChange={event=>setSelected(previous=>event.target.checked?[...previous,row.id]:previous.filter(id=>id!==row.id))}/>{t('选择核实','確認対象に選択')} {row.name}</label>}
 {data.canSubmit&&row.state==='rejected'&&row.submittedBy===session.user.id&&<UIButton type="button" disabled={busy} onClick={()=>{reset();setEditing(row.id);setDraft({name:row.name,address:row.address,lat:String(row.lat),lon:String(row.lon)});}}>{t('修改申请','申請を修正')}</UIButton>}
 </article>)}
 {data.canReview&&data.items.some(row=>row.state==='pending')&&<div className="proposal-review"><label>{t('驳回原因（驳回时必填）','却下理由（却下時は必須）')}<UIInput value={reason} maxLength={500} onChange={e=>setReason(e.target.value)} disabled={busy}/></label><div className="batch-actions"><UIButton type="button" disabled={busy||!selected.length||!data.canApprove} onClick={()=>review('approve')}>{t('通过所选申请','選択した申請を承認')}</UIButton><UIButton type="button" disabled={busy||!selected.length||!reason.trim()} onClick={()=>review('reject')}>{t('驳回所选申请','選択した申請を却下')}</UIButton></div></div>}
 {mobile?<ScrollMore hasMore={data.items.length<data.total} loading={loading||busy} error={error} language={language} onLoad={()=>{if(loading||busy)return;if(error)setRefresh(v=>v+1);else setPage(v=>v+1);}}/>:(data.total>100&&<div className="batch-actions"><UIButton type="button" disabled={busy||page===1} onClick={()=>setPage(n=>n-1)}>{t('上一页','前へ')}</UIButton><span>{page}</span><UIButton type="button" disabled={busy||page*100>=data.total} onClick={()=>setPage(n=>n+1)}>{t('下一页','次へ')}</UIButton></div>)}</>}
 </>}</details>;
}
