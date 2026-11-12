import {createHash} from 'node:crypto';
import type {PoolClient} from 'pg';
import {BusinessError} from './errors';

/** Caller owns the transaction; release -> region -> grid locks match regroup/enable. */
export async function regionSplit(client:PoolClient,actorId:number,id:number,revision:number,previewToken?:string){
 const apply=previewToken!==undefined;
 if(!apply)await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');
 const {rows:[actor]}=await client.query(`SELECT username,display_name FROM users
  WHERE id=$1 AND role='admin' AND is_active=true AND status='active' ${apply?'FOR SHARE':''}`,[actorId]);
 if(!actor)throw new BusinessError('FORBIDDEN','仅在岗管理员可拆分片区',403);
 const {rows:[snapshot]}=await client.query('SELECT release_id FROM bd_region WHERE id=$1',[id]);
 if(!snapshot)throw new BusinessError('NOT_FOUND','片区不存在，请刷新后重试',404);
 if(apply)await client.query('SELECT id FROM bd_zone_release WHERE id=$1 FOR UPDATE',[snapshot.release_id]);
 const {rows:[region]}=await client.query(`SELECT * FROM bd_region WHERE id=$1 ${apply?'FOR UPDATE':''}`,[id]);
 if(!region)throw new BusinessError('NOT_FOUND','片区不存在，请刷新后重试',404);
 if(region.release_id!==snapshot.release_id||region.revision!==revision)throw new BusinessError('REVISION_CONFLICT','片区已变化，请刷新后重试',409);
 if(region.state==='archived')throw new BusinessError('REGION_ARCHIVED','片区已归档',409);
 const {rows:grids}=await client.query(`SELECT * FROM bd_work_grid WHERE zone_id=$1 ORDER BY id ${apply?'FOR UPDATE':''}`,[id]);
 if(region.team_id!==null||region.leader_id!==null)throw new BusinessError('REGION_ASSIGNED','请先召回片区，再调整网格归属',409);
 if(grids.some(g=>g.assignee_id!==null||g.progress||g.task_state!=='unvisited'))throw new BusinessError('GRID_PROTECTED','涉及已派发或已开展的网格，不能调整片区',409);
 const ids=grids.map(g=>g.id);
 if((await client.query('SELECT id FROM bd_store_visit_log WHERE grid_id=ANY($1::int[]) LIMIT 1',[ids])).rowCount)throw new BusinessError('GRID_PROTECTED','涉及已有巡店历史的网格，不能调整片区',409);
 const {rows:mappings}=await client.query('SELECT id,grid_id,source_id,release_id FROM bd_grid_store WHERE grid_id=ANY($1::int[]) ORDER BY id',[ids]);
 const token=createHash('sha256').update(JSON.stringify({region,grids,mappings})).digest('hex');
 const preview={id:region.id,name:region.name,revision:region.revision,previewToken:token,gridCount:grids.length,storeCount:mappings.length,
  grids:grids.map(g=>({id:g.id,name:g.name||g.source_id,state:g.state,revision:g.revision})),archived:true};
 if(!apply)return preview;
 if(token!==previewToken)throw new BusinessError('PREVIEW_CHANGED','片区或网格内容已变化，请重新预览',409);
 await client.query('UPDATE bd_work_grid SET zone_id=NULL,revision=revision+1,updated_at=now() WHERE id=ANY($1::int[])',[ids]);
 await client.query("UPDATE bd_region SET state='archived',store_count=0,workload=NULL,revision=revision+1,updated_at=now() WHERE id=$1",[id]);
 const before={state:region.state,revision:region.revision,grid_ids:ids},after={state:'archived',released_grid_ids:ids};
 await client.query("INSERT INTO bd_log(domain,action,ref_id,ref_label,field,old_value,new_value,actor,actor_name,note,created_at) VALUES('zone','split_zone',$1,$2,'operations',$3,$4,$5,$6,'',now())",[id,String(id),JSON.stringify(before),JSON.stringify(after),actor.username,actor.display_name]);
 return {...preview,revision:region.revision+1};
}
