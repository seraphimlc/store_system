import {createHash} from 'node:crypto';
import type {Comparison} from './report-comparison';
export const REPORT_PROMPT_VERSION=3;
export type ReportLanguage='zh'|'ja';
export interface ModelResponse {data:unknown;tokens:number;model:string}
export type ReportModel=(prompt:string,language:ReportLanguage,idempotencyKey:string)=>Promise<ModelResponse>;
function canonical(value:unknown):string{
 if(Array.isArray(value))return '['+value.map(canonical).join(', ')+']';
 if(value&&typeof value==='object')return '{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+': '+canonical((value as Record<string,unknown>)[key])).join(', ')+'}';
 return JSON.stringify(value);
}
export function reportFingerprint(res:Comparison){
 const payload={v:REPORT_PROMPT_VERSION,start:res.start,end:res.end,counts:res.counts,persons:res.persons.map(p=>[p.person_code,p.sys_p1,p.sys_p2,p.rep_p1,p.rep_p2]),daily:res.daily.map(d=>[d.person_code,d.date,d.kind,d.sys_total,d.rep_total,d.dt])};
 return createHash('sha256').update(canonical(payload)).digest('hex').slice(0,16);
}
export interface ReportComment {overall_comment:string;accuracy_notes:string;per_person:Record<string,{name:string;comment:string;off_days:{date:string;delta:unknown;note:string}[];questions:string[]}>}
function text(value:unknown,max=4000){return String(value??'').slice(0,max);}
export function normalizeReport(input:unknown,res:Comparison):ReportComment{
 if(!input||typeof input!=='object'||Array.isArray(input))throw new Error('MODEL_INVALID_JSON');
 const data=input as Record<string,unknown>;const known=new Map(res.persons.map(p=>[p.person_code,p.name]));
 const out:ReportComment={overall_comment:text(data.overall_comment),accuracy_notes:text(data.accuracy_notes),per_person:Object.create(null)};
 if(data.per_person&&typeof data.per_person==='object'&&!Array.isArray(data.per_person))for(const [code,raw]of Object.entries(data.per_person)){
  if(!known.has(code)||!raw||typeof raw!=='object'||Array.isArray(raw))continue;
  const block=raw as Record<string,unknown>;const actualDays=new Set(res.daily.filter(day=>day.person_code===code&&(day.kind!=='both'||day.dt)).map(day=>day.date));
  const off_days=(Array.isArray(block.off_days)?block.off_days:[]).slice(0,30).filter(day=>day&&typeof day==='object'&&actualDays.has(String(day.date))).map(day=>({date:String(day.date),delta:day.delta,note:text(day.note,300)}));
  out.per_person[code]={name:known.get(code)!,comment:text(block.comment),off_days,questions:(Array.isArray(block.questions)?block.questions:[]).slice(0,10).map(value=>text(value,300))};
 }
 if(!out.overall_comment&&!Object.keys(out.per_person).length)throw new Error('MODEL_EMPTY_REPORT');
 return out;
}
export function reportPrompt(res:Comparison,language:ReportLanguage){
 const labels=language==='ja'?{system:'システム',report:'自己申告',delta:'差分',accuracy:'正確率',filled:'申告日数',gaps:'要申告未申告',total:'店舗数'}:{system:'系统',report:'自报',delta:'差值',accuracy:'准确率',filled:'实填天数',gaps:'应填未填',total:'店数'};
 const people=res.persons.slice(0,20).map(p=>({person_code:p.person_code,name:p.name,[labels.system+labels.total]:p.sys_total,[labels.report+labels.total]:p.rep_total,[labels.delta]:p.d_total,[labels.accuracy]:p.acc,[labels.filled]:p.days_filled,[labels.gaps]:p.gaps}));
 const daily=res.daily.filter(day=>day.kind!=='both'||day.dt).slice(0,60).map(day=>({person_code:day.person_code,date:day.date,kind:day.kind,[labels.delta]:day.dt}));
 const rules=language==='ja'?'日本の巡回業務の照合結果を日本語で説明してください。数値はすべて計算済みです。差分はシステム−自己申告。正は過少申告、負は過大申告です。未申告日は正確率に含めません。新しい数値や人物を推測せず、個人への非難や断定を避け、確認事項として述べてください。':'请用中文解释日本巡店业务的核对结果。数字均由程序算好，差值=系统−自报，正数为少报、负数为多报；漏填日不进入准确率。不要计算或推测新数字，不做人身评价或定性指控，只提出核实问题。';
 return rules+'\n'+JSON.stringify({start:res.start,end:res.end,summary:res.summary,persons:people,anomalies:daily})+'\nReturn strict JSON only: {"overall_comment":"≤3 sentences","accuracy_notes":"≤2 sentences","per_person":{"<known person_code>":{"comment":"≤2 sentences","off_days":[{"date":"YYYY-MM-DD","delta":0,"note":""}],"questions":[""]}}}. Use only given person codes and anomaly dates. No markdown.';
}
export const configuredJsonModel:ReportModel=async(prompt,language,idempotencyKey)=>{
 const key=process.env.AI_API_KEY;if(!key)throw new Error('AI_NOT_CONFIGURED');
 const base=new URL((process.env.AI_BASE_URL||'https://api.openai.com/v1').replace(/\/?$/,'/'));
 if(!['https:','http:'].includes(base.protocol)||base.username||base.password)throw new Error('AI_CONFIG_INVALID');
 const timeout=Math.max(5000,Math.min(120000,Number(process.env.NEXT_REPORT_AI_TIMEOUT_MS)||60000));
 let failed:unknown;
 for(let attempt=0;attempt<2;attempt++){
  try{
   const response=await fetch(new URL('chat/completions',base),{method:'POST',headers:{Authorization:'Bearer '+key,'Content-Type':'application/json','Idempotency-Key':idempotencyKey},body:JSON.stringify({model:process.env.AI_MODEL||'gpt-4o-mini',messages:[{role:'user',content:prompt}],max_tokens:Math.max(512,Math.min(16000,Number(process.env.NEXT_REPORT_AI_MAX_TOKENS)||4096)),temperature:0.2}),signal:AbortSignal.timeout(timeout)});
   if(!response.ok){await response.body?.cancel();if((response.status===429||response.status>=500)&&attempt===0)continue;throw new Error('AI_HTTP_'+response.status);}
   if(Number(response.headers.get('content-length'))>1000000){await response.body?.cancel();throw new Error('AI_RESPONSE_TOO_LARGE');}
   const reader=response.body?.getReader();if(!reader)throw new Error('AI_EMPTY_RESPONSE');const chunks:Uint8Array[]=[];let bytes=0;
   for(;;){const part=await reader.read();if(part.done)break;bytes+=part.value.byteLength;if(bytes>1000000){await reader.cancel();throw new Error('AI_RESPONSE_TOO_LARGE');}chunks.push(part.value);}
   const data=JSON.parse(Buffer.concat(chunks).toString('utf8'));const raw=data.choices?.[0]?.message?.content;
   if(typeof raw!=='string')throw new Error('AI_INVALID_CONTENT');const json=raw.trim().replace(/^```(?:json)?\s*/,'').replace(/\s*```$/,'');
   return {data:JSON.parse(json),tokens:Number.isFinite(Number(data.usage?.total_tokens))&&Number(data.usage?.total_tokens)>0?Math.floor(Number(data.usage.total_tokens)):Math.ceil((prompt.length+raw.length)/3),model:process.env.AI_MODEL||'gpt-4o-mini'};
  }catch(error){failed=error;if(attempt===1||error instanceof SyntaxError||String((error as Error).message).startsWith('AI_'))break;}
 }
 throw new Error(failed instanceof Error&&/^AI_[A-Z0-9_]+$/.test(failed.message)?failed.message:'AI_REQUEST_FAILED');
};

export const configuredReportModel:ReportModel=async(...args)=>{if(process.env.VISIT_REPORT_AI==='0')throw new Error('AI_DISABLED');return configuredJsonModel(...args);};
