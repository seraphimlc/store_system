import {taskPlanData} from './task-plan-import-data';
export interface TaskPlanLayout{header_row:number;cols:Record<string,number>;n_cols:number;source:string}
export interface TaskPlanAssets{stations:{placeId:number;name:string;line:string;operatorShort:string}[];teams:{id:number;name:string;status:string}[];tasks:{id:number;placeId:number|null;teamId:number|null}[]}
export interface TaskPlanRow{row_no:number;line:string;station_raw:string;team_raw:string;source:string;status:string;done_date:string;note:string;team_id:number|null;team_name:string;kind:string;hits:[number,string,string][];missing:string[];near:string[];create:[number,string,string][];existing:[number,string,string,string][];skip_reason:string;decision:string}
const norm=(value:unknown)=>String(value??'').normalize('NFKC').trim();
const cmp=(a:string,b:string)=>a<b?-1:a>b?1:0;
const kana=(value:string)=>taskPlanData._KANA_FOLD.reduce((text,[from,to])=>text.replaceAll(from,to),norm(value));
function normLine(value:string){let text=norm(value);for(const junk of ['沿線','沿線(東京~高尾)'])text=text.replaceAll(junk,'');for(const [open,close]of [['(',')'],['（','）']])while(text.includes(open)&&text.includes(close)){const a=text.indexOf(open),b=text.indexOf(close);if(a>b)break;text=(text.slice(0,a)+text.slice(b+1)).trim();}text=text.replace(/\s/g,'');return (taskPlanData.LINE_ALIAS as Record<string,string>)[text]??text;}
function split(value:string){return [...new Set(norm(value).replaceAll('/','／').replaceAll('、','／').replaceAll('，','／').split('／').map(v=>v.trim()).filter(Boolean))];}
function variants(value:string){const result=[value];for(const [open,close]of taskPlanData._ANNOT_PAIRS)if(value.includes(open)&&value.endsWith(close)){const base=value.slice(0,value.indexOf(open)).trim();if(base&&!result.includes(base))result.push(base);}return result.filter(Boolean);}
export class TaskPlanError extends Error{}
export function detectTaskPlanLayout(rows:unknown[][]):TaskPlanLayout{
 let best:{score:number;exact:number;row:number;cols:Record<string,number>}|undefined;
 for(let i=0;i<Math.min(rows.length,12);i++){
  const found=new Map<string,{score:number;col:number}>();
  for(const [j,cell]of rows[i].entries()){
   const text=norm(cell);if(!text)continue;
   for(const [key,synonyms]of Object.entries(taskPlanData.COL_SYNONYMS)){
    const score=(synonyms as readonly string[]).includes(text)?3:synonyms.some(word=>text.includes(word))?1:0;
    if(!score||score===1&&['line','station'].includes(key)&&['编号','序号','ID','コード'].some(word=>text.includes(word)))continue;
    if(!found.has(key)||score>found.get(key)!.score)found.set(key,{score,col:j});
   }
  }
  const cols=Object.fromEntries([...found].map(([key,value])=>[key,value.col])),score=found.size,exact=[...found.values()].filter(value=>value.score===3).length;
  if(cols.station!==undefined&&(!best||score>best.score||score===best.score&&exact>best.exact))best={score,exact,row:i+1,cols};
 }
 if(!best)throw new TaskPlanError('认不出表头：前12行需要车站及负责队列');
 return {header_row:best.row,cols:best.cols,n_cols:Math.max(0,...rows.map(row=>row.length)),source:'auto'};
}
export function buildTaskPlan(grid:unknown[][],assets:TaskPlanAssets,manual?:TaskPlanLayout){
 const layout=manual??detectTaskPlanLayout(grid),rows:TaskPlanRow[]=[];
 if(!Number.isInteger(layout.cols.station)||layout.cols.station<0)throw new TaskPlanError('列映射缺少车站列');
 const teams=assets.teams.filter(team=>team.status==='active'),teamNames=new Map(assets.teams.map(team=>[team.id,team.name]));
 const resolveTeam=(raw:string)=>{const exact=teams.find(team=>norm(team.name)===raw);if(exact)return exact;const matches=teams.filter(team=>raw.includes(norm(team.name))||norm(team.name).includes(raw));return matches.length===1?matches[0]:undefined;};
 for(let i=layout.header_row;i<grid.length;i++){
  const cell=(key:string)=>layout.cols[key]===undefined?'':norm(grid[i][layout.cols[key]]),station=cell('station');if(!station)continue;
  const rawTeam=cell('team'),team=rawTeam?resolveTeam(rawTeam):undefined;
  rows.push({row_no:i+1,line:cell('line'),station_raw:station,team_raw:rawTeam,source:cell('source'),status:cell('status'),done_date:cell('done_date'),note:cell('note'),team_id:team?.id??null,team_name:team?.name??'',kind:'new',hits:[],missing:[],near:[],create:[],existing:[],skip_reason:'',decision:''});
 }
 if(!rows.length)throw new TaskPlanError('表头下没有车站数据行');
 if(!rows.some(row=>row.team_id))throw new TaskPlanError('文件里认不出负责队：请提供可匹配系统活跃团队的负责队列');
 const byLine=new Map<string,Map<string,number>>(),byName=new Map<string,Set<number>>(),names=new Map<number,string>(),lineNames=new Map<number,string[]>();
 for(const station of assets.stations){
  if(!station.placeId||!station.name)continue;
  names.set(station.placeId,station.name);const lines=lineNames.get(station.placeId)??[];if(station.line&&!lines.includes(station.line))lines.push(station.line);lineNames.set(station.placeId,lines);
  const key=kana(station.name);for(const line of [station.line,(station.operatorShort||'')+(station.line||'')]){const normed=normLine(line),map=byLine.get(normed)??new Map();map.set(key,station.placeId);byLine.set(normed,map);}
  const ids=byName.get(key)??new Set();ids.add(station.placeId);byName.set(key,ids);
 }
 const match=(line:string,name:string):[number|null,string]=>{
  const key=kana(name),parts=line.replaceAll('，','、').replaceAll('・','、').split('、').map(norm).filter(Boolean);
  for(const part of parts){const hit=byLine.get(normLine(part))?.get(key);if(hit)return [hit,'strict'];}
  for(const part of parts){const core=normLine(part).replace(/^[0-9]+/,'').replaceAll('号線','');let found:number|undefined;
   for(const [line,map]of byLine){const base=line.replace(/^[0-9]+/,'').replaceAll('号線','');if(map.has(key)&&core&&(base===core||core.length>=3&&(base.includes(core)||core.includes(base)))){if(found&&found!==map.get(key))return [null,'宽松匹配命中多个线路'];found=map.get(key);}}
   if(found)return [found,'loose'];
  }
  const candidates=byName.get(key);if(candidates?.size===1)return [[...candidates][0],'name-only'];return [null,candidates?.size?'同名多站('+candidates.size+')':'车站表里找不到'];
 };
 const taskByPlace=new Map(assets.tasks.filter(task=>task.placeId).map(task=>[task.placeId,task]));
 for(const row of rows){
  if(!row.team_id){row.decision='skip';row.skip_reason=row.team_raw?`负责队「${row.team_raw}」在系统里对不上`:'这一行没写负责队';continue;}
  if(row.done_date||['已完成','完了','完成済'].some(word=>row.status.includes(word))){row.decision='skip';row.skip_reason=`表上标为已完成（${row.done_date||row.status}）`;continue;}
  for(const part of split(row.station_raw)){let hit:[number,string,string]|undefined;for(const candidate of variants(part)){const [id,how]=match(row.line,candidate);if(id){hit=[id,how,candidate];break;}}if(!hit)row.missing.push(part);else if(!row.hits.some(value=>value[0]===hit![0]))row.hits.push(hit);}
  if(row.missing.length||!row.hits.length){row.decision='bad';const key=kana(variants(split(row.station_raw)[0]??row.station_raw).at(-1)??'');row.near=[...byName.keys()].filter(name=>key&&(key.includes(name)||name.includes(key))).sort(cmp).slice(0,8);continue;}
  row.kind=['原任务保留','原有任务','已分配保留'].some(word=>row.source.includes(word))?'keep':'new';row.decision='skip';
  for(const [id]of row.hits){const name=names.get(id)??'',lines=lineNames.get(id)??[],line=lines.slice(0,3).join('／')+(lines.length>3?'…':''),task=taskByPlace.get(id);if(task)row.existing.push([id,name,teamNames.get(task.teamId!)||'未分配',line]);else if(row.kind==='new')row.create.push([id,name,line]);else row.skip_reason='标为「原任务保留」但库里没有这个任务';}
  if(row.create.length)row.decision='create';else if(row.existing.length){row.decision='skip';if(!row.skip_reason)row.skip_reason='已有任务，不改归属';}
 }
 const creates=rows.filter(row=>row.decision==='create').flatMap(row=>row.create);
 return {layout,rows,summary:{rows:rows.length,create_rows:rows.filter(row=>row.decision==='create').length,create_stations:new Set(creates.map(row=>row[0])).size,existing:rows.reduce((total,row)=>total+row.existing.length,0),skipped:rows.filter(row=>row.decision==='skip').length,bad:rows.filter(row=>row.decision==='bad').length,teams:[...new Set(rows.filter(row=>row.team_id).map(row=>row.team_name))].sort(cmp)},team_names_found:[...new Set(rows.map(row=>norm(row.team_raw)).filter(Boolean))].sort(cmp)};
}
export function taskPlanRows(plan:ReturnType<typeof buildTaskPlan>){return plan.rows.flatMap<Record<string,unknown>>(row=>row.decision==='create'?[{row:row.row_no,decision:'create',line:row.line,station:row.station_raw,team:row.team_name,stations:row.create.map(([station_id,name,line])=>({station_id,name,line}))}]:row.decision==='bad'?[{row:row.row_no,decision:'bad',line:row.line,station:row.station_raw,missing:row.missing,near:row.near}]:[]).slice(0,400);}
