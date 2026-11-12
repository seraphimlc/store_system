import {reconPreview,validateReconLayout,inferReconLayout,type ReconLayout} from './recon-layout-model';import type {ReportModel} from './report-model';
import {readWorkbookRows,parseSubmitter} from './visit-workbook';import {validDate} from './business-dates';import {aggregateReconDays,integerPoints,type ReconDay} from '../../../packages/domain/src/reconciliation';
export type ReconKind='daily_records'|'person_points'|'store_list';
interface Header {sheet:string;row:number;columns:Record<string,number|undefined>}
interface Person {code:string;name:string;inMonth?:boolean}
export interface ReconParsed {kind:ReconKind;days:ReconDay[];report:Map<string,{name:string;points:number;count:number}>;unmatched:{name:string;store:string;date:string}[];singleDate:string|null;layout?:ReconLayout;layoutSource?:'rule'|'explicit'|'model'}
const keys:Record<string,string[]>={date:['statement date','modified time','记录日期','日期'],store:['store basic id','store id','店舗id','店铺id','storecode'],name:['agent name','submitter','提交人','员工姓名','姓名'],code:['员工编号','submitter code','employee code','person code','员工id'],points:['store total amount','标准点数','标准点','点数','total amount'],visible:['a+ posm visible','visible'],deploy:['deploy new a+posm','new a+ posm','new a+posm','deploy'],month:['settle month','结算月','月份']};
const text=(value:unknown)=>value==null?'':String(value).trim();
function col(headers:string[],aliases:string[],exclude:string[]=[]){const index=headers.findIndex(value=>!exclude.some(word=>value.toLowerCase().includes(word))&&aliases.some(word=>value.toLowerCase().includes(word)));return index<0?undefined:index;}
function daily(headers:string[]){const columns=Object.fromEntries(Object.entries(keys).map(([key,names])=>[key,col(headers,names,key==='visible'?['existing']:key==='deploy'?['existing','visible']:[])]));return columns.store!==undefined&&columns.date!==undefined&&(columns.name!==undefined||columns.code!==undefined)?columns:null;}
function storeList(headers:string[]){const columns={month:col(headers,keys.month),store:col(headers,keys.store),name:col(headers,['employee name','姓名','提交人']),points:col(headers,['store total amount','点数','总点数'])};return columns.store!==undefined&&columns.name!==undefined&&columns.points!==undefined?columns:null;}
export async function parseReconWorkbook(path:string,month:string,people:Person[],requested?:ReconKind,layout?:ReconLayout):Promise<ReconParsed>{
 const dailyHeaders:Header[]=[],storeHeaders:Header[]=[],personHeaders:Header[]=[];let firstSheet:string|undefined;
 for await(const row of readWorkbookRows(path)){
  firstSheet??=row.sheet;if(row.row>12)continue;const headers=row.values.map(text),d=daily(headers),s=storeList(headers);
  if(d&&row.row<=10&&!dailyHeaders.some(value=>value.sheet===row.sheet))dailyHeaders.push({sheet:row.sheet,row:row.row,columns:d});
  if(s&&!storeHeaders.some(value=>value.sheet===row.sheet))storeHeaders.push({sheet:row.sheet,row:row.row,columns:s});
  if(row.sheet===firstSheet&&headers.some(value=>value.includes('姓名')||value.includes('点数'))&&!personHeaders.length){const name=headers.findIndex(value=>value.includes('姓名')),code=headers.findIndex(value=>value.includes('编号')),points=headers.findIndex(value=>value.includes('总点')||value==='点数');if(name>=0&&points>=0)personHeaders.push({sheet:row.sheet,row:row.row,columns:{name,code:code<0?undefined:code,points}});}
 }
 if(layout){if(requested&&requested!=='daily_records')throw new Error('RECON_LAYOUT_KIND_MISMATCH');layout=validateReconLayout(layout,await reconPreview(path));}
 const kind=layout?'daily_records':requested??(dailyHeaders.length?'daily_records':personHeaders.length?'person_points':storeHeaders.length?'store_list':null);
 if(!kind)throw new Error('RECON_HEADER_NOT_FOUND');
 const header:Header|undefined=layout?{sheet:layout.sheet,row:layout.headerRow,columns:layout.columns}:kind==='daily_records'?(dailyHeaders.find(value=>/最终有效|有效明细|有效数据/.test(value.sheet))??dailyHeaders[0]):kind==='store_list'?storeHeaders[0]:personHeaders[0];
 if(!header)throw new Error('RECON_REQUIRED_COLUMNS_MISSING');
 const byCode=new Map(people.map(person=>[person.code,person]));const byName=new Map<string,Person[]>();for(const person of people){const list=byName.get(person.name)??[];list.push(person);byName.set(person.name,list);}
 const unmatched:ReconParsed['unmatched']=[],report=new Map<string,{name:string;points:number;count:number}>();const days:ReconDay[]=[];
 function resolve(name:string,code:string){if(code)return byCode.has(code)?code:code;const parsed=parseSubmitter(name);if(parsed)return parsed.code;const candidates=byName.get(name)??[];if(candidates.length===1)return candidates[0].code;const current=candidates.filter(person=>person.inMonth);if(current.length===1)return current[0].code;if(candidates.length>1)throw new Error('RECON_AMBIGUOUS_PERSON');return '';}
 for await(const row of readWorkbookRows(path)){
  if(row.sheet!==header.sheet||row.row<=header.row)continue;const get=(key:string)=>header.columns[key]===undefined?null:row.values[header.columns[key]!];
  const sid=text(get('store'));if(kind==='daily_records'&&!sid)continue;
  const name=text(get('name'));if(!name&&kind!=='daily_records')continue;
  let day=month+'-01';
  if(kind==='daily_records'){day=text(get('date')).slice(0,10);if(!validDate(day)||!day.startsWith(month))continue;if(header.columns.visible!==undefined&&!['YES','NO'].includes(text(get('visible')).toUpperCase()))continue;}
  if(kind==='store_list'){const selected=text(get('month'));if(selected&&!selected.startsWith(month))continue;}
  const code=resolve(name,text(get('code')));if(!code){if(unmatched.length<30)unmatched.push({name,store:sid,date:day});continue;}
  let points=integerPoints(get('points'));
  if(points===null&&kind==='daily_records')points=day>='2026-07-09'&&text(get('deploy')).toUpperCase()==='YES'?2:1;
  if(points===null)continue;
  const display=byCode.get(code)?.name||parseSubmitter(name)?.name||name||code;
  if(kind==='person_points')report.set(code,{name:display,points,count:1});
  else{const previous=report.get(code);report.set(code,{name:display,points:(previous?.points??0)+points,count:(previous?.count??0)+1});days.push({code,name:display,date:day,points,count:1});}
 }
 const aggregated=kind==='person_points'?[...report].map(([code,value])=>({code,name:value.name,date:month+'-01',points:value.points,count:1})):aggregateReconDays(days);
 if(!report.size)throw new Error(unmatched.length?'RECON_NO_MATCHED_PEOPLE':'RECON_NO_ROWS');
 const dates=new Set(aggregated.map(row=>row.date));return {kind,report,days:aggregated,unmatched,...layout?{layout}:{},singleDate:kind==='store_list'?month+'-01':kind==='daily_records'&&dates.size===1?[...dates][0]:null};
}

export async function parseReconWorkbookAdaptive(path:string,month:string,people:Person[],requested?:ReconKind,options:{layout?:ReconLayout;model?:ReportModel;onInferred?:(layout:ReconLayout)=>Promise<void>}={}):Promise<ReconParsed>{
 if(options.layout)return {...await parseReconWorkbook(path,month,people,requested,options.layout),layoutSource:'explicit'};
 try{return {...await parseReconWorkbook(path,month,people,requested),layoutSource:'rule'};}
 catch(error){
  if(!(error instanceof Error)||!['RECON_HEADER_NOT_FOUND','RECON_REQUIRED_COLUMNS_MISSING'].includes(error.message)||requested&&requested!=='daily_records'||process.env.RECON_LAYOUT_AI==='0'||!process.env.AI_API_KEY)throw error;
  let layout:ReconLayout;try{layout=await inferReconLayout(path,options.model);}catch{throw new Error('RECON_LAYOUT_AI_FAILED');}
  await options.onInferred?.(layout);
  return {...await parseReconWorkbook(path,month,people,'daily_records',layout),layoutSource:'model'};
 }
}
