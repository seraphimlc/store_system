/** Legacy settlement judging; independent from ops, persistence, and AI. */
export interface SettlementRecord {id:number;importId:number;storeId:string|null;name:string|null;modified:string|null;visible:string|null;deploy:string|null;filteredBy?:number|null}
export interface ImportRules {visible?:Record<string,string>|null;pointRules?:unknown[]|null}
export type CleanStatus='valid'|'master_late'|'from_sub'|'cross_file_dup'|'visible_blank'|'no_ref';
export interface Judgement {id:number;status:CleanStatus;reason:string|null;confirmation:'auto_ok'|'auto_approved';filteredBy:number|null}
export function settlementName(value:string){return value.normalize('NFKC').replace(/[ \u3000]/g,'').toLowerCase();}
export function storeEntityName(value:string){return value.trim();}
export function visibleCandidate(value:string|null,rules?:Record<string,string>|null){const text=(value??'').trim();return rules&&Object.keys(rules).length>0?rules[text]==='candidate':text!=='';}
export function settlementPoints(day:string|null,deploy:string|null,visible='',pointRules?:unknown[]|null){
 if(!day||!/^\d{4}-\d{2}-\d{2}$/.test(day)||!Number.isFinite(Date.parse(day+'T00:00:00Z'))||new Date(day+'T00:00:00Z').toISOString().slice(0,10)!==day)return 0;
 if(pointRules?.length){for(const raw of pointRules){if(!raw||typeof raw!=='object'||Array.isArray(raw))continue;const rule=raw as Record<string,unknown>;
  if(Array.isArray(rule.visible)&&!rule.visible.includes(visible))continue;if(Array.isArray(rule.deploy)&&!rule.deploy.includes(deploy??''))continue;
  if(typeof rule.points==='number'&&Number.isInteger(rule.points)&&rule.points>=0&&rule.points<=9)return rule.points;
 }return 0;}
 return day<'2026-07-09'?1:deploy==='YES'?2:1;
}
export function judgeSettlement(records:SettlementRecord[],imports:Map<number,ImportRules>,importId:number){
 const best=new Map<string,{id:number;store:string;modified:string;rank:number}>();
 for(const row of records){if(!visibleCandidate(row.visible,imports.get(row.importId)?.visible))continue;const name=(row.name??'').trim();if(!name||!row.modified)continue;
  const month=row.modified.slice(0,7),key=JSON.stringify([month>='2026-09'?settlementName(name):name,month]);
  const rank=month<'2026-09'?0:(row.deploy??'').trim()==='YES'?0:['OTHER','AUDIT_SUCCESS','YES','NO'].includes((row.visible??'').trim())?1:2;
  const previous=best.get(key);if(!previous||rank<previous.rank||rank===previous.rank&&row.modified<previous.modified)best.set(key,{id:row.id,store:(row.storeId??'').trim(),modified:row.modified,rank});
 }
 return records.filter(row=>row.importId===importId).map((row):Judgement=>{
  const store=(row.storeId??'').trim(),name=(row.name??'').trim();
  if(!store||!name)return {id:row.id,status:'no_ref',reason:'no_ref',confirmation:'auto_ok',filteredBy:row.filteredBy??null};
  if(!visibleCandidate(row.visible,imports.get(row.importId)?.visible))return {id:row.id,status:'visible_blank',reason:null,confirmation:'auto_ok',filteredBy:row.filteredBy??null};
  const month=(row.modified??'').slice(0,7),winner=best.get(JSON.stringify([month>='2026-09'?settlementName(name):name,month]));
  if(!winner||winner.id===row.id)return {id:row.id,status:'valid',reason:null,confirmation:'auto_approved',filteredBy:null};
  const status=winner.store!==store?'from_sub':winner.modified.slice(0,10)===(row.modified??'').slice(0,10)?'cross_file_dup':'master_late';
  return {id:row.id,status,reason:status,confirmation:'auto_ok',filteredBy:winner.id};
 });
}
