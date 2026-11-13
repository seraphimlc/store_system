import {Inject,Injectable} from '@nestjs/common';
import type {PoolClient} from 'pg';
import {createHash} from 'node:crypto';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {safeUploadName} from './private-files';
import {tmpdir} from 'node:os';
import ExcelJS from 'exceljs';
import {z} from 'zod';
import type {McpActor} from './mcp';
import {McpReadError} from './mcp-read';
import {BusinessError} from './errors';
import {StationTasksService} from './station-tasks';
import {orderedWorkbook} from './xlsx-container';
import {buildTaskPlan,taskPlanRows,TaskPlanError,type TaskPlanAssets,type TaskPlanLayout} from '../../../packages/domain/src/task-plan-import';
const schema=z.object({content_base64:z.string().nullable().optional(),path:z.string().nullable().optional(),filename:z.string().max(255).nullable().optional(),action:z.enum(['preview','apply']).default('preview'),blob_sha:z.string().regex(/^[a-f0-9]{64}$/).nullable().optional(),header_row:z.number().int().min(1).max(20000).nullable().optional(),cols:z.record(z.string(),z.union([z.number().int().min(0).max(127),z.string().regex(/^[0-9]+$/)])).nullable().optional(),skip_rows:z.array(z.union([z.number().int().min(1),z.string().regex(/^[1-9][0-9]*$/)])).max(20000).nullable().optional(),plan_token:z.string().regex(/^[a-f0-9]{64}$/).optional()}).strict();
export const mcpTaskImportTool={name:'visit_task_import',title:'车站任务计划导入',description:'仅管理员且需write scope：Excel线路执行计划按“预览→确认”导入。提供content_base64及filename，或本人预览返回的blob_sha。action=preview默认；apply重新核对并仅建新任务，不改已有任务、不新建车站、不猜队伍。bad行需明确skip_rows或先处理。可选header_row/cols（0起列号，需station列）及plan_token防预览过期。path服务器路径模式关闭。计划文件私有保存，不进入巡店imports。',inputSchema:{type:'object' as const,properties:{content_base64:{type:['string','null']},path:{type:['string','null']},filename:{type:['string','null']},action:{type:'string',enum:['preview','apply'],default:'preview'},blob_sha:{type:['string','null'],pattern:'^[a-f0-9]{64}$'},header_row:{type:['integer','null'],minimum:1},cols:{type:['object','null'],additionalProperties:{type:['integer','string']}},skip_rows:{type:['array','null'],items:{type:['integer','string']}},plan_token:{type:'string',pattern:'^[a-f0-9]{64}$'}},additionalProperties:false},annotations:{readOnlyHint:false,destructiveHint:true,idempotentHint:true,openWorldHint:false}};
const digest=(value:unknown)=>createHash('sha256').update(JSON.stringify(value)).digest('hex');
@Injectable()
export class McpTaskImportService{
 constructor(@Inject(StationTasksService)private readonly tasks:StationTasksService){}
 private directory(){return join(process.env.NEXT_UPLOAD_DIRECTORY??join(tmpdir(),'store-next-uploads'),'task_plan');}
 private async source(actor:McpActor,input:z.infer<typeof schema>){
  if(input.path)throw new McpReadError('FORBIDDEN_TOOL','服务器路径模式未开启','请使用content_base64传入文件内容');
  if(input.blob_sha&&input.content_base64)throw new McpReadError('BAD_REQUEST','blob_sha与content_base64只选一种来源');
  const directory=this.directory();let sha=input.blob_sha,content:Buffer,name=input.filename?safeUploadName(input.filename):null;
  if(input.content_base64){const raw=input.content_base64;if(raw.length>24*1024*1024||raw.length%4||!/^[A-Za-z0-9+/]+={0,2}$/.test(raw))throw new McpReadError('BAD_REQUEST','文件base64无效或超过限制');content=Buffer.from(raw,'base64');if(!content.length||content.length>16*1024*1024||content.toString('base64')!==raw)throw new McpReadError('BAD_REQUEST','文件base64无效或超过16MiB');if(name&&!name.toLowerCase().endsWith('.xlsx'))throw new McpReadError('BAD_REQUEST','计划文件必须是.xlsx');sha=createHash('sha256').update(content).digest('hex');await mkdir(directory,{recursive:true,mode:0o700});
   try{await writeFile(join(directory,sha+'.xlsx'),content,{mode:0o600,flag:'wx'});}catch(error){if((error as NodeJS.ErrnoException).code!=='EEXIST')throw error;}
   await writeFile(join(directory,sha+'.'+actor.uid+'.owner'),JSON.stringify({filename:name}),{mode:0o600});
  }else if(sha){try{const owner=JSON.parse(await readFile(join(directory,sha+'.'+actor.uid+'.owner'),'utf8'));name=name??(owner.filename?safeUploadName(owner.filename):null);content=await readFile(join(directory,sha+'.xlsx'));}catch{throw new McpReadError('NOT_FOUND','文件不存在或不属于当前账号','请重新上传计划文件');}}
  else throw new McpReadError('BAD_REQUEST','需要content_base64或blob_sha');
  const file=join(directory,sha!+'.xlsx');const bytes=await readFile(file);if(createHash('sha256').update(bytes).digest('hex')!==sha)throw new McpReadError('BAD_REQUEST','文件摘要不匹配，请重新上传');return {sha:sha!,file,filename:name};
 }
 private async workbook(file:string){
  let normalized:Awaited<ReturnType<typeof orderedWorkbook>>|undefined;
  try{normalized=await orderedWorkbook(file);const workbook=new ExcelJS.Workbook();await workbook.xlsx.readFile(normalized.path);const index=workbook.views?.[0]?.activeTab??0,worksheet=workbook.worksheets[index]??workbook.worksheets[0];if(!worksheet||worksheet.rowCount>20000||worksheet.columnCount>128)throw new TaskPlanError('工作表为空或超过20000行/128列');const rows:unknown[][]=[];worksheet.eachRow({includeEmpty:true},row=>{const cells:unknown[]=[];for(let col=1;col<=worksheet.columnCount;col++){let value:any=row.getCell(col).value;if(value instanceof Date)value=value.toISOString().replace('T',' ').replace(/\.000Z$/,'');else if(value&&typeof value==='object')value=value.result??value.text??(value.richText?.map((t:any)=>t.text).join('')||'');cells.push(value);}rows.push(cells);});return rows;
  }catch(error){if(error instanceof TaskPlanError)throw error;throw new McpReadError('TASK_IMPORT_REJECTED','无法读取有效.xlsx计划文件');}finally{await normalized?.cleanup();}
 }
 async run(client:PoolClient,actor:McpActor,args:Record<string,unknown>):Promise<Record<string,unknown>>{
  if(actor.role!=='admin'||!actor.scopes.includes('write'))throw new McpReadError('FORBIDDEN_TOOL','计划导入仅管理员且需要write scope');
  const parsed=schema.safeParse(args);if(!parsed.success)throw new McpReadError('BAD_PARAM','导入动作、文件、行列或版本参数无效');const input=parsed.data;
  try{
   const source=await this.source(actor,input),grid=await this.workbook(source.file);let manual:TaskPlanLayout|undefined;
   if(input.cols||input.header_row!=null){const cols=Object.fromEntries(Object.entries(input.cols??{}).map(([key,value])=>[key.replace(/^col_/,''),Number(value)]));if(cols.station===undefined||Object.values(cols).some(value=>value>127))throw new McpReadError('BAD_REQUEST','cols需包含station列，列号从0开始');manual={header_row:input.header_row??1,cols,n_cols:Math.max(...Object.values(cols))+1,source:'manual'};}
   if(input.action==='apply'){if(!input.blob_sha)throw new McpReadError('CONFIRM_REQUIRED','请先预览，再携带blob_sha确认导入');await client.query("SELECT pg_advisory_xact_lock(hashtext('teams-write'),0)");await client.query("SELECT pg_advisory_xact_lock(hashtext('station-assets'),0)");}
   const stations=(await client.query<TaskPlanAssets['stations'][number]>(`SELECT s.place_id AS "placeId",s.name,l.name AS line,l.operator_short AS "operatorShort" FROM bd_station s JOIN bd_line l ON l.id=s.line_id WHERE s.place_id IS NOT NULL ORDER BY s.id`)).rows;
   const teams=(await client.query<TaskPlanAssets['teams'][number]>('SELECT id,name,status FROM bd_team ORDER BY id')).rows;
   const tasks=(await client.query<TaskPlanAssets['tasks'][number]>(`SELECT t.id,coalesce(t.place_id,s.place_id) AS "placeId",t.team_id AS "teamId" FROM bd_task t LEFT JOIN bd_station s ON s.id=t.station_id ORDER BY t.id`)).rows;
   const plan=buildTaskPlan(grid,{stations,teams,tasks},manual),token=digest({sha:source.sha,layout:plan.layout,summary:plan.summary,rows:plan.rows});
   if(input.action==='preview')return {applied:false,blob_sha:source.sha,filename:source.filename,layout:plan.layout,summary:plan.summary,teams_in_file:plan.team_names_found,rows:taskPlanRows(plan),plan_token:token,next:"确认无误后携带action='apply'和blob_sha；bad行需处理或显式skip_rows，可附plan_token防止覆盖过期预览"};
   if(input.plan_token&&input.plan_token!==token)throw new McpReadError('TASK_IMPORT_CHANGED','计划或资产已变化，请重新预览');
   const skip=new Set((input.skip_rows??[]).map(Number)),unresolved=plan.rows.filter(row=>row.decision==='bad'&&!skip.has(row.row_no));if(unresolved.length)throw new TaskPlanError('还有对不上的车站行：'+unresolved.map(row=>row.row_no).slice(0,12).join('、'));
   const byTeam=new Map<number,Set<number>>();for(const row of plan.rows)if(row.decision==='create'&&!skip.has(row.row_no)&&row.team_id){const ids=byTeam.get(row.team_id)??new Set();for(const [id]of row.create)ids.add(id);byTeam.set(row.team_id,ids);}
   const perTeam:{team_id:number;team:string;created:number;skipped:number}[]=[];let created=0;
   for(const [teamId,ids]of [...byTeam].sort(([a],[b])=>a-b)){let count=0,skipped=0;for(const placeId of ids){const result=await this.tasks.fromPlacesInTransaction(client,actor.uid,[placeId]);if(!result.created){skipped++;continue;}const taskId=result.ids[0],view=await this.tasks.progressViewInTransaction(client,taskId,actor.uid);await this.tasks.changeInTransaction(client,taskId,actor.uid,{action:'dispatch',teamId},view.token);count++;}created+=count;perTeam.push({team_id:teamId,team:teams.find(team=>team.id===teamId)!.name,created:count,skipped});}
   const after=(await client.query('SELECT id,team_id FROM bd_task WHERE id=ANY($1::int[])',[tasks.map(task=>task.id)])).rows;const before=new Map(tasks.map(task=>[task.id,task.teamId]));if(after.some(row=>before.get(row.id)!==row.team_id))throw new TaskPlanError('既有任务归属发生变化，导入已回滚');
   return {applied:true,created,by_team:perTeam,existing_untouched:plan.summary.existing,skipped_rows:plan.summary.skipped,rows:plan.summary.rows};
  }catch(error){if(error instanceof TaskPlanError)throw new McpReadError('TASK_IMPORT_REJECTED',error.message,'处理匹配问题或显式skip_rows后再导入');if(error instanceof BusinessError)throw new McpReadError(error.code,error.message);throw error;}
 }
}
