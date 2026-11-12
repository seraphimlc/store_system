import {SuccessNotice} from './SuccessNotice';
import {Dialog,DialogContent,DialogTitle} from '@appica/ui-react/dialog';
import {Alert} from '@appica/ui-react/alert';
import {Form} from '@appica/ui-react/form';
import {Input} from '@appica/ui-react/input';
import {useRef,useState,type FormEvent} from 'react';import {Button} from '@appica/ui-react/button';import type {Session} from '../../../packages/contracts/src';import {api} from './api';
interface Preview {id:number;fileName:string;rawRows:number;formalRows:number;points:number;appeals:number;months:string[];token:string}
export function ImportDeletion({id,session,language,onDeleted}:{id:number;session:Session;language:string;onDeleted:()=>Promise<void>}){
 const t=(zh:string,ja:string)=>language==='ja'?ja:zh;const [preview,setPreview]=useState<Preview|null>(null),[confirmation,setConfirmation]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');const writing=useRef(false),request=useRef<{token:string;id:string}|null>(null);
 async function inspect(){if(busy)return;setBusy(true);setError('');try{const value=await api<Preview>('/imports/'+id+'/deletion-preview');setPreview(value);setConfirmation('');}catch(error){setError((error as Error).message);}finally{setBusy(false);}}
 async function remove(event:FormEvent){event.preventDefault();if(!preview||writing.current)return;writing.current=true;setBusy(true);setError('');
 try{
  if(request.current?.token!==preview.token)request.current={token:preview.token,id:crypto.randomUUID()};
  const result=await api<{jobId:string}>('/imports/'+id+'/delete',session,{token:preview.token,requestId:request.current.id,confirm:'DELETE_IMPORT'});setNotice(t('删除任务已入队，正在事务内清理并重算…','削除タスクを登録しました。トランザクション内で削除・再計算中…'));
  for(let attempt=0;attempt<1200;attempt++){
   const job=await api<{status:string;result?:{ok:boolean;message?:string;originalRemoved?:boolean;originalRetained?:string}}>('/imports/deletion-jobs/'+result.jobId);
   if(job.status==='failed'){request.current=null;throw new Error(job.result?.message||t('删除任务失败，数据库未部分删除。','削除タスクが失敗しました。データベースは部分削除されていません。'));}
   if(job.status==='done'){setNotice(job.result?.originalRemoved?t('文件已删除，受影响月份已重算。','ファイルを削除し、影響する月を再計算しました。'):t('文件记录已删除并重算；原件因共享引用或目录保护而保留。','ファイル記録を削除・再計算しました。元ファイルは共有参照・保存場所の保護により保持しました。'));setPreview(null);await onDeleted();return;}
   await new Promise(resolve=>setTimeout(resolve,500));
  }
  throw new Error(t('任务仍在后台处理，请稍后用同一确认重试查询。','タスクは引き続き実行中です。後で同じ確認を再試行して状態を取得できます。'));
 }catch(error){setError((error as Error).message);}finally{writing.current=false;setBusy(false);}}
 return <section className="import-deletion"><Button variant="outline" disabled={busy} onClick={inspect}>{t('预览删除文件','ファイル削除をプレビュー')}</Button>{notice&&<SuccessNotice>{notice}</SuccessNotice>}{error&&!preview&&<Alert className="error" role="alert">{error}</Alert>}
 <Dialog open={!!preview} onOpenChange={open=>{if(!open&&!busy)setPreview(null);}}><DialogContent className="management-dialog management-dialog-wide" frame={false} closeButton={false}>{preview&&<Form onSubmit={remove}><DialogTitle id="delete-import-title">{t('确认删除文件','ファイル削除の確認')} · {preview.fileName}</DialogTitle><p>{t('会删除该文件的源记录、正式行和申诉，并重算受影响月份。人员账号、店铺主档、已登记发放与核销历史保持；这不是撤销实际发薪。','このファイルの元レコード・確定行・申立を削除し、影響する月を再計算します。アカウント・店舗マスター・支給登録・精算履歴は保持します。実際の支給取消ではありません。')}</p><dl className="deletion-summary"><div><dt>{t('源记录','元レコード')}</dt><dd>{preview.rawRows}</dd></div><div><dt>{t('正式行 / 点数','確定行 / ポイント')}</dt><dd>{preview.formalRows} / {preview.points}</dd></div><div><dt>{t('申诉','申立')}</dt><dd>{preview.appeals}</dd></div><div><dt>{t('影响月份','影響月')}</dt><dd>{preview.months.join('、')||'—'}</dd></div></dl><label>{t('输入 DELETE_IMPORT 确认','確認として DELETE_IMPORT を入力')}<Input value={confirmation} onChange={event=>setConfirmation(event.target.value)} autoComplete="off" required/></label>{notice&&<SuccessNotice>{notice}</SuccessNotice>}{error&&<Alert className="error" role="alert">{error}</Alert>}<div className="dialog-actions"><Button type="button" variant="outline" disabled={busy} onClick={()=>{setPreview(null);}}>{t('取消','キャンセル')}</Button><Button type="submit" disabled={busy||confirmation!=='DELETE_IMPORT'}>{t(busy?'正在删除…':'确认删除',busy?'削除中…':'削除する')}</Button></div></Form>}</DialogContent></Dialog>
 </section>;
}
