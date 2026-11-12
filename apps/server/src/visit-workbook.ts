import ExcelJS from 'exceljs';
import {orderedWorkbook} from './xlsx-container';
import {validDate} from './business-dates';
export interface VisitLayout {parse_request?:string;sheet_name?:string;ai_model?:string;ai_tokens?:number;header_row?:number;cols?:Partial<Record<'store_id'|'store_name'|'modified_time'|'submitter'|'visible'|'deploy',number>>;value_map?:{visible?:Record<string,string>;deploy?:Record<string,number>};point_rules?:{visible?:string[]|null;deploy?:string[]|null;points:number}[];source?:string;rules_source?:string}
export interface ParsedVisit {sheet_name:string;excel_row:number;store_id_raw:string;store_name_local_raw:string;store_name_en_raw:string;modified_raw:string;submitter_raw:string;submitter_code:string|null;record_id_raw:string;visible_raw:string;deploy_raw:string;original_row:unknown[]}
interface Sheet {name:string;headers:unknown[][];maxColumn:number;rows:number;ruleLines:string[]}
interface Reader extends AsyncIterable<ExcelJS.Row>{name:string}
export class VisitParseError extends Error{constructor(public readonly code:string,message:string){super(message);}}
function simple(value:ExcelJS.CellValue|undefined):unknown{
 if(value==null)return null;
 if(value instanceof Date)return value.toISOString().replace('T',' ').replace(/\.000Z$/,'').replace(/Z$/,'');
 if(typeof value==='object'){
  if('result'in value)return simple(value.result);
  if('richText'in value)return value.richText.map(part=>part.text).join('');
  if('text'in value)return value.text;
  if('error'in value)return value.error;
  return null;
 }
 return value;
}
function raw(value:unknown){if(value==null)return '';if(typeof value==='boolean')return value?'True':'False';return String(value);}
function values(row:ExcelJS.Row,maxColumn?:number){const cells=row.values as ExcelJS.CellValue[];const n=maxColumn??Math.max(0,cells.length-1);return Array.from({length:n},(_,index)=>simple(cells[index+1]));}
const deployNames=['Deploy New A+POSM','NEW A+ POSM'],visibleNames=['A+ POSM Visible','Review status'];
const required=['Store ID','Store Name-Local','Modified Time','Submitter'];
function header(sheet:Sheet){for(let index=0;index<3;index++){const mapping=new Map<string,number>();for(const [column,value]of (sheet.headers[index]??[]).entries()){const text=raw(value).trim();if(text&&!mapping.has(text))mapping.set(text,column+1);}if(mapping.has('Store ID'))return {row:index+1,mapping};}return null;}
function workbook(path:string){return new ExcelJS.stream.xlsx.WorkbookReader(path,{entries:'emit',sharedStrings:'cache',styles:'cache',hyperlinks:'ignore',worksheets:'emit'}) as unknown as AsyncIterable<Reader>;}
export function parseSubmitter(value:string){const normalized=value.normalize('NFKC').trim();const found=/^(.+)\((\d+)\)$/.exec(normalized);return found?{name:found[1].trim(),code:found[2].trim()}:null;}
export function validModified(value:string){const text=value.trim();const match=/^(\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2}):(\d{2})$/.exec(text);return !!match&&validDate(match[1])&&Number(match[2])<24&&Number(match[3])<60&&Number(match[4])<60;}
export function defaultVisitLayout(env=process.env):VisitLayout{
 const parseMap=(raw:string)=>Object.fromEntries(raw.split(',').filter(part=>part.includes('=')).map(part=>{const i=part.indexOf('=');return [part.slice(0,i).trim().replace(/^~$/,''),part.slice(i+1).trim()];}));
 const visible=parseMap(env.DEFAULT_VISIBLE_MAP??'AUDIT_SUCCESS=candidate, AUDIT_FAILED=candidate, OTHER=candidate, YES=candidate, NO=candidate, NOT_REQUEST=blank');
 const deployed=parseMap(env.DEFAULT_DEPLOY_MAP??'');const deploy=Object.fromEntries(Object.entries(deployed).filter(([,value])=>/^\d+$/.test(value)).map(([key,value])=>[key,Number(value)]));
 const split=(part:string|undefined)=>{if(part===undefined)return null;const list=part.split('|').map(part=>part.trim()).filter(Boolean).map(part=>part==='~'?'':part);return list.includes('*')||!list.length?null:list;};
 const point_rules=(env.DEFAULT_POINT_RULES??'*&YES=2, OTHER|AUDIT_SUCCESS|YES|NO&*=1').replaceAll('\r','').split(/[\n,]+/).flatMap(line=>{const i=line.lastIndexOf('=');if(i<0||!/^\d+$/.test(line.slice(i+1).trim()))return [];const left=line.slice(0,i).trim().split('&');return [{visible:split(left[0]),deploy:split(left[1]),points:Number(line.slice(i+1).trim())}];});
 return {source:'default',value_map:{...(Object.keys(visible).length?{visible}:{}),...(Object.keys(deploy).length?{deploy}:{})},...(point_rules.length?{point_rules}:{})};
}
export function rulesFromText(lines:string[]){
 let yes:number|undefined,no:number|undefined,zero:number|undefined,pending=false;let zeroStatuses:string[]=[];
 for(const rawLine of lines){const line=rawLine.trim();if(!line)continue;if(line.includes('不计分')){if(/AUDITING|NOT_REQUEST/.test(line))pending=true;continue;}
  let points:number|undefined;for(const pattern of [/计\s*([0-9]+)\s*点/,/=\s*([0-9]+)\s*点?/,/([0-9]+)\s*点/]){const match=pattern.exec(line);if(match){points=Number(match[1]);break;}}if(points===undefined)continue;
  if(line.includes('AUDIT_SUCCESS')){if(line.includes('YES')&&yes===undefined)yes=points;if((line.includes('NO')||line.includes('空白'))&&no===undefined)no=points;}
  else if(/AUDIT_FAILED|OTHER/.test(line)){zero=points;zeroStatuses=['AUDIT_FAILED','OTHER'].filter(status=>line.includes(status));}
 }
 if(yes===undefined||no===undefined||zero===undefined||!pending)return null;
 return [{visible:['AUDIT_SUCCESS'],deploy:['YES'],points:yes},{visible:['AUDIT_SUCCESS'],deploy:['NO'],points:no},{visible:['AUDIT_SUCCESS'],deploy:[''],points:no},...(zeroStatuses.length?zeroStatuses:['AUDIT_FAILED','OTHER']).flatMap(status=>['YES','NO',''].map(deploy=>({visible:[status],deploy:[deploy],points:zero!})))];
}
export function cleanAddress(value:unknown){return raw(value).replace(/[\r\n]+/g,' ').trim().replace(/\s+/g,' ').replace(/\s+NULL\s*$/i,'').replace(/^日本[、,]\s*/,'');}
export function validStoreAddress(value:string,name=''){
 const address=cleanAddress(value),compact=(text:string)=>text.replace(/[\s　,，、]+/g,'').toLowerCase();
 if(address.length<6||compact(address)===compact(name)||/^https?:\/\//i.test(address)||/^(?:東京都|北海道|(?:京都|大阪)府|.{2,3}県)$/.test(address))return false;
 const number=/[0-9０-９一二三四五六七八九十百]/.test(address);
 return number&&((/(?:東京都|北海道|(?:京都|大阪)府|.{2,3}県)/.test(address)&&/[市区町村郡丁目]/.test(address))||/\b(tokyo|osaka|kyoto|hokkaido|japan|city|ward|prefecture)\b/i.test(address));
}
export interface WorkbookResult {format:string;parsed_sheets:string[];ignored_sheets:{name:string;rows:number;reason:string}[];header_row:number;data_start_row:number;total_rows:number;parsed_rows:number;warnings:string[];layout:VisitLayout;months:string[];people:Map<string,string>;addresses:Map<string,string>;addressConflicts:number}
/** Two streaming passes: discovery and row emission. Callers spool private JSONL before the DB write transaction. */
async function parseOrderedWorkbook(path:string,emit:(row:ParsedVisit)=>Promise<void>,layout?:VisitLayout,options:{defaults?:boolean}={}):Promise<WorkbookResult>{
 const sheets:Sheet[]=[];let scanned=0;
 for await(const worksheet of workbook(path)){
  const sheet:Sheet={name:worksheet.name,headers:[],maxColumn:0,rows:0,ruleLines:[]};let empty=0,stopped=false;
  for await(const row of worksheet){if(++scanned>600000)throw new VisitParseError('WORKBOOK_TOO_LARGE','工作簿行数超过处理上限');const cells=values(row);sheet.maxColumn=Math.max(sheet.maxColumn,cells.length);if(sheet.maxColumn>1000)throw new VisitParseError('WORKBOOK_TOO_WIDE','工作簿列数超过处理上限');
   if(row.number<=20)sheet.headers[row.number-1]=cells;
   if(row.number<=60&&/[规则说明]/.test(sheet.name))for(const value of cells)if(value!=null&&raw(value).trim())sheet.ruleLines.push(raw(value));
   const nonempty=cells.some(value=>value!==null&&raw(value).trim()!=='');if(!stopped){if(nonempty){sheet.rows++;empty=0;}else if(++empty>=5)stopped=true;}
  }sheets.push(sheet);if(sheets.length>100)throw new VisitParseError('WORKBOOK_TOO_MANY_SHEETS','工作簿工作表过多');
 }
 if(!sheets.length)throw new VisitParseError('WORKBOOK_EMPTY','工作簿没有可解析的工作表');
 const defaults=options.defaults===false?{}:defaultVisitLayout();
 const used:VisitLayout={...defaults,...layout,value_map:layout?.value_map??defaults.value_map,point_rules:layout?.point_rules??defaults.point_rules};
 if(!layout||layout.source==='ai'){const rules=rulesFromText(sheets.filter(sheet=>/规则|说明/.test(sheet.name)).slice(0,3).flatMap(sheet=>sheet.ruleLines));if(rules){used.point_rules=rules;used.rules_source='sheet';}}
 const templates=sheets.filter(sheet=>sheet.name.trim().toUpperCase()==='STORE_TASK_EXCEL_SHEET');let chosen:Sheet[]=[];
 if(templates.length)chosen=templates;
 else if(used.header_row!==undefined)chosen=sheets.filter(sheet=>sheet.rows>0&&(!used.sheet_name||sheet.name===used.sheet_name)).slice(0,1);
 else{const first=sheets.find(sheet=>{const found=header(sheet);return found&&required.every(name=>found.mapping.has(name))&&deployNames.some(name=>found.mapping.has(name));});if(first)chosen=[first];}
 if(!chosen.length)throw new VisitParseError('HEADER_NOT_FOUND','未找到模板工作表或含必需列的可用工作表');
 const result:WorkbookResult={format:'other',parsed_sheets:[],ignored_sheets:sheets.filter(sheet=>!chosen.includes(sheet)).map(sheet=>({name:sheet.name,rows:sheet.rows,reason:templates.length?'非模板 sheet，忽略':'非模板/缺必需列，忽略'})),header_row:0,data_start_row:0,total_rows:0,parsed_rows:0,warnings:templates.length>1?[`发现 ${templates.length} 个模板 sheet，将全部并入候选池`]:templates.length?[]:['未找到模板 sheet，已回退使用含必需列的 sheet'],layout:used,months:[],people:new Map(),addresses:new Map(),addressConflicts:0};
 const addressCandidates=new Map<string,Set<string>>();
 for await(const worksheet of workbook(path)){
  const selected=chosen.find(sheet=>sheet.name===worksheet.name);const meta=sheets.find(sheet=>sheet.name===worksheet.name)!;let columns:Record<string,number|undefined>={},headerRow=0;
  if(selected){const found=header(selected);headerRow=used.header_row??found?.row??0;const map=used.header_row?new Map<string,number>():found?.mapping??new Map<string,number>();const override=used.cols??{};
   columns={store_id:override.store_id??map.get('Store ID'),store_name:override.store_name??map.get('Store Name-Local'),modified_time:override.modified_time??map.get('Modified Time'),submitter:override.submitter??map.get('Submitter'),visible:override.visible??visibleNames.map(name=>map.get(name)).find(Boolean),deploy:override.deploy??deployNames.map(name=>map.get(name)).find(Boolean),english:map.get('Store Name-English'),record:map.get('Record ID')};
   if(!headerRow||Object.entries(columns).some(([key,value])=>!['english','record'].includes(key)&&!value))throw new VisitParseError('REQUIRED_COLUMNS_MISSING','工作表缺少店铺、日期、人员、可见性或投放列');
   result.parsed_sheets.push(worksheet.name);result.header_row=headerRow;result.data_start_row=headerRow+1;result.format=headerRow===2?'wide50':'flat9';
  }
  const normalized=(cells:unknown[])=>cells.map(value=>raw(value).trim().toLowerCase().replace(/\s+/g,' '));
  let addressHeader:{row:number;id:number;name:number;columns:number[]}|undefined;
  for(const [i,cells]of meta.headers.entries())if(cells){const labels=normalized(cells),id=labels.findIndex(name=>['store basic id','store id','store code','storecode','店舗id','店铺id','店舖id'].includes(name));const addresses=labels.map((name,index)=>({name,index})).filter(({name})=>['store address-local','store address','store address-english','store address local','store address english','shop address','住所','地址','店舗住所','店铺地址','店舖地址'].includes(name)).sort((a,b)=>Number(!(/local|住所|地址/.test(a.name)))-Number(!(/local|住所|地址/.test(b.name))));if(id>=0&&addresses.length){addressHeader={row:i+1,id,name:labels.findIndex(name=>['store name-local','store name local','shop name','store name','店舗名','店铺名','店舖名','店名'].includes(name)),columns:addresses.map(value=>value.index)};break;}}
  for await(const row of worksheet){
   const cells=values(row,meta.maxColumn);
   if(addressHeader&&row.number>addressHeader.row){const sid=raw(cells[addressHeader.id]).trim(),name=raw(cells[addressHeader.name]).trim();if(sid){for(const column of addressHeader.columns){const address=cleanAddress(cells[column]);if(validStoreAddress(address,name)){const set=addressCandidates.get(sid)??new Set<string>();set.add(address);addressCandidates.set(sid,set);break;}}}}
   if(!selected||row.number<=headerRow||cells.every(value=>value==null||raw(value).trim()===''))continue;
   const get=(key:string)=>columns[key]?raw(cells[columns[key]!-1]):'';
   const modified=get('modified_time');if(!validModified(modified))throw new VisitParseError('MODIFIED_INVALID',`行${row.number}: Modified Time必须为有效的19位日期时间`);
   if(!result.months.includes(modified.trim().slice(0,7)))result.months.push(modified.trim().slice(0,7));
   const submitter=get('submitter'),person=parseSubmitter(submitter);if(person){if(person.code.length>32)throw new VisitParseError('PERSON_CODE_TOO_LONG',`行${row.number}: 员工编号超过32位`);if(!result.people.has(person.code))result.people.set(person.code,person.name);}else if(submitter&&result.warnings.length<100)result.warnings.push(`行${row.number}: Submitter无法解析编号，按未识别处理`);
   let deploy=get('deploy').trim();const map=used.value_map?.deploy;if(map&&Object.keys(map).length){const points=map[deploy];deploy=Number.isInteger(points)?points>=2?'YES':points===1?'NO':'':'';}else if(!['YES','NO',''].includes(deploy))deploy='';
   await emit({sheet_name:worksheet.name,excel_row:row.number,store_id_raw:get('store_id'),store_name_local_raw:get('store_name'),store_name_en_raw:get('english'),modified_raw:modified,submitter_raw:submitter,submitter_code:person?.code??null,record_id_raw:get('record'),visible_raw:get('visible').trim(),deploy_raw:deploy,original_row:cells});result.parsed_rows++;result.total_rows++;
  }
 }
 for(const [id,addresses]of addressCandidates)if(addresses.size===1)result.addresses.set(id,[...addresses][0]);else result.addressConflicts++;
 return result;
}

export async function parseVisitWorkbook(path:string,emit:(row:ParsedVisit)=>Promise<void>,layout?:VisitLayout,options:{defaults?:boolean}={}){const ordered=await orderedWorkbook(path);try{return await parseOrderedWorkbook(ordered.path,emit,layout,options);}finally{await ordered.cleanup();}}

export async function* readWorkbookRows(path:string){const ordered=await orderedWorkbook(path);try{let scanned=0;for await(const sheet of workbook(ordered.path))for await(const row of sheet){if(++scanned>600000)throw new VisitParseError('WORKBOOK_TOO_LARGE','工作簿行数超过处理上限');const cells=values(row);if(cells.length>1000)throw new VisitParseError('WORKBOOK_TOO_WIDE','工作簿列数超过处理上限');yield {sheet:sheet.name,row:row.number,values:cells};}}finally{await ordered.cleanup();}}
