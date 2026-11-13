import { Inject, Injectable } from '@nestjs/common';
import type { PoolClient } from 'pg';
import { z } from 'zod';
import type { McpActor } from './mcp';
import { McpReadError } from './mcp-read';
import { BusinessError } from './errors';
import { SelfReportService } from './self-report';
import { StationTasksService } from './station-tasks';
import { jstToday, validDate } from './business-dates';

const text={type:'string'},count={type:'integer',minimum:0,maximum:999},tokenSchema={type:'string',pattern:'^[a-f0-9]{64}$'};
const itemsSchema={type:['array','null'],items:{type:'object',properties:{task_id:{type:'integer',minimum:1},pct:{type:['integer','null'],minimum:0,maximum:100},note:text,store_count:{type:['integer','null'],minimum:0},token:tokenSchema},required:['task_id'],additionalProperties:false}};
const annotations={readOnlyHint:false,destructiveHint:true,idempotentHint:true,openWorldHint:false};
export const mcpTaskWriteTools=[
 {name:'visit_task_assign',title:'任务派工或回收担当',description:'队长仅本队、管理员可派工；person_codes为最多两名有效本队成员，空数组回收担当且保留进度。需write scope，可选token防覆盖。',inputSchema:{type:'object' as const,properties:{task_id:{type:'integer',minimum:1},person_codes:{type:['array','null'],items:{type:'string'},maxItems:2},token:tokenSchema},required:['task_id'],additionalProperties:false},annotations},
 {name:'visit_task_transfer',title:'任务转队',description:'队长将本队未完成任务转给有效其他队。保留进度、清除原担当并通知接收队长和被移出者。需write scope；整个批次同事务，失败全部回滚。可选tokens按任务编号提供版本。',inputSchema:{type:'object' as const,properties:{task_ids:{type:'array',items:{type:'integer',minimum:1},minItems:1,maxItems:500},to_team_id:{type:'integer',minimum:1},tokens:{type:'object',additionalProperties:tokenSchema}},required:['task_ids','to_team_id'],additionalProperties:false},annotations},
 {name:'visit_task_confirm',title:'确认或驳回进展',description:'队长确认本队今日进展；task_id确认一条，all_today=true确认今日有权复核的全部待确认，reject=true且pct为0–99时驳回已完成任务。保留原上报值及当前审核权限。需write scope；可选token/tokens防覆盖。',inputSchema:{type:'object' as const,properties:{task_id:{type:['integer','null'],minimum:1},all_today:{type:'boolean',default:false},reject:{type:'boolean',default:false},pct:{type:['integer','null'],minimum:0,maximum:99},note:{type:'string',default:''},token:tokenSchema,tokens:{type:'object',additionalProperties:tokenSchema}},additionalProperties:false},annotations},
 {name:'visit_task_return',title:'退回车站池',description:'仅管理员撤回已派队且没有担当的任务，保留进度；有担当或已在池中则跳过并说明原因。需write scope；可选tokens防覆盖，执行及通知审计同事务。',inputSchema:{type:'object' as const,properties:{task_ids:{type:'array',items:{type:'integer',minimum:1},minItems:1,maxItems:500},tokens:{type:'object',additionalProperties:tokenSchema}},required:['task_ids'],additionalProperties:false},annotations},
 {name:'visit_self_report',title:'提交今日自报',description:'绑定本人提交今日1点/2点店数和任务进度，同事务。需write scope；items=[{task_id,pct,note,store_count}]；队员完成100%必须给store_count（可为0）。可选token/items.token防覆盖；未提供时在作业锁内取当前版本。跨队/确认锁定/原始数据覆盖会拒绝。',inputSchema:{type:'object' as const,properties:{area:{...text,default:''},p1_cnt:{...count,default:0},p2_cnt:{...count,default:0},items:itemsSchema,token:tokenSchema},additionalProperties:false},annotations},
 {name:'visit_task_report',title:'上报任务进展',description:'本人担当或本队队长上报/调整单条任务；需write scope。task_id、pct必填，note可选。队员100%完成必须给store_count（可0），队长可省。保留任务权限、当天确认锁和版本保护，可选token防覆盖。',inputSchema:{type:'object' as const,properties:{task_id:{type:'integer',minimum:1},pct:{type:'integer',minimum:0,maximum:100},note:{...text,default:''},store_count:{type:['integer','null'],minimum:0},token:tokenSchema},required:['task_id','pct'],additionalProperties:false},annotations},
 {name:'visit_day_report',title:'指定员工日报',description:'仅管理员：person工号精确或姓名包含；date默认今日JST。action=view查询已有日报及可编辑进展（只读Token可用）；save写入需write scope和p1_cnt/p2_cnt。无日报只能补点数，不能补进展。可选include_version=true读取token供save和items使用；未提供时作业锁内取当前版本。',inputSchema:{type:'object' as const,properties:{person:text,date:{type:['string','null']},action:{type:'string',enum:['view','save'],default:'view'},area:{type:['string','null']},p1_cnt:{type:['integer','null'],minimum:0,maximum:999},p2_cnt:{type:['integer','null'],minimum:0,maximum:999},items:itemsSchema,token:tokenSchema,include_version:{type:'boolean',default:false}},required:['person'],additionalProperties:false},annotations},
];
export function isMcpTaskWrite(tool:string,args:Record<string,unknown>){return ['visit_task_assign','visit_task_transfer','visit_task_confirm','visit_task_return'].includes(tool)||tool==='visit_self_report'||tool==='visit_task_report'||tool==='visit_day_report'&&args.action!==undefined&&args.action!=='view';}
const item=z.object({task_id:z.number().int().positive().max(2147483647),pct:z.number().int().min(0).max(100).nullable().optional(),note:z.string().trim().max(4000).default(''),store_count:z.number().int().min(0).max(2147483647).nullable().optional(),token:z.string().regex(/^[a-f0-9]{64}$/).optional()}).strict();
const report=z.object({area:z.string().max(4096).default(''),p1_cnt:z.number().int().min(0).max(999).default(0),p2_cnt:z.number().int().min(0).max(999).default(0),items:z.array(item).max(1000).nullable().optional(),token:z.string().regex(/^[a-f0-9]{64}$/).optional()}).strict();
function parse<T>(schema:z.ZodType<T>,input:unknown):T{const p=schema.safeParse(input);if(!p.success)throw new McpReadError('BAD_PARAM','请检查日报店数、任务进度、日期和版本参数');return p.data;}
const reviewLabels:Record<string,string>={pending:'待确认',confirmed:'已确认',adjusted:'已调整',rejected:'已驳回','':'未上报'};
@Injectable()
export class McpTaskWriteService{
 constructor(@Inject(SelfReportService)private readonly reports:SelfReportService,@Inject(StationTasksService)private readonly tasks:StationTasksService){}
 async run(client:PoolClient,actor:McpActor,tool:string,args:Record<string,unknown>):Promise<Record<string,unknown>>{
  if(tool==='visit_task_return'&&actor.role!=='admin'||['visit_task_assign','visit_task_transfer','visit_task_confirm'].includes(tool)&&!['admin','leader'].includes(actor.role))throw new McpReadError('FORBIDDEN_TOOL','没有该作业管理权限');
  if(tool==='visit_day_report'&&actor.role!=='admin')throw new McpReadError('FORBIDDEN_TOOL','该工具仅管理员可用');
  if(isMcpTaskWrite(tool,args)&&!actor.scopes.includes('write'))throw new McpReadError('FORBIDDEN_TOOL','当前Token没有写权限','请使用包含write scope的凭据');
  try{
   if(['visit_task_assign','visit_task_transfer','visit_task_confirm','visit_task_return'].includes(tool))return await this.manage(client,actor,tool,args);
   if(tool==='visit_task_report')return await this.progress(client,actor,args);
   if(tool==='visit_self_report'){
    if(!actor.personCode)throw new McpReadError('BAD_PARAM','账号未绑定人员');
    const input={...args};for(const key of ['person','person_code','person_id']){if(key in input&&input[key]!==actor.personCode)throw new McpReadError('FORBIDDEN_TOOL','不能替他人提交日报');delete input[key];}
    const data=parse(report,input);const {created,...result}=await this.submit(client,actor,data);return result;
   }
   if(tool==='visit_day_report')return await this.day(client,actor,args);
   throw new McpReadError('NOT_FOUND','工具不存在');
  }catch(error){if(error instanceof BusinessError)throw new McpReadError(error.code==='VALIDATION'?'BAD_PARAM':error.code,error.message,error.getStatus()===409?'先查询最新状态后重试':'');throw error;}
 }
 private async manage(client:PoolClient,actor:McpActor,tool:string,args:Record<string,unknown>){
  const id=z.number().int().positive().max(2147483647),version=z.string().regex(/^[a-f0-9]{64}$/),versions=z.record(z.string().regex(/^[1-9][0-9]*$/),version).optional();
  await client.query("SELECT pg_advisory_xact_lock(hashtext('teams-write'),0)");
  if(tool==='visit_task_assign'){
   const input=parse(z.object({task_id:id,person_codes:z.array(z.string().trim().min(1).max(32)).max(2).nullable().optional(),token:version.optional()}).strict(),args);
   const before=await this.tasks.progressViewInTransaction(client,input.task_id,actor.uid);
   const result=await this.tasks.changeInTransaction(client,input.task_id,actor.uid,{action:'assign',codes:input.person_codes??[]},input.token??before.token);
   const after=await this.tasks.progressViewInTransaction(client,input.task_id,actor.uid);
   return {task_id:input.task_id,n_assignees:new Set(input.person_codes??[]).size,state:after.task.state,warnings:result.warnings??[]};
  }
  if(tool==='visit_task_confirm'){
   const input=parse(z.object({task_id:id.nullable().optional(),all_today:z.boolean().default(false),reject:z.boolean().default(false),pct:z.number().int().min(0).max(99).nullable().optional(),note:z.string().trim().max(4000).default(''),token:version.optional(),tokens:versions}).strict(),args);
   if(input.reject){
    if(!input.task_id||input.pct==null)throw new McpReadError('BAD_PARAM','驳回需要task_id和0–99的pct');
    const before=await this.tasks.progressViewInTransaction(client,input.task_id,actor.uid);
    const result=await this.tasks.changeInTransaction(client,input.task_id,actor.uid,{action:'reject',pct:input.pct,note:input.note},input.token??input.tokens?.[input.task_id]??before.token);
    return {task_id:input.task_id,pct:result.pct,state:result.state};
   }
   if(!input.all_today&&!input.task_id)throw new McpReadError('BAD_PARAM','需要task_id或all_today=true');
   const ids=input.all_today?(await client.query(`SELECT p.task_id FROM bd_task_progress p JOIN bd_task t ON t.id=p.task_id WHERE p.progress_date=$1 AND p.reported_pct IS NOT NULL AND p.review_status='pending'
    AND ($2='admin' OR EXISTS(SELECT 1 FROM bd_team_member m JOIN bd_team team ON team.id=m.team_id WHERE m.team_id=t.team_id AND m.person_code=$3 AND m.role='leader' AND m.end_date IS NULL AND m.start_date<=$1 AND team.status='active')) ORDER BY p.task_id`,[jstToday(),actor.role,actor.personCode])).rows.map(row=>row.task_id):[input.task_id!];
   let confirmed=0,lastPct:number|undefined;
   for(const taskId of ids){
    const before=await this.tasks.progressViewInTransaction(client,taskId,actor.uid);
    if(input.all_today&&(!before.permission.adjust||!before.permission.report))continue;
    const result=await this.tasks.changeInTransaction(client,taskId,actor.uid,{action:'confirm',pct:before.task.pct,note:''},input.tokens?.[taskId]??input.token??before.token);
    confirmed++;lastPct=result.pct;
   }
   return input.all_today?{confirmed}:{confirmed,task_id:input.task_id,pct:lastPct};
  }
  const input=parse(z.object({task_ids:z.array(id).min(1).max(500),to_team_id:tool==='visit_task_transfer'?id:z.never().optional(),tokens:versions}).strict(),args);
  const ids=[...new Set(input.task_ids)].sort((a,b)=>a-b);
  const before=[];for(const taskId of ids)before.push({id:taskId,view:await this.tasks.progressViewInTransaction(client,taskId,actor.uid)});
  if(tool==='visit_task_return'){
   let returned=0;const skipped:{task_id:number;why:string}[]=[];
   for(const entry of before){
    if(!entry.view.permission.returnPool){skipped.push({task_id:entry.id,why:entry.view.task.team_id===null?'还没派给队伍':'还有担当，请让队长先回收或改派'});continue;}
    await this.tasks.changeInTransaction(client,entry.id,actor.uid,{action:'return-pool'},input.tokens?.[entry.id]??entry.view.token);returned++;
   }
   return {returned,skipped};
  }
  const target=(await client.query("SELECT name FROM bd_team WHERE id=$1 AND status='active'",[input.to_team_id])).rows[0];
  if(!target)throw new McpReadError('TEAM_UNAVAILABLE','目标团队不存在或已停用');
  const fromTeams=new Set(before.map(entry=>entry.view.task.team_id));
  if(fromTeams.size!==1)throw new McpReadError('BAD_PARAM','一次转队只能来自同一个队');
  const source=(await client.query('SELECT name FROM bd_team WHERE id=$1',[[...fromTeams][0]])).rows[0];
  const removed=(await client.query('SELECT DISTINCT person_code FROM bd_task_assign WHERE task_id=ANY($1::int[]) ORDER BY person_code',[before.filter(entry=>entry.view.task.state!=='done'&&entry.view.task.pct<100).map(entry=>entry.id)])).rows.map(row=>row.person_code);
  const picked=before.filter(entry=>entry.view.task.state!=='done'&&entry.view.task.pct<100);
  if(!picked.length)throw new McpReadError('TRANSFER_COMPLETED','已完成的任务不能转');
  const stationNames=new Map((await client.query("SELECT t.id,coalesce(p.name,s.name,'') AS name FROM bd_task t LEFT JOIN bd_station_place p ON p.id=t.place_id LEFT JOIN bd_station s ON s.id=t.station_id WHERE t.id=ANY($1::int[])",[picked.map(entry=>entry.id)])).rows.map(row=>[row.id,row.name]));
  let cleared=0;const labels:string[]=[];
  for(const entry of picked){const result=await this.tasks.changeInTransaction(client,entry.id,actor.uid,{action:'transfer',teamId:input.to_team_id!},input.tokens?.[entry.id]??entry.view.token);cleared+=result.cleared??0;labels.push(stationNames.get(entry.id)??'');}
  return {transferred:picked.length,to_team:target.name,from_team:source?.name??'',cleared,removed_people:removed,labels};
 }
 private async submit(client:PoolClient,actor:McpActor,input:z.infer<typeof report>,personCode?:string,date=jstToday()){
  await client.query("SELECT pg_advisory_xact_lock(hashtext('teams-write'),0)");
  const view=await this.reports.viewInTransaction(client,actor.uid,personCode,date);
  const items=(input.items||[]).map(entry=>({taskId:entry.task_id,pct:entry.pct,note:entry.note,...entry.store_count!=null?{storeCount:entry.store_count}:{},token:entry.token??view.items.find(row=>row.taskId===entry.task_id)?.token??'0'.repeat(64)}));
  const result=await this.reports.submitInTransaction(client,actor.uid,{area:input.area,p1:input.p1_cnt,p2:input.p2_cnt,items,token:input.token??view.token},personCode,date);
  return {date:result.date,p1_cnt:input.p1_cnt,p2_cnt:input.p2_cnt,tasks_reported:result.tasks,tasks_skipped:result.skipped,created:result.created};
 }
 private async progress(client:PoolClient,actor:McpActor,args:Record<string,unknown>){
  const input=parse(item.required({pct:true}),args);if(input.pct==null)throw new McpReadError('BAD_PARAM','pct必填');
  await client.query("SELECT pg_advisory_xact_lock(hashtext('teams-write'),0)");
  const view=await this.tasks.progressViewInTransaction(client,input.task_id,actor.uid);
  const admin=view.permission.admin,leader=view.permission.leader;
  await this.tasks.reportInTransaction(client,input.task_id,actor.uid,{pct:input.pct,note:input.note,...input.store_count!=null?{storeCount:input.store_count}:{}},input.token??view.token,jstToday(),admin||leader);
  const current=await this.tasks.progressViewInTransaction(client,input.task_id,actor.uid);
  return {task_id:input.task_id,pct:current.task.pct,state:current.task.state,review_status:current.progress?.review_status??null,store_count:current.task.store_count};
 }
 private async day(client:PoolClient,actor:McpActor,args:Record<string,unknown>){
  const input=parse(z.object({person:z.string().trim().min(1).max(128),date:z.string().refine(value=>validDate(value)&&!value.startsWith('0000')).nullable().optional(),action:z.enum(['view','save']).default('view'),area:z.string().max(4096).nullable().optional(),p1_cnt:z.number().int().min(0).max(999).nullable().optional(),p2_cnt:z.number().int().min(0).max(999).nullable().optional(),items:z.array(item).max(1000).nullable().optional(),token:z.string().regex(/^[a-f0-9]{64}$/).optional(),include_version:z.boolean().default(false)}).strict(),args);
  const day=input.date??jstToday();
  const person=(await client.query("SELECT code,display_name FROM persons WHERE code=$1 OR strpos(display_name,$1)>0 ORDER BY (code=$1) DESC,code LIMIT 1",[input.person])).rows[0];
  if(!person)throw new McpReadError('NOT_FOUND','未找到人员');
  if(input.action==='save'){
   if(input.p1_cnt==null||input.p2_cnt==null)throw new McpReadError('BAD_PARAM','save必须给p1_cnt和p2_cnt');
   const result=await this.submit(client,actor,{area:input.area??'',p1_cnt:input.p1_cnt,p2_cnt:input.p2_cnt,items:input.items,token:input.token},person.code,day);
   return {person_code:person.code,name:person.display_name||person.code,date:result.date,'1点':input.p1_cnt,'2点':input.p2_cnt,tasks_changed:result.tasks_reported,created:result.created,skipped:result.tasks_skipped,hint:'created=true = 那天原来没有自报（补录，只写了点数）'};
  }
  const view=await this.reports.viewInTransaction(client,actor.uid,person.code,day),rep=view.report;
  return {person_code:person.code,name:person.display_name||person.code,date:day,is_backfill:view.backfill,report:rep?{area:rep.area||'',p1_cnt:rep.p1,p2_cnt:rep.p2,total_cnt:rep.p1+rep.p2,source:rep.source}:null,
   progress:view.items.map(row=>({task_id:row.taskId,station:row.station,line:row.line,pct:row.dayPct,reported_pct:row.reportedPct,review_status:row.review,review_label:reviewLabels[row.review]??row.review,editable:row.editable,note:row.note,...input.include_version?{token:row.token}:{}})),
   ...input.include_version?{token:view.token}:{},hint:view.backfill?'is_backfill=true = 那天还没有自报：只能补录点数，不能补录任务进展':'editable=false 的进展 = 队长还没处理过，管理员暂不能改'};
 }
}
