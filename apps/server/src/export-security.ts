import type {Database} from './database';
import {BusinessError} from './errors';
/** Shared by HTTP and MCP; only identifiers, never exported contents or credentials, enter this audit. */
export async function withExportGuard<T>(db:Database,userId:number,kind:string,target:string,work:()=>Promise<T>,succeeded:(value:T)=>boolean=()=>true):Promise<T>{
 const id=await db.transaction(async client=>{
  await client.query("SELECT pg_advisory_xact_lock(hashtext('security-export'),$1)",[userId]);
  const counts=(await client.query<{recent:number;active:number}>(`SELECT count(*) FILTER(WHERE created_at>now()-interval '5 minutes')::int AS recent,
   count(*) FILTER(WHERE finished_at IS NULL AND created_at>now()-interval '15 minutes')::int AS active
   FROM security_export_events WHERE user_id=$1 AND created_at>now()-interval '15 minutes'`,[userId])).rows[0];
  if(counts.recent>=10||counts.active>0)throw new BusinessError('EXPORT_RATE_LIMITED','导出过于频繁或已有导出正在执行，请稍后重试',429);
  return (await client.query<{id:string}>('INSERT INTO security_export_events(user_id,kind,target) VALUES($1,$2,$3) RETURNING id',[userId,kind,target.slice(0,128)])).rows[0].id;
 });
 try{const result=await work();await db.query('UPDATE security_export_events SET finished_at=now(),ok=$2 WHERE id=$1',[id,succeeded(result)]);return result;}
 catch(error){await db.query('UPDATE security_export_events SET finished_at=now(),ok=false WHERE id=$1',[id]).catch(()=>{});throw error;}
}
