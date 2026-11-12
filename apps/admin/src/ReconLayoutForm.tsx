import {Form} from '@appica/ui-react/form';
import {AdminSelect} from './AdminSelect';
import {Input} from '@appica/ui-react/input';
import {useState,type FormEvent} from 'react';
import {Button} from '@appica/ui-react/button';
export function ReconLayoutForm({language,busy,onApply}:{language:string;busy:boolean;onApply:(layout:unknown)=>Promise<void>}){
 const t=(zh:string,ja:string)=>language==='ja'?ja:zh;
 const [identity,setIdentity]=useState('code');
 function submit(event:FormEvent<HTMLFormElement>){
  event.preventDefault();const values=new FormData(event.currentTarget),columns:Record<string,number>={};
  for(const key of ['date','store','person','points','visible','deploy']){const value=String(values.get(key)??'').trim();if(value)columns[key==='person'?identity:key]=Number(value)-1;}
  void onApply({sheet:String(values.get('sheet')??'').trim(),headerRow:Number(values.get('headerRow')),columns});
 }
 return <details className="recon-layout"><summary>{t('指定列映射修复逐日明细','列を指定して日別明細を修復')}</summary>
  <p>{t('填写原文件的工作表、表头行和列位置。行列均从1开始（A列=1、B列=2）。只修改读取位置，不修改原文件或点数规则；该映射会直接用于本次重试。','元ファイルのシート名、ヘッダー行、列位置を入力します。行・列は1始まり（A列=1、B列=2）です。読み取り位置のみ変更し、元ファイルやポイント規則は変更しません。この指定で再試行します。')}</p>
  <Form onSubmit={submit}><fieldset className="recon-layout-fields" disabled={busy}>
   <label>{t('工作表名称','シート名')}<Input name="sheet" required maxLength={255}/></label>
   <label>{t('表头行号','ヘッダー行番号')}<Input name="headerRow" type="number" min={1} max={14} defaultValue={1} required/></label>
   <label>{t('人员标识类型','スタッフ識別方法')}<AdminSelect value={identity} onChange={event=>setIdentity(event.target.value)}><option value="code">{t('员工编号','スタッフ番号')}</option><option value="name">{t('员工姓名','スタッフ氏名')}</option></AdminSelect></label>
   {(['date','store','person','points','visible','deploy'] as const).map(key=><label key={key}>{t(...({date:['日期列号','日付列番号'],store:['店铺编号列号','店舗番号列番号'],person:['人员列号','スタッフ列番号'],points:['单店点数列号（可选）','店舗ポイント列番号（任意）'],visible:['可见标记列号（可选）','可視フラグ列番号（任意）'],deploy:['投放标记列号（可选）','設置フラグ列番号（任意）']} as Record<string,[string,string]>)[key])}<Input name={key} type="number" min={1} max={128} required={['date','store','person'].includes(key)}/></label>)}
  </fieldset><Button type="submit" disabled={busy}>{t(busy?'正在重试…':'使用该布局重试',busy?'再試行中…':'指定した列で再試行')}</Button></Form>
 </details>;
}
