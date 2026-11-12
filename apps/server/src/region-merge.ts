import {createHash,randomUUID} from 'node:crypto';
import type {PoolClient} from 'pg';
import {z} from 'zod';
import {mergeGridsWithMetricRivers,GridGeometryError,type GridMergeGeometry} from '../../../packages/domain/src/grid-geometry';
import {BusinessError} from './errors';
import {riverEvidence} from './river-evidence';
const id=z.number().int().positive().max(2147483647),rev=z.number().int().min(0);
const selection=z.object({id,revision:rev,zoneId:id.nullable(),zoneRevision:rev.nullable()}).strict();
const common=z.object({selections:z.array(selection).min(1).max(5000),mergeBoundaries:z.boolean(),name:z.string().trim().refine(value=>Array.from(value).length>=1&&Array.from(value).length<=128)}).strict();
const applySchema=common.extend({previewToken:z.string().regex(/^[a-f0-9]{64}$/),confirm:z.literal('MERGE_GRIDS')});
export async function regionMerge(client:PoolClient,actorId:number,value:unknown,apply=false){
 const parsed=(apply?applySchema:common).safeParse(value);if(!parsed.success)throw new BusinessError('VALIDATION','请选择不重复的网格并填写片区名称',400);
 const input=parsed.data,expected=new Map(input.selections.map(item=>[item.id,item]));
 if(expected.size!==input.selections.length||input.selections.some(row=>row.zoneId!==null&&row.zoneRevision===null))throw new BusinessError('VALIDATION','请选择不重复的网格并刷新版本信息',400);
 if(!apply)await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');
 const {rows:[actor]}=await client.query(`SELECT username,display_name FROM users WHERE id=$1 AND role='admin' AND status='active' AND is_active=true ${apply?'FOR SHARE':''}`,[actorId]);
 if(!actor)throw new BusinessError('FORBIDDEN','仅在岗管理员可合并片区',403);
 const ids=[...expected.keys()],{rows:snapshots}=await client.query('SELECT id,release_id FROM bd_work_grid WHERE id=ANY($1::int[])',[ids]);
 if(snapshots.length!==ids.length)throw new BusinessError('NOT_FOUND','网格不存在，请刷新后重试',404);
 const releases=new Set(snapshots.map(g=>g.release_id));if(releases.size!==1)throw new BusinessError('GRID_RELEASE_MISMATCH','不同版本的网格不能合并',409);
 const releaseId=snapshots[0].release_id;if(apply)await client.query('SELECT id FROM bd_zone_release WHERE id=$1 FOR UPDATE',[releaseId]);
 const zoneIds=[...new Set(input.selections.map(row=>row.zoneId).filter((n):n is number=>n!==null))].sort((a,b)=>a-b);
 const {rows:zones}=await client.query(`SELECT * FROM bd_region WHERE id=ANY($1::int[]) ORDER BY id ${apply?'FOR UPDATE':''}`,[zoneIds]);
 if(zones.length!==zoneIds.length||zones.some(z=>z.state==='archived'||z.release_id!==releaseId))throw new BusinessError('REVISION_CONFLICT','片区已变更，请刷新后重试',409);
 const byZone=new Map(zones.map(z=>[z.id,z]));
 const {rows:affected}=await client.query(`SELECT * FROM bd_work_grid WHERE id=ANY($1::int[]) OR zone_id=ANY($2::int[]) ORDER BY id ${apply?'FOR UPDATE':''}`,[ids,zoneIds]);
 const selected=affected.filter(g=>expected.has(g.id));
 if(selected.length!==ids.length)throw new BusinessError('REVISION_CONFLICT','网格已变更，请刷新后重试',409);
 for(const grid of selected){const previous=expected.get(grid.id)!;
  if(grid.release_id!==releaseId||grid.revision!==previous.revision||grid.zone_id!==previous.zoneId||(grid.zone_id!==null&&byZone.get(grid.zone_id)?.revision!==previous.zoneRevision))throw new BusinessError('REVISION_CONFLICT','网格或片区已变更，请刷新后重试',409);
 }
 if(zones.some(z=>z.team_id!==null||z.leader_id!==null))throw new BusinessError('REGION_ASSIGNED','请先召回片区，再调整网格归属',409);
 if(affected.some(g=>g.assignee_id!==null||g.progress||g.task_state!=='unvisited'))throw new BusinessError('GRID_PROTECTED','涉及已派发或已开展的网格，不能调整片区',409);
 const affectedIds=affected.map(g=>g.id);
 if((await client.query('SELECT id FROM bd_store_visit_log WHERE grid_id=ANY($1::int[]) LIMIT 1',[affectedIds])).rowCount)throw new BusinessError('GRID_PROTECTED','涉及已有巡店历史的网格，不能调整片区',409);
 if(selected.some(g=>g.state==='disabled'))throw new BusinessError('GRID_DISABLED','禁用网格不能划入片区',409);
 const evidence=await riverEvidence();
 async function geometry(rows:typeof affected){try{return await mergeGridsWithMetricRivers(rows.map(g=>g.geometry),evidence.faces);}catch(error){if(error instanceof GridGeometryError)throw new BusinessError(error.code,error.message,409);throw error;}}
 const combined=await geometry(selected),remaining:{zone:any;members:typeof affected;geometry:GridMergeGeometry|null}[]=[];
 for(const zone of zones){const members=affected.filter(g=>g.zone_id===zone.id&&!expected.has(g.id));remaining.push({zone,members,geometry:members.length?await geometry(members):null});}
 const {rows:mappings}=await client.query('SELECT id,release_id,grid_id,source_id FROM bd_grid_store WHERE grid_id=ANY($1::int[]) ORDER BY id',[affectedIds]);
 const counts=new Map<number,number>();for(const row of mappings)counts.set(row.grid_id,(counts.get(row.grid_id)??0)+1);
 const count=(rows:typeof affected)=>rows.reduce((sum,g)=>sum+(counts.get(g.id)??0),0);
 const previewToken=createHash('sha256').update(JSON.stringify({name:input.name,mergeBoundaries:input.mergeBoundaries,zones,affected,mappings,ids:selected.map(g=>g.id),evidence:evidence.fingerprint})).digest('hex');
 const preview={previewToken,name:input.name,mergeBoundaries:input.mergeBoundaries,gridCount:selected.length,storeCount:count(selected),areaKm2:combined.areaM2/1e6,shapeValid:combined.shape.acceptable,
  geometry:combined.geometry,displayGeometry:input.mergeBoundaries?combined.displayGeometry:{type:'GeometryCollection',geometries:selected.map(g=>g.geometry)},
  archivedZoneIds:remaining.filter(r=>!r.members.length).map(r=>r.zone.id),remainingZones:remaining.map(r=>({id:r.zone.id,name:r.zone.name,grids:r.members.length,stores:count(r.members),areaKm2:r.geometry?.areaM2? r.geometry.areaM2/1e6:0})),riverFiles:evidence.files};
 if(!apply)return preview;
 if((input as z.infer<typeof applySchema>).previewToken!==previewToken)throw new BusinessError('PREVIEW_CHANGED','网格、片区或河道证据已变化，请重新预览',409);
 const {rows:[created]}=await client.query(`INSERT INTO bd_region(release_id,source_id,name,geometry,merge_boundaries,area_km2,store_count,review,state,revision,updated_at)
  VALUES($1,$2,$3,$4,$5,$6,$7,'[]','ready',0,now()) RETURNING id,revision`,[releaseId,'manual-'+randomUUID().replaceAll('-',''),input.name,JSON.stringify(combined.geometry),input.mergeBoundaries,combined.areaM2/1e6,count(selected)]);
 await client.query('UPDATE bd_work_grid SET zone_id=$1,revision=revision+1,updated_at=now() WHERE id=ANY($2::int[])',[created.id,selected.map(g=>g.id)]);
 async function audit(action:string,identity:number,before:unknown,after:unknown){await client.query("INSERT INTO bd_log(domain,action,ref_id,ref_label,field,old_value,new_value,actor,actor_name,note,created_at) VALUES('zone',$1,$2,$3,'operations',$4,$5,$6,$7,'',now())",[action,identity,String(identity),JSON.stringify(before),JSON.stringify(after),actor.username,actor.display_name]);}
 for(const entry of remaining){const {zone,members,geometry:rest}=entry;
  if(rest)await client.query('UPDATE bd_region SET geometry=$1,area_km2=$2,store_count=$3,workload=NULL,revision=revision+1,updated_at=now() WHERE id=$4',[JSON.stringify(rest.geometry),rest.areaM2/1e6,count(members),zone.id]);
  else await client.query("UPDATE bd_region SET state='archived',store_count=0,workload=NULL,revision=revision+1,updated_at=now() WHERE id=$1",[zone.id]);
  await audit('regroup',zone.id,{revision:zone.revision,state:zone.state,store_count:zone.store_count},{state:rest?zone.state:'archived',grid_ids:members.map(g=>g.id),new_zone_id:created.id});
 }
 await audit('merge_grids',created.id,{grids:selected.map(g=>({id:g.id,zone_id:g.zone_id,revision:g.revision}))},{name:input.name,merge_boundaries:input.mergeBoundaries,grid_ids:selected.map(g=>g.id),geometry_sha256:createHash('sha256').update(Buffer.from(combined.normalizedWkbHex,'hex')).digest('hex'),geometry_hash_convention:'normalized-wkb-le-2d-zero-v1',river_fingerprint:evidence.fingerprint});
 return {...preview,id:created.id,revision:created.revision};
}
