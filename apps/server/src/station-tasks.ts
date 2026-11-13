import {movementRequest,movementQuery,movementStamp,uniformProgressRequest} from './station-movement-contract';
import {reviewQuery,reviewStamp,confirmReviews,reopenReviews} from './station-review-contract';
import {Body,Controller,Get,Inject,Injectable,Param,Post,Query,Req} from '@nestjs/common';import type {Request} from 'express';import type {PoolClient} from 'pg';import {z} from 'zod';import {createHash} from 'node:crypto';import {Database} from './database';import {AuthService} from './auth';import {BusinessError} from './errors';import {jstToday} from './business-dates';import {stationTaskState,staffProgressLocked,adminMayAdjust} from '../../../packages/domain/src/station-tasks';
const identity=z.coerce.number().int().positive().max(2147483647),pct=z.number().int().min(-2147483648).max(2147483647).transform(value=>Math.max(0,Math.min(100,value))),note=z.string().trim().max(4000).default('');
const actionSchema=z.discriminatedUnion('action',[
 z.object({action:z.literal('transfer'),teamId:identity}).strict(),
 z.object({action:z.literal('return-pool')}).strict(),
 z.object({action:z.literal('dispatch'),teamId:identity.nullable(),date:z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional()}).strict(),
 z.object({action:z.literal('assign'),codes:z.array(z.string().trim().min(1).max(32)).max(2)}).strict(),
 z.object({action:z.enum(['report','confirm','adjust','reject']),pct,note,storeCount:z.number().int().min(0).max(2147483647).optional()}).strict()
]);
type Action=z.infer<typeof actionSchema>;type Actor={id:number;username:string;name:string;code:string|null;role:string;status:string};
function parse<T>(schema:z.ZodType<T>,value:unknown){const result=schema.safeParse(value);if(!result.success)throw new BusinessError('VALIDATION','车站任务参数无效',400);return result.data;}
const digest=(value:unknown)=>createHash('sha256').update(JSON.stringify(value)).digest('hex');
@Injectable()
export class StationTasksService{
 constructor(@Inject(Database)private readonly db:Database){}
 private async actor(client:PoolClient,id:number,write=false):Promise<Actor>{const row=(await client.query<Actor>(`SELECT id,username,display_name AS name,person_code AS code,role,status FROM users WHERE id=$1 AND is_active=true AND status IN('active','leave') ${write?'FOR SHARE':''}`,[id])).rows[0];if(!row)throw new BusinessError('FORBIDDEN','账号状态已变化，任务未执行',403);return row;}
 private async caps(client:PoolClient,role:string){const defaults:Record<string,boolean>={'task.view_team':role==='leader','task.report':role==='leader'||role==='staff','task.assign':role==='leader','task.adjust':role==='leader'};for(const row of(await client.query('SELECT capability,allowed FROM bd_role_cap WHERE role=$1',[role])).rows)defaults[row.capability]=row.allowed;return defaults;}
 private async snapshot(client:PoolClient,id:number,onDate=jstToday()){const task=(await client.query(`SELECT t.*,t.assign_date::text AS "assignDate",t.start_date::text AS "startDate",t.done_date::text AS "doneDate",coalesce(p.name,s.name,'#'||t.id::text) AS label FROM bd_task t LEFT JOIN bd_station_place p ON p.id=t.place_id LEFT JOIN bd_station s ON s.id=t.station_id WHERE t.id=$1`,[id])).rows[0];if(!task)throw new BusinessError('NOT_FOUND','车站任务不存在',404);const assigned=(await client.query('SELECT person_code AS code,assigned_by AS by,assigned_at AS at,dispatch_date::text AS date FROM bd_task_assign WHERE task_id=$1 ORDER BY person_code',[id])).rows;const latest=(await client.query('SELECT *,progress_date::text AS date FROM bd_task_progress WHERE task_id=$1 ORDER BY progress_date DESC LIMIT 1',[id])).rows[0]??null;const progress=(await client.query('SELECT * FROM bd_task_progress WHERE task_id=$1 AND progress_date=$2',[id,onDate])).rows[0]??null;return {task,assigned,progress,latest,token:digest({task,assigned,progress,latest})};}
 private async permission(client:PoolClient,actor:Actor,state:Awaited<ReturnType<StationTasksService['snapshot']>>){const membership=(await client.query("SELECT m.role FROM bd_team_member m JOIN bd_team t ON t.id=m.team_id WHERE m.team_id=$1 AND m.person_code=$2 AND m.end_date IS NULL AND m.start_date<=$3 AND t.status='active'",[state.task.team_id,actor.code,jstToday()])).rows[0];const caps=await this.caps(client,actor.role),admin=actor.role==='admin',leader=actor.role==='leader'&&membership?.role==='leader',assigned=!!membership&&state.assigned.some(row=>row.code===actor.code);return {admin,leader,assigned,transfer:leader&&state.task.team_id!==null&&state.task.state!=='done'&&state.task.pct<100,returnPool:admin&&state.task.team_id!==null&&state.assigned.length===0,view:admin||leader&&!!caps['task.view_team']||assigned,assign:admin||leader&&!!caps['task.assign'],report:admin||!!caps['task.report']&&(leader||assigned&&!staffProgressLocked(state.progress?.review_status)),adjust:leader||admin&&adminMayAdjust(state.progress?.review_status),reject:admin||leader&&state.task.pct===100&&state.latest?.reported_pct===100&&state.latest?.review_status==='pending'};}
 private async log(client:PoolClient,actor:Actor,task:any,action:string,field:string,old:unknown,next:unknown,note=''){await client.query('INSERT INTO bd_log(domain,ref_id,ref_label,action,field,old_value,new_value,actor,actor_name,note,created_at) VALUES(\'task\',$1,$2,$3,$4,$5,$6,$7,$8,$9,now())',[task.id,String(task.label).slice(0,128),action,field,String(old??''),String(next??''),actor.username,actor.name,note]);}
 async list(actorId:number,page=1,q='',tab=''){return this.db.transaction(async client=>{await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');const actor=await this.actor(client,actorId),caps=await this.caps(client,actor.role);return (await client.query(`WITH visible AS(SELECT t.*,EXISTS(SELECT 1 FROM bd_task_assign own WHERE own.task_id=t.id AND own.person_code=$1) AS mine,(SELECT max(own.dispatch_date)::date FROM bd_task_assign own WHERE own.task_id=t.id AND own.person_code=$1) AS dispatch_day,EXISTS(SELECT 1 FROM bd_task_progress pr WHERE pr.task_id=t.id AND pr.review_status='pending') AS pending_review,coalesce(p.name,s.name,'#'||t.id::text) AS label FROM bd_task t LEFT JOIN bd_station_place p ON p.id=t.place_id LEFT JOIN bd_station s ON s.id=t.station_id WHERE ($2='admin' OR EXISTS(SELECT 1 FROM bd_team_member m JOIN bd_team team ON team.id=m.team_id WHERE m.team_id=t.team_id AND m.person_code=$1 AND m.end_date IS NULL AND m.start_date<=$3 AND team.status='active' AND (($2='leader' AND m.role='leader' AND $4) OR EXISTS(SELECT 1 FROM bd_task_assign a WHERE a.task_id=t.id AND a.person_code=$1))))),filtered AS(SELECT * FROM visible WHERE ($5='' OR label ILIKE $5) AND ($7='' OR ($7='mine' AND mine AND state<>'done') OR ($7='done' AND state='done') OR ($7='unassigned' AND state='unassigned') OR ($7='doing' AND state='doing') OR ($7='pending' AND pending_review))),paged AS(SELECT id,label,team_id AS "teamId",assign_date::text AS date,dispatch_day::text AS "dispatchDate",state,pct,store_count AS "storeCount" FROM filtered ORDER BY id DESC OFFSET $6 LIMIT 30)SELECT (SELECT count(*)::int FROM filtered) AS total,coalesce((SELECT json_agg(p) FROM paged p),'[]') AS items,
(SELECT json_build_object('mine',count(*) FILTER(WHERE mine AND state<>'done'),'done',count(*) FILTER(WHERE state='done'),'unassigned',count(*) FILTER(WHERE state='unassigned'),'doing',count(*) FILTER(WHERE state='doing'),'pending',count(*) FILTER(WHERE pending_review)) FROM visible) AS counts,
(SELECT json_build_object('today',count(*) FILTER(WHERE mine AND state<>'done' AND dispatch_day=$3::date),'carried',count(*) FILTER(WHERE mine AND state<>'done' AND dispatch_day<$3::date),'longest',coalesce(max($3::date-dispatch_day) FILTER(WHERE mine AND state<>'done' AND dispatch_day<$3::date),0)) FROM visible) AS "dispatchSummary"`,[actor.code,actor.role,jstToday(),!!caps['task.view_team'],q?'%'+q+'%':'',(page-1)*30,tab])).rows[0];});}
 async detail(id:number,actorId:number){return this.db.transaction(async client=>{await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');const actor=await this.actor(client,actorId),state=await this.snapshot(client,id),permission=await this.permission(client,actor,state);if(!permission.view)throw new BusinessError('FORBIDDEN','不能查看其他团队或非本人担当的任务',403);return {...state,permission,transferTargets:permission.transfer?(await client.query("SELECT id,name FROM bd_team WHERE status='active' AND id<>$1 ORDER BY name,id",[state.task.team_id])).rows:[],history:(await client.query('SELECT progress_date::text AS date,pct,note,submitted_by AS "submittedBy",reported_pct AS "reportedPct",reported_by AS "reportedBy",review_status AS "reviewStatus",reviewed_by AS "reviewedBy",review_note AS "reviewNote" FROM bd_task_progress WHERE task_id=$1 ORDER BY progress_date DESC LIMIT 31',[id])).rows,logs:(await client.query("SELECT id,action,field,old_value AS old,new_value AS next,actor_name AS actor,note,created_at AS at FROM bd_log WHERE domain='task' AND ref_id=$1 ORDER BY id DESC LIMIT 100",[id])).rows,candidates:permission.assign?(await client.query("SELECT m.person_code AS code,coalesce(p.display_name,m.person_code) AS name,u.status FROM bd_team_member m LEFT JOIN persons p ON p.code=m.person_code LEFT JOIN LATERAL(SELECT status FROM users WHERE person_code=m.person_code ORDER BY id DESC LIMIT 1)u ON true WHERE m.team_id=$1 AND m.end_date IS NULL AND m.start_date<=$2 ORDER BY p.display_name,m.person_code",[state.task.team_id,jstToday()])).rows:[]};});}
 async places(actorId:number,q:string){return this.db.transaction(async client=>{const actor=await this.actor(client,actorId);if(actor.role!=='admin')throw new BusinessError('FORBIDDEN','仅管理员可查看车站资产',403);return {items:(await client.query("SELECT id,name FROM bd_station_place WHERE $1='' OR name ILIKE $1 ORDER BY name,id LIMIT 50",[q?'%'+q+'%':''])).rows};});}
 async fromPlaces(actorId:number,ids:number[]){return this.db.transaction(client=>this.fromPlacesInTransaction(client,actorId,ids));}
 async fromPlacesInTransaction(client:PoolClient,actorId:number,ids:number[]){await client.query("SELECT pg_advisory_xact_lock(hashtext('teams-write'),0)");await client.query("SELECT pg_advisory_xact_lock(hashtext('station-assets'),0)");const actor=await this.actor(client,actorId,true);if(actor.role!=='admin')throw new BusinessError('FORBIDDEN','仅管理员可生成车站任务',403);let created=0;const tasks:number[]=[];for(const id of [...new Set(ids)].sort((a,b)=>a-b)){const place=(await client.query('SELECT id,name FROM bd_station_place WHERE id=$1 FOR SHARE',[id])).rows[0];if(!place)throw new BusinessError('NOT_FOUND','物理车站不存在，未生成任务',404);const occupied=(await client.query('SELECT t.id FROM bd_task t LEFT JOIN bd_station s ON s.id=t.station_id WHERE t.place_id=$1 OR s.place_id=$1 ORDER BY t.id LIMIT 1',[id])).rows[0];if(occupied){tasks.push(occupied.id);continue;}const row=(await client.query("INSERT INTO bd_task(source_type,place_id,state,pct,note,created_by,created_at,updated_at) VALUES('station',$1,'unassigned',0,'',$2,now(),now()) ON CONFLICT(place_id) WHERE place_id IS NOT NULL DO NOTHING RETURNING id",[id,actor.username])).rows[0];if(row){tasks.push(row.id);created++;await this.log(client,actor,{id:row.id,label:place.name},'create','任务','',place.name);}else tasks.push((await client.query('SELECT id FROM bd_task WHERE place_id=$1',[id])).rows[0].id);}return {created,ids:tasks};}

 private async notifyMovement(client:PoolClient,actor:Actor,task:any,teamName:string,fromName:string,receivers:string[],former:string[]){
  const codes=[...new Set([...receivers,...former])];if(!codes.length)return 0;
  const languages=(await client.query("SELECT code,coalesce((SELECT CASE WHEN lang='ja' THEN 'ja' ELSE 'zh' END FROM users WHERE person_code=code ORDER BY id DESC LIMIT 1),'zh') AS lang FROM unnest($1::text[])code",[codes])).rows;
  let count=0;
  for(const [kind,group]of [['receiver',[...new Set(receivers)]],['former',[...new Set(former)]]] as const)for(const lang of ['zh','ja']){
   const recipients=group.filter(code=>languages.find(row=>row.code===code)?.lang===lang);if(!recipients.length)continue;
   const title=kind==='receiver'?(lang==='ja'?`タスクが${fromName}から回ってきました：${task.label}`:`从${fromName}转来任务：${task.label}`):(lang==='ja'?'担当していたタスクが他チームへ移りました':'你担当的任务被转给别的队了');
   const body=kind==='receiver'?(lang==='ja'?'未割当・進行中のタスクです。担当を割り当ててください。':'未分配/进行中的任务，派工即可。'):`${task.label} → ${teamName}`;
   const message=(await client.query("INSERT INTO bd_message(sender_kind,sender,sender_name,title,body,url,scope,ref_type,ref_id,created_at) VALUES('leader',$1,$2,$3,$4,'/my/tasks','task_review','task',$5,now()) RETURNING id",[actor.username,actor.name,title.slice(0,200),body,task.id])).rows[0];
   await client.query('INSERT INTO bd_message_recipient(message_id,person_code,created_at) SELECT $1,code,now() FROM unnest($2::text[])code',[message.id,recipients]);count+=recipients.length;
  }
  return count;
 }
 private async notify(client:PoolClient,actor:Actor,state:any,action:string,old:number|null,next:number,note:string){const recipients=(await client.query("SELECT DISTINCT code,coalesce((SELECT CASE WHEN lang='ja' THEN 'ja' ELSE 'zh' END FROM users WHERE person_code=code ORDER BY id DESC LIMIT 1),'zh') AS lang FROM(SELECT person_code AS code FROM bd_task_assign WHERE task_id=$1 UNION SELECT person_code FROM users WHERE username=$2 AND person_code IS NOT NULL)r WHERE code IS NOT NULL",[state.task.id,state.progress?.reported_by||state.progress?.submitted_by||''])).rows;let sent=0;for(const lang of ['zh','ja']){const codes=recipients.filter(row=>row.lang===lang).map(row=>row.code);if(!codes.length)continue;const label=lang==='ja'?({confirmed:'確認済み',adjusted:'調整',rejected:'却下',unconfirmed:'確認取消'} as Record<string,string>)[action]:({confirmed:'已确认',adjusted:'已调整',rejected:'被驳回',unconfirmed:'确认已撤销'} as Record<string,string>)[action];const title=(lang==='ja'?'タスク進捗':'任务进展')+label+'：'+state.task.label,body=lang==='ja'?`${actor.name}：${old??'—'}% → ${next}%。${note}`:`${actor.name}：${old??'—'}% → ${next}%。${note}`;const message=(await client.query("INSERT INTO bd_message(sender_kind,sender,sender_name,title,body,url,scope,ref_type,ref_id,created_at) VALUES($1,$2,$3,$4,$5,$6,'task_review','task',$7,now()) RETURNING id",[actor.role==='admin'?'admin':'leader',actor.username,actor.name,title.slice(0,200),body,'/tasks/'+state.task.id,state.task.id])).rows[0];await client.query('INSERT INTO bd_message_recipient(message_id,person_code,created_at) SELECT $1,code,now() FROM unnest($2::text[])code',[message.id,codes]);sent+=codes.length;}return sent;}
 async movementQueue(actorId:number,value:unknown){const input=parse(movementQuery,value);return this.db.transaction(async client=>{
  await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');const actor=await this.actor(client,actorId),caps=await this.caps(client,actor.role);
  if(!['admin','leader'].includes(actor.role)&&!(input.progress==='1'&&actor.role==='staff'))throw new BusinessError('FORBIDDEN','没有批量任务列表权限',403);
  const result=(await client.query(`WITH scoped AS(SELECT t.id,t.team_id AS "teamId",team.name AS "teamName",coalesce(place.name,s.name,'#'||t.id::text) AS label,t.pct,t.state,(SELECT count(*)::int FROM bd_task_assign WHERE task_id=t.id) AS assignees,${movementStamp} AS stamp FROM bd_task t LEFT JOIN bd_team team ON team.id=t.team_id LEFT JOIN bd_station_place place ON place.id=t.place_id LEFT JOIN bd_station s ON s.id=t.station_id WHERE $1 OR ($2 AND EXISTS(SELECT 1 FROM bd_team_member m JOIN bd_team current_team ON current_team.id=m.team_id WHERE m.team_id=t.team_id AND m.person_code=$3 AND m.role='leader' AND m.end_date IS NULL AND m.start_date<=$4 AND current_team.status='active')) OR ($7::boolean AND EXISTS(SELECT 1 FROM bd_task_assign mine JOIN bd_team_member m ON m.person_code=mine.person_code AND m.team_id=t.team_id JOIN bd_team team2 ON team2.id=m.team_id WHERE mine.task_id=t.id AND mine.person_code=$3 AND m.end_date IS NULL AND m.start_date<=$4 AND team2.status='active'))),filtered AS(SELECT * FROM scoped WHERE $5='' OR label ILIKE $5 OR "teamName" ILIKE $5),paged AS(SELECT * FROM filtered ORDER BY id DESC OFFSET $6 LIMIT 30) SELECT (SELECT count(*)::int FROM filtered) AS total,coalesce((SELECT json_agg(row) FROM paged row),'[]') AS items`,[actor.role==='admin',!!caps['task.view_team'],actor.code,jstToday(),input.q?'%'+input.q+'%':'',(input.page-1)*30,input.progress==='1'])).rows[0];
  return {...result,items:result.items.map(({stamp,...row}:any)=>({...row,token:digest(stamp)})),page:input.page,pageSize:30,targets:actor.role==='leader'?(await client.query("SELECT t.id,t.name FROM bd_team t WHERE t.status='active' AND NOT EXISTS(SELECT 1 FROM bd_team_member m WHERE m.team_id=t.id AND m.person_code=$1 AND m.role='leader' AND m.end_date IS NULL) ORDER BY t.name,t.id",[actor.code])).rows:[]};
 });}
 private async notifyBatchMovement(client:PoolClient,actor:Actor,picked:any[],target:{id:number;name:string}){
  const former=[...new Set<string>(picked.flatMap(row=>row.assigned.map((person:any)=>person.code)))],leaders=(await client.query("SELECT DISTINCT person_code AS code FROM bd_team_member WHERE team_id=$1 AND role='leader' AND end_date IS NULL",[target.id])).rows.map(row=>row.code as string);
  const all=[...new Set([...former,...leaders])];if(!all.length)return {messages:0,receipts:0};
  const languages=(await client.query("SELECT code,coalesce((SELECT CASE WHEN lang='ja' THEN 'ja' ELSE 'zh' END FROM users WHERE person_code=code ORDER BY id DESC LIMIT 1),'zh') AS lang FROM unnest($1::text[])code",[all])).rows;
  const head=picked.slice(0,3).map(row=>row.task.label).join('、')+(picked.length>3?'…':''),from=[...new Set(picked.map(row=>row.fromName))].join(' / ');let messages=0,receipts=0;
  for(const [kind,codes]of [['leader',leaders],['former',former]] as const)for(const lang of ['zh','ja']){
   const recipients=[...new Set(codes.filter(code=>languages.find(row=>row.code===code)?.lang===lang))];if(!recipients.length)continue;
   const title=kind==='leader'?(lang==='ja'?`${picked.length}件のタスクが${from}から回ってきました：${head}`:`从${from}转来 ${picked.length} 个任务：${head}`):(lang==='ja'?'担当していたタスクが他チームへ移りました':'你担当的任务被转给别的队了');
   const body=kind==='leader'?(lang==='ja'?'未割当・進行中のタスクです。担当を割り当ててください。':'都是未分配/进行中的任务，派工即可。'):`${head} → ${target.name}`;
   const message=(await client.query("INSERT INTO bd_message(sender_kind,sender,sender_name,title,body,url,scope,ref_type,ref_id,created_at) VALUES('leader',$1,$2,$3,$4,$5,'task_review','team',$6,now()) RETURNING id",[actor.username,actor.name,title.slice(0,200),body,kind==='leader'?'/my/tasks?tab=unassigned':'/my/tasks',target.id])).rows[0];
   await client.query('INSERT INTO bd_message_recipient(message_id,person_code,created_at) SELECT $1,code,now() FROM unnest($2::text[])code',[message.id,recipients]);messages++;receipts+=recipients.length;
  }
  return {messages,receipts};
 }
 async uniformProgress(actorId:number,value:unknown){const input=parse(uniformProgressRequest,value);return this.db.transaction(async client=>{
  await client.query("SELECT pg_advisory_xact_lock(hashtext('teams-write'),0)");const actor=await this.actor(client,actorId,true);
  const initial=(await client.query(`SELECT t.id,${movementStamp} AS stamp FROM bd_task t WHERE t.id=ANY($1::int[]) ORDER BY t.id FOR UPDATE OF t`,[input.selection.map(row=>row.id)])).rows,versions=new Map(initial.map(row=>[row.id,digest(row.stamp)]));const ids:number[]=[],skipped:{id:number;code:string}[]=[];
  for(const selected of [...input.selection].sort((a,b)=>a.id-b.id)){
   if(!versions.has(selected.id)){skipped.push({id:selected.id,code:'NOT_FOUND'});continue;}
   if(versions.get(selected.id)!==selected.token){skipped.push({id:selected.id,code:'CHANGED'});continue;}
   const state=await this.snapshot(client,selected.id),permission=await this.permission(client,actor,state);
   if(!permission.report){skipped.push({id:selected.id,code:'NO_PERMISSION'});continue;}
   await client.query('SAVEPOINT uniform_progress');
   try{await this.changeTransaction(client,selected.id,actorId,{action:'report',pct:input.pct,note:input.note},state.token);await client.query('RELEASE SAVEPOINT uniform_progress');ids.push(selected.id);}
   catch(error){await client.query('ROLLBACK TO SAVEPOINT uniform_progress');await client.query('RELEASE SAVEPOINT uniform_progress');skipped.push({id:selected.id,code:error instanceof BusinessError?error.code:'TASK_UPDATE_FAILED'});}
  }
  return {updated:ids.length,ids,skipped,cleared:0,notifications:{messages:0,receipts:0},action:'progress'};
 });}
 async movementBatch(actorId:number,value:unknown){const input=parse(movementRequest,value);return this.db.transaction(async client=>{
  await client.query("SELECT pg_advisory_xact_lock(hashtext('teams-write'),0)");const actor=await this.actor(client,actorId,true);
  if(input.action==='transfer'&&actor.role!=='leader'||input.action==='return-pool'&&actor.role!=='admin')throw new BusinessError('FORBIDDEN','只有队长可批量转队，只有管理员可批量退池',403);
  const target=input.action==='transfer'?(await client.query("SELECT id,name FROM bd_team WHERE id=$1 AND status='active'",[input.teamId])).rows[0]:null;if(input.action==='transfer'&&!target)throw new BusinessError('TEAM_UNAVAILABLE','目标团队不存在或已停用',409);
  const initial=(await client.query(`SELECT t.id,${movementStamp} AS stamp FROM bd_task t WHERE t.id=ANY($1::int[]) ORDER BY t.id FOR UPDATE OF t`,[input.selection.map(row=>row.id)])).rows,versions=new Map(initial.map(row=>[row.id,digest(row.stamp)]));
  const skipped:{id:number;code:string}[]=[],picked:any[]=[];
  for(const selected of [...input.selection].sort((a,b)=>a.id-b.id)){
   if(!versions.has(selected.id)){skipped.push({id:selected.id,code:'NOT_FOUND'});continue;}
   const state=await this.snapshot(client,selected.id),permission=await this.permission(client,actor,state);
   if(input.action==='transfer'){
    if(state.task.team_id===null){skipped.push({id:selected.id,code:'NO_TEAM'});continue;}
    if(state.task.team_id===input.teamId){skipped.push({id:selected.id,code:'SAME_TEAM'});continue;}
    if(state.task.state==='done'||state.task.pct>=100){skipped.push({id:selected.id,code:'COMPLETED'});continue;}
    if(!permission.leader)throw new BusinessError('FORBIDDEN','包含不是自己带队的可转任务，整批未修改',403);
   }
   if(versions.get(selected.id)!==selected.token){skipped.push({id:selected.id,code:'CHANGED'});continue;}
   if(input.action==='return-pool'){
    if(state.task.team_id===null){skipped.push({id:selected.id,code:'ALREADY_IN_POOL'});continue;}
    if(state.assigned.length){skipped.push({id:selected.id,code:'HAS_ASSIGNEES'});continue;}
   }
   const from=(await client.query('SELECT name FROM bd_team WHERE id=$1',[state.task.team_id])).rows[0];picked.push({...state,fromName:from?.name??''});
  }
  let cleared=0;for(const state of picked){const change:Action=input.action==='transfer'?{action:'transfer',teamId:input.teamId}:{action:'return-pool'};const result=await this.changeTransaction(client,state.task.id,actorId,change,state.token,jstToday(),true);if('cleared'in result)cleared+=result.cleared as number;}
  const notified=input.action==='transfer'&&picked.length?await this.notifyBatchMovement(client,actor,picked,target):{messages:0,receipts:0};
  return {updated:picked.length,ids:picked.map(row=>row.task.id),skipped,cleared,notifications:notified,action:input.action,removedFromView:input.action==='transfer'&&picked.length>0};
 });}

 async reviewQueue(actorId:number,value:unknown){
  const input=parse(reviewQuery,value);return this.db.transaction(async client=>{
   await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');const actor=await this.actor(client,actorId),caps=await this.caps(client,actor.role);
   if(!['admin','leader'].includes(actor.role)||input.kind==='settled'&&actor.role!=='admin')throw new BusinessError('FORBIDDEN','仅队长可看本队待确认，已复核列表仅管理员可看',403);
   const result=(await client.query(`WITH scoped AS(SELECT p.id,p.task_id AS "taskId",p.progress_date::text AS date,p.pct,p.reported_pct AS "reportedPct",p.reported_by AS "reportedBy",p.review_status AS "reviewStatus",p.note,p.review_note AS "reviewNote",coalesce(place.name,s.name,'#'||t.id::text) AS label,team.name AS "teamName",p.progress_date=(SELECT max(progress_date) FROM bd_task_progress WHERE task_id=t.id) AS latest,${reviewStamp} AS stamp FROM bd_task_progress p JOIN bd_task t ON t.id=p.task_id LEFT JOIN bd_station_place place ON place.id=t.place_id LEFT JOIN bd_station s ON s.id=t.station_id LEFT JOIN bd_team team ON team.id=t.team_id WHERE p.review_status=ANY($1::text[]) AND ($2 OR ($3 AND EXISTS(SELECT 1 FROM bd_team_member m JOIN bd_team team2 ON team2.id=m.team_id WHERE m.team_id=t.team_id AND m.person_code=$4 AND m.role='leader' AND m.end_date IS NULL AND m.start_date<=$5 AND team2.status='active'))) AND ($6::date IS NULL OR p.progress_date>=$6::date) AND ($7::date IS NULL OR p.progress_date<=$7::date)),paged AS(SELECT * FROM scoped ORDER BY date DESC,id DESC OFFSET $8 LIMIT 30) SELECT (SELECT count(*)::int FROM scoped) AS total,coalesce((SELECT json_agg(row) FROM paged row),'[]') AS items`,[input.kind==='pending'?['pending']:['confirmed','adjusted'],actor.role==='admin',!!caps['task.view_team'],actor.code,jstToday(),input.dateFrom??null,input.dateTo??null,(input.page-1)*30])).rows[0];
   return {...result,items:result.items.map(({stamp,...row}:any)=>({...row,token:digest(stamp)})),page:input.page,pageSize:30};
  });
 }
 private async reviewRecord(client:PoolClient,id:number){
  const progress=(await client.query(`SELECT p.*,p.progress_date::text AS date,${reviewStamp} AS stamp FROM bd_task_progress p JOIN bd_task t ON t.id=p.task_id WHERE p.id=$1 FOR UPDATE OF p,t`,[id])).rows[0];
  return progress?{progress,token:digest(progress.stamp),state:await this.snapshot(client,progress.task_id,progress.date)}:null;
 }
 private async initialReviewTokens(client:PoolClient,selection:{id:number;token:string}[]){const rows=(await client.query(`SELECT p.id,${reviewStamp} AS stamp FROM bd_task_progress p JOIN bd_task t ON t.id=p.task_id WHERE p.id=ANY($1::int[]) ORDER BY p.task_id,p.progress_date,p.id FOR UPDATE OF p,t`,[selection.map(row=>row.id)])).rows;return new Map(rows.map(row=>[row.id,digest(row.stamp)]));}
 async confirmBatch(actorId:number,value:unknown){
  const input=parse(confirmReviews,value);return this.db.transaction(async client=>{
   await client.query("SELECT pg_advisory_xact_lock(hashtext('teams-write'),0)");const actor=await this.actor(client,actorId,true),caps=await this.caps(client,actor.role);
   if(!['admin','leader'].includes(actor.role)||actor.role==='leader'&&!caps['task.report'])throw new BusinessError('FORBIDDEN','没有批量确认权限',403);
   const selection=input.selection??(await client.query("SELECT p.id FROM bd_task_progress p JOIN bd_task t ON t.id=p.task_id WHERE p.review_status='pending' AND ($1 OR EXISTS(SELECT 1 FROM bd_team_member m JOIN bd_team team ON team.id=m.team_id WHERE m.team_id=t.team_id AND m.person_code=$2 AND m.role='leader' AND m.end_date IS NULL AND m.start_date<=$3 AND team.status='active')) ORDER BY p.progress_date,p.id",[actor.role==='admin',actor.code,jstToday()])).rows.map(row=>({id:row.id,token:undefined}));
   const initialTokens=input.selection?await this.initialReviewTokens(client,input.selection):null;let updated=0;const skipped:{id:number;code:string}[]=[];
   for(const selected of selection){
    const row=await this.reviewRecord(client,selected.id);if(!row){skipped.push({id:selected.id,code:'NOT_FOUND'});continue;}
    const permission=await this.permission(client,actor,row.state);if(!permission.adjust||!permission.report){skipped.push({id:selected.id,code:'NO_PERMISSION'});continue;}
    if(selected.token&&initialTokens?.get(selected.id)!==selected.token){skipped.push({id:selected.id,code:'CHANGED'});continue;}
    if(row.progress.review_status!=='pending'){skipped.push({id:selected.id,code:'NOT_PENDING'});continue;}
    await this.changeTransaction(client,row.progress.task_id,actorId,{action:'confirm',pct:row.progress.pct,note:''},row.state.token,row.progress.date);updated++;
   }
   return {updated,skipped};
  });
 }
 private async reviewSummary(client:PoolClient,actor:Actor,teamId:number,count:number,note:string){
  const rows=(await client.query("SELECT m.person_code AS code,coalesce((SELECT CASE WHEN lang='ja' THEN 'ja' ELSE 'zh' END FROM users WHERE person_code=m.person_code ORDER BY id DESC LIMIT 1),'zh') AS lang FROM bd_team_member m WHERE m.team_id=$1 AND m.role='leader' AND m.end_date IS NULL",[teamId])).rows;if(!rows.length)return 0;
  for(const lang of ['zh','ja']){const codes=[...new Set(rows.filter(row=>row.lang===lang).map(row=>row.code))];if(!codes.length)continue;
   const title=lang==='ja'?`チームの${count}件の進捗確認が管理者に取り消されました`:`你队的 ${count} 条进展确认被管理员撤销`,body=lang==='ja'?`${actor.name}が${count}件を確認待ちに戻しました。再確認してください。${note}`:`${actor.name}把 ${count} 条进展退回待确认，请重新确认。${note}`;
   const message=(await client.query("INSERT INTO bd_message(sender_kind,sender,sender_name,title,body,url,scope,ref_type,ref_id,created_at) VALUES('admin',$1,$2,$3,$4,'/my/tasks?tab=pending','task_review','team',$5,now()) RETURNING id",[actor.username,actor.name,title,body,teamId])).rows[0];
   await client.query('INSERT INTO bd_message_recipient(message_id,person_code,created_at) SELECT $1,code,now() FROM unnest($2::text[])code',[message.id,codes]);
  }
  return 1;
 }
 async reopenBatch(actorId:number,value:unknown){
  const input=parse(reopenReviews,value);return this.db.transaction(async client=>{
   await client.query("SELECT pg_advisory_xact_lock(hashtext('teams-write'),0)");const actor=await this.actor(client,actorId,true);if(actor.role!=='admin')throw new BusinessError('FORBIDDEN','仅管理员可批量撤销复核',403);
   const initialTokens=await this.initialReviewTokens(client,input.selection);let updated=0,notified=0,summaries=0;const skipped:{id:number;code:string}[]=[],notificationFailures:number[]=[],summaryFailures:number[]=[],byTeam=new Map<number,number>();
   for(const selected of input.selection){
    const row=await this.reviewRecord(client,selected.id);if(!row){skipped.push({id:selected.id,code:'NOT_FOUND'});continue;}
    if(initialTokens.get(selected.id)!==selected.token){skipped.push({id:selected.id,code:'CHANGED'});continue;}
    const progress=row.progress,state=row.state;
    if(!['confirmed','adjusted'].includes(progress.review_status)){skipped.push({id:selected.id,code:'NOT_SETTLED'});continue;}
    const target=input.forcePct??progress.reported_pct??input.noOriginPct;if(target===undefined||target===null){skipped.push({id:selected.id,code:'NO_ORIGINAL'});continue;}
    await client.query('SAVEPOINT review_apply');
    try{
     await client.query("UPDATE bd_task_progress SET pct=$1,review_status='pending',reviewed_by='',reviewed_at=NULL,review_note='',updated_at=now() WHERE id=$2",[target,progress.id]);
     if(state.latest?.date===progress.date)await client.query('UPDATE bd_task SET pct=$1,state=$2,done_date=CASE WHEN $1<100 THEN NULL ELSE done_date END,updated_at=now() WHERE id=$3',[target,stationTaskState(target,state.assigned.length),state.task.id]);
     await this.log(client,actor,state.task,'reject','进展确认',progress.review_status+' '+progress.pct+'%','退回待确认 '+target+'%',input.note);await client.query('RELEASE SAVEPOINT review_apply');
    }catch{await client.query('ROLLBACK TO SAVEPOINT review_apply');await client.query('RELEASE SAVEPOINT review_apply');skipped.push({id:selected.id,code:'UPDATE_FAILED'});continue;}
    updated++;if(state.task.team_id!==null)byTeam.set(state.task.team_id,(byTeam.get(state.task.team_id)??0)+1);
    await client.query('SAVEPOINT review_notice');
    try{
     state.progress={...progress,reported_by:progress.reported_pct===null?'':progress.reported_by,submitted_by:progress.reported_pct===null?'':progress.submitted_by};
     if(await this.notify(client,actor,state,'unconfirmed',progress.pct,target,input.note))notified++;await client.query('RELEASE SAVEPOINT review_notice');
    }catch{await client.query('ROLLBACK TO SAVEPOINT review_notice');await client.query('RELEASE SAVEPOINT review_notice');notificationFailures.push(selected.id);}
   }
   for(const [team,count]of byTeam){await client.query('SAVEPOINT review_summary');try{summaries+=await this.reviewSummary(client,actor,team,count,input.note);await client.query('RELEASE SAVEPOINT review_summary');}catch{await client.query('ROLLBACK TO SAVEPOINT review_summary');await client.query('RELEASE SAVEPOINT review_summary');summaryFailures.push(team);}}
   return {updated,skipped,notified,summaries,notificationFailures,summaryFailures,forcePct:input.forcePct??null,noOriginPct:input.noOriginPct??null};
  });
 }

 async progressViewInTransaction(client:PoolClient,id:number,actorId:number,onDate=jstToday()){
  const actor=await this.actor(client,actorId),state=await this.snapshot(client,id,onDate),permission=await this.permission(client,actor,state);
  if(!permission.view)throw new BusinessError('FORBIDDEN','不能查看其他团队或非本人担当的任务',403);
  return {token:state.token,task:state.task,progress:state.progress,permission};
 }
 async reportForSelfReport(client:PoolClient,id:number,actorId:number,input:{pct:number;note:string;storeCount?:number},onDate:string,admin=false){
  const state=await this.snapshot(client,id,onDate);return this.reportInTransaction(client,id,actorId,input,state.token,onDate,admin);
 }
 async reportInTransaction(client:PoolClient,id:number,actorId:number,input:{pct:number;note:string;storeCount?:number},token:string,onDate:string,admin=false){
  const action=parse(actionSchema,{action:admin?'adjust':'report',...input});return this.changeTransaction(client,id,actorId,action,token,onDate);
 }
 async change(id:number,actorId:number,input:Action,token:string){return this.db.transaction(client=>this.changeInTransaction(client,id,actorId,input,token));}
 async changeInTransaction(client:PoolClient,id:number,actorId:number,input:Action,token:string){input=parse(actionSchema,input);if(input.action==='reject')input={...input,storeCount:undefined};return this.changeTransaction(client,id,actorId,input,token);}
 private async changeTransaction(client:PoolClient,id:number,actorId:number,input:Action,token:string,onDate=jstToday(),silentMovement=false){
  await client.query("SELECT pg_advisory_xact_lock(hashtext('teams-write'),0)");const actor=await this.actor(client,actorId,true);await client.query("SELECT pg_advisory_xact_lock(hashtext('station-task'),$1)",[id]);const state=await this.snapshot(client,id,onDate),permission=await this.permission(client,actor,state);if(token!==state.token)throw new BusinessError('TASK_CHANGED','任务或进度已变化，请刷新后重试',409);const task=state.task,day=onDate;
  if(input.action==='transfer'){
   if(!permission.leader)throw new BusinessError('FORBIDDEN','只能转出自己担任队长的团队任务',403);
   if(!permission.transfer)throw new BusinessError('TRANSFER_COMPLETED','已完成任务不能转队',409);
   if(input.teamId===task.team_id)throw new BusinessError('TRANSFER_SAME_TEAM','目标不能是本队',409);
   const target=(await client.query("SELECT id,name FROM bd_team WHERE id=$1 AND status='active'",[input.teamId])).rows[0];if(!target)throw new BusinessError('TEAM_UNAVAILABLE','目标队伍不存在或已停用',409);
   const from=(await client.query('SELECT name FROM bd_team WHERE id=$1',[task.team_id])).rows[0];
   const removed=(await client.query('DELETE FROM bd_task_assign a WHERE a.task_id=$1 AND NOT EXISTS(SELECT 1 FROM bd_team_member m WHERE m.team_id=$2 AND m.person_code=a.person_code AND m.end_date IS NULL) RETURNING person_code',[id,target.id])).rows;
   for(const row of removed)await this.log(client,actor,task,'unassign','assignee',row.person_code,'','换队清空原担当');
   const count=(await client.query('SELECT count(*)::int AS n FROM bd_task_assign WHERE task_id=$1',[id])).rows[0].n;
   await client.query('UPDATE bd_task SET team_id=$1,state=$2,updated_at=now() WHERE id=$3',[target.id,stationTaskState(task.pct,count),id]);
   await this.log(client,actor,task,'transfer','team',from?.name??'',target.name);
   const leaders=(await client.query("SELECT person_code AS code FROM bd_team_member WHERE team_id=$1 AND role='leader' AND end_date IS NULL",[target.id])).rows.map(row=>row.code);
   const notified=silentMovement?0:await this.notifyMovement(client,actor,task,target.name,from?.name??'',leaders,state.assigned.map(row=>row.code));
   return {ok:true,transferred:true,cleared:removed.length,toTeam:target.name,notified,removedFromView:true};
  }
  if(input.action==='return-pool'){
   if(!permission.admin)throw new BusinessError('FORBIDDEN','仅管理员可退回车站池',403);
   if(task.team_id===null)throw new BusinessError('ALREADY_IN_POOL','任务已经在车站池',409);
   if(!permission.returnPool)throw new BusinessError('RETURN_HAS_ASSIGNEES','还有担当，请让队长先回收或改派',409);
   const from=(await client.query('SELECT name FROM bd_team WHERE id=$1',[task.team_id])).rows[0];
   await client.query('DELETE FROM bd_task_assign WHERE task_id=$1',[id]);await client.query('UPDATE bd_task SET team_id=NULL,assign_date=NULL,state=$1,updated_at=now() WHERE id=$2',[stationTaskState(task.pct,0),id]);
   await this.log(client,actor,task,'return_pool','team',from?.name??'','（车站池）');return {ok:true,returnedToPool:true};
  }
  if(input.action==='dispatch'){
   if(!permission.admin)throw new BusinessError('FORBIDDEN','仅管理员可派发团队',403);if(input.teamId===null)throw new BusinessError('RETURN_POOL_REQUIRED','退池请使用退回车站池，且须先回收担当',409);if(input.teamId!==null&&!(await client.query("SELECT id FROM bd_team WHERE id=$1 AND status='active'",[input.teamId])).rowCount)throw new BusinessError('TEAM_UNAVAILABLE','目标团队不存在或已停用',409);const date=input.date??day;if(!Number.isFinite(Date.parse(date+'T00:00:00Z'))||new Date(date+'T00:00:00Z').toISOString().slice(0,10)!==date)throw new BusinessError('VALIDATION','分配日期无效',400);
   if(task.team_id!==input.teamId){if(input.teamId!==null){const removed=(await client.query('DELETE FROM bd_task_assign a WHERE a.task_id=$1 AND NOT EXISTS(SELECT 1 FROM bd_team_member m WHERE m.team_id=$2 AND m.person_code=a.person_code AND m.end_date IS NULL) RETURNING person_code',[id,input.teamId])).rows;for(const row of removed)await this.log(client,actor,task,'unassign','assignee',row.person_code,'','换队清空原担当');}await this.log(client,actor,task,'dispatch','team',task.team_id,input.teamId);}
   if(task.assignDate!==date)await this.log(client,actor,task,'update','assign_date',task.assignDate,date);const count=(await client.query('SELECT count(*)::int AS n FROM bd_task_assign WHERE task_id=$1',[id])).rows[0].n;await client.query('UPDATE bd_task SET team_id=$1,assign_date=$2,state=$3,updated_at=now() WHERE id=$4',[input.teamId,date,stationTaskState(task.pct,count),id]);return {ok:true};
  }
  if(input.action==='assign'){
   if(!permission.assign)throw new BusinessError('FORBIDDEN','只有管理员或本队队长能分派担当',403);const codes=[...new Set(input.codes)];if(codes.length&&!task.team_id)throw new BusinessError('TEAM_REQUIRED','请先派给团队',409);const valid=(await client.query("SELECT m.person_code AS code FROM bd_team_member m WHERE m.team_id=$1 AND m.end_date IS NULL AND m.start_date<=$3 AND m.person_code=ANY($2::text[]) AND NOT EXISTS(SELECT 1 FROM users u WHERE u.person_code=m.person_code AND u.status IN('disabled','resigned'))",[task.team_id,codes,day])).rows;if(valid.length!==codes.length)throw new BusinessError('ASSIGNEE_UNAVAILABLE','担当必须是本队现役成员，且未离职或停用',409);
   const workDay=task.assignDate&&task.assignDate>day?task.assignDate:day;const warnings=(await client.query("SELECT p.display_name AS name,CASE WHEN EXISTS(SELECT 1 FROM users u WHERE u.person_code=p.code AND u.status='leave') THEN '员工状态：请假' ELSE '' END AS status,EXISTS(SELECT 1 FROM staff_date_plans WHERE person_code=p.code AND plan_date=$2 AND available=false) AS off,EXISTS(SELECT 1 FROM bd_staff_leave WHERE person_code=p.code AND status='active' AND start_date<=$2 AND (end_date IS NULL OR end_date>=$2)) AS leave FROM persons p WHERE code=ANY($1::text[])",[codes,workDay])).rows.filter(row=>row.status||row.off||row.leave).map(row=>row.name+'：'+[row.status,row.off?'计划休':'',row.leave?'休假中':''].filter(Boolean).join('；'));
   await client.query('DELETE FROM bd_task_assign WHERE task_id=$1',[id]);for(const code of codes)await client.query('INSERT INTO bd_task_assign(task_id,person_code,assigned_by,assigned_at,dispatch_date) VALUES($1,$2,$3,now(),$4)',[id,code,actor.username,day]);for(const row of state.assigned)if(!codes.includes(row.code))await this.log(client,actor,task,'unassign','assignee',row.code,'');for(const code of codes)if(!state.assigned.some(row=>row.code===code))await this.log(client,actor,task,'assign','assignee','',code);await client.query('UPDATE bd_task SET state=$1,updated_at=now() WHERE id=$2',[stationTaskState(task.pct,codes.length),id]);return {ok:true,warnings};
  }
  const progress=state.progress,review=permission.leader||permission.admin&&adminMayAdjust(progress?.review_status),staff=permission.assigned&&!review;
  if(input.action==='reject'){
   if(!permission.reject)throw new BusinessError('FORBIDDEN','不能驳回这条任务',403);if(task.pct!==100||input.pct>=100)throw new BusinessError('REJECT_RANGE','仅能把完成任务驳回至0–99%',409);
  }else{if(!permission.report)throw new BusinessError('FORBIDDEN','非本人担当、跨队或当天进度已确认',403);if(permission.admin&&!review)throw new BusinessError('REVIEW_REQUIRED','管理员只能修改队长已处理的当天进度',409);if(['confirm','adjust'].includes(input.action)&&!review)throw new BusinessError('FORBIDDEN','仅本队队长或符合条件的管理员可复核',403);}
  const value=input.action==='confirm'&&progress?.reported_pct!==null&&progress?.reported_pct!==undefined?progress.reported_pct:input.pct;if(staff&&value===100&&input.storeCount===undefined)throw new BusinessError('STORE_COUNT_REQUIRED','完成时必须填写店铺数，可以填0',400);
  let row:any={...progress},notified=0;
  if(input.action==='reject'){row.reported_pct=progress?.reported_pct??task.pct;row.reported_by=progress?.reported_by||progress?.submitted_by||'';row.review_status='rejected';row.reviewed_by=actor.username;row.review_note=input.note;row.note=progress?.note||'';row.submitted_by=progress?.submitted_by||'';}
  else{
   row.note=input.note||progress?.note||'';row.submitted_by=staff?actor.username:progress?.submitted_by||actor.username;
   if(staff&&!(progress&&['confirmed','adjusted'].includes(progress.review_status)&&progress.pct===value)){row.reported_pct=value;row.reported_by=actor.username;row.review_status='pending';row.reviewed_by='';row.review_note='';}
   else if(review){row.review_status=row.reported_pct===null||row.reported_pct===undefined?'adjusted':row.reported_pct===value?'confirmed':'adjusted';row.reviewed_by=actor.username;row.review_note=input.note;}
  }
  const status=row.review_status||'pending',reviewed=!!row.reviewed_by;await client.query(`INSERT INTO bd_task_progress(task_id,progress_date,pct,note,submitted_by,reported_pct,reported_by,review_status,reviewed_by,reviewed_at,review_note,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,CASE WHEN $10 THEN now() ELSE NULL END,$11,now(),now()) ON CONFLICT(task_id,progress_date) DO UPDATE SET pct=excluded.pct,note=excluded.note,submitted_by=excluded.submitted_by,reported_pct=excluded.reported_pct,reported_by=excluded.reported_by,review_status=excluded.review_status,reviewed_by=excluded.reviewed_by,reviewed_at=CASE WHEN $10 THEN excluded.reviewed_at WHEN excluded.reviewed_by='' THEN NULL ELSE bd_task_progress.reviewed_at END,review_note=excluded.review_note,updated_at=now()`,[id,day,value,row.note,row.submitted_by,row.reported_pct??null,row.reported_by||'',status,row.reviewed_by||'',review||input.action==='reject',row.review_note||'']);
  const nextState=stationTaskState(value,state.assigned.length),isLatest=!state.latest||day>=state.latest.date; if(isLatest)await client.query('UPDATE bd_task SET pct=$1,state=$2,start_date=CASE WHEN $6 THEN start_date ELSE coalesce(start_date,$3) END,done_date=CASE WHEN $1>=100 THEN coalesce(done_date,$3) ELSE NULL END,store_count=coalesce($4,store_count),updated_at=now() WHERE id=$5',[value,nextState,day,input.storeCount??null,id,input.action==='reject']);
  if(isLatest&&input.storeCount!==undefined&&input.storeCount!==task.store_count)await this.log(client,actor,task,'update','store_count',task.store_count,input.storeCount);
  const beforePct=isLatest?task.pct:progress?.pct??task.pct;if(input.action==='reject'||value!==beforePct||isLatest&&nextState!==task.state)await this.log(client,actor,task,input.action==='reject'?'reject':'progress','pct',beforePct+'%',value+'%',input.note);else if(review)await this.log(client,actor,task,'update','进展确认',row.reported_pct,status,input.note);
  if((review||input.action==='reject')&&row.reported_pct!==null&&row.reported_pct!==undefined&&(input.action!=='reject'||row.reported_pct!==value)){state.progress=row;notified=await this.notify(client,actor,state,status,row.reported_pct,value,input.note);}return {ok:true,pct:value,state:isLatest?nextState:task.state,reviewStatus:status,reportedPct:row.reported_pct??null,notified};
 }
}
@Controller('station-tasks')
export class StationTasksController{
 constructor(@Inject(AuthService)private readonly auth:AuthService,@Inject(StationTasksService)private readonly service:StationTasksService){}
 @Get()async list(@Req()req:Request,@Query()query:unknown){const {user}=await this.auth.require(req);const input=parse(z.object({q:z.string().trim().max(200).default(''),page:z.coerce.number().int().min(1).max(10000).default(1),tab:z.enum(['','mine','done','unassigned','doing','pending']).default('')}).strict(),query);return this.service.list(user.id,input.page,input.q,input.tab);}
 @Get('places')async places(@Req()req:Request,@Query()query:unknown){const {user}=await this.auth.require(req,{admin:true});const input=parse(z.object({q:z.string().trim().max(200).default('')}).strict(),query);return this.service.places(user.id,input.q);}
 @Get('reviews')async reviews(@Req()req:Request,@Query()query:unknown){const {user}=await this.auth.require(req);return this.service.reviewQueue(user.id,query);}
 @Post('reviews/confirm')async confirm(@Req()req:Request,@Body()body:unknown){const {user}=await this.auth.require(req,{write:true});return this.service.confirmBatch(user.id,body);}
 @Post('reviews/reopen')async reopen(@Req()req:Request,@Body()body:unknown){const {user}=await this.auth.require(req,{admin:true,write:true});return this.service.reopenBatch(user.id,body);}
 @Get('movement')async movement(@Req()req:Request,@Query()query:unknown){const {user}=await this.auth.require(req);return this.service.movementQueue(user.id,query);}
 @Post('movement')async move(@Req()req:Request,@Body()body:unknown){const {user}=await this.auth.require(req,{write:true});return this.service.movementBatch(user.id,body);}
 @Get(':id')async detail(@Req()req:Request,@Param('id')id:string){const {user}=await this.auth.require(req);return this.service.detail(parse(identity,id),user.id);}
 @Post('from-places')async create(@Req()req:Request,@Body()body:unknown){const {user}=await this.auth.require(req,{admin:true,write:true});const input=parse(z.object({placeIds:z.array(identity).min(1).max(200)}).strict(),body);return this.service.fromPlaces(user.id,input.placeIds);}
 @Post('progress-batch')async progressBatch(@Req()req:Request,@Body()body:unknown){const {user}=await this.auth.require(req,{write:true});return this.service.uniformProgress(user.id,body);}
 @Post(':id')async change(@Req()req:Request,@Param('id')id:string,@Body()body:unknown){const {user}=await this.auth.require(req,{write:true});const input=parse(z.object({token:z.string().regex(/^[a-f0-9]{64}$/),change:actionSchema}).strict(),body);return this.service.change(parse(identity,id),user.id,input.change,input.token);}
}
