/** Explicit local telemetry maintenance. Preview is read-only; never called by API/Worker startup. */
import {createRequire} from 'node:module';import {pathToFileURL} from 'node:url';import {resolve} from 'node:path';import {spawnSync} from 'node:child_process';
const require=createRequire(new URL('../apps/server/package.json',import.meta.url));
const TEMP=/^store_test_[0-9]{13}_[a-f0-9]{12}_refactor_p1$/;
export function retentionTarget(value,isolated){
 const url=new URL(value),database=decodeURIComponent(url.pathname.slice(1));
 if(!['postgres:','postgresql:'].includes(url.protocol)||!['localhost','127.0.0.1','[::1]'].includes(url.hostname)||!url.username||!(database==='store_settle'||isolated==='1'&&TEMP.test(database)))throw Error('Retention is restricted to local store_settle or an explicitly isolated test database');
 return {url,database,user:decodeURIComponent(url.username)};
}
export function retentionOptions(args,now=Date.now()){
 const options={apply:false,limit:1000};const seen=new Set();
 for(const arg of args){const [name,...rest]=arg.split('='),value=rest.join('=');if(seen.has(name))throw Error('Duplicate retention option');seen.add(name);
  if(name==='--apply'&&!rest.length)options.apply=true;
  else if(name==='--before'&&rest.length)options.before=value;
  else if(name==='--max-delete'&&/^[1-9][0-9]*$/.test(value))options.limit=Number(value);
  else if(name==='--confirm-database'&&rest.length)options.confirmDatabase=value;
  else throw Error('Usage: search-log-retention.mjs --before=UTC_ISO [--max-delete=1000] [--apply --confirm-database=NAME]');
 }
 if(!options.before||!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(options.before)||!Number.isFinite(Date.parse(options.before))||new Date(options.before).toISOString()!==options.before)throw Error('An explicit canonical UTC --before=YYYY-MM-DDTHH:mm:ss.sssZ is required');
 if(Date.parse(options.before)>now-7*86400000)throw Error('Cutoff must be at least seven days ago; no retention period is chosen automatically');
 if(!Number.isSafeInteger(options.limit)||options.limit<1||options.limit>10000)throw Error('--max-delete must be between 1 and 10000');
 if(options.apply&&!options.confirmDatabase)throw Error('Apply requires explicit --confirm-database');
 return options;
}
const eligible=`l.created_at < ($1::timestamptz AT TIME ZONE 'UTC') AND NOT EXISTS(SELECT 1 FROM public.bd_store_search_feedback f WHERE f.search_log_id=l.id AND f.created_at >= ($1::timestamptz AT TIME ZONE 'UTC'))`;
export async function retainSearchTelemetry(client,options,database){
 if(options.apply&&options.confirmDatabase!==database)throw Error('Confirmation does not match the selected database');
 await client.query(options.apply?'BEGIN':'BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY');
 try{
  await client.query("SET LOCAL statement_timeout='5s'; SET LOCAL lock_timeout='1s'");
  const identity=(await client.query("SELECT current_database() AS database,current_setting('transaction_read_only') AS readonly,to_regclass('public.bd_store_search_log')::text AS logs,to_regclass('public.bd_store_search_feedback')::text AS feedback")).rows[0];
  if(identity.database!==database||!identity.logs||!identity.feedback)throw Error('Unexpected database or missing telemetry schema (004 migration required)');
  if(!options.apply&&identity.readonly!=='on')throw Error('Preview must be read-only');
  if(options.apply&&!(await client.query('SELECT pg_try_advisory_xact_lock(20261113,11) AS locked')).rows[0].locked)throw Error('Another search retention run is active');
  const counts=(await client.query(`SELECT (SELECT count(*)::int FROM public.bd_store_search_log) AS total_logs,(SELECT count(*)::int FROM public.bd_store_search_feedback) AS total_feedback,(SELECT count(*)::int FROM public.bd_store_search_log l WHERE ${eligible}) AS eligible_logs,(SELECT count(*)::int FROM public.bd_store_search_log l WHERE l.created_at<($1::timestamptz AT TIME ZONE 'UTC') AND EXISTS(SELECT 1 FROM public.bd_store_search_feedback f WHERE f.search_log_id=l.id AND f.created_at>=($1::timestamptz AT TIME ZONE 'UTC'))) AS old_logs_protected_by_recent_feedback`,[options.before])).rows[0];
  const selected=(await client.query(`SELECT l.id FROM public.bd_store_search_log l WHERE ${eligible} ORDER BY l.created_at,l.id LIMIT $2 ${options.apply?'FOR UPDATE OF l SKIP LOCKED':''}`,[options.before,options.limit])).rows.map(row=>row.id);
  const cascadeFeedback=(await client.query('SELECT count(*)::int AS n FROM public.bd_store_search_feedback WHERE search_log_id=ANY($1::int[])',[selected])).rows[0].n;
  let deleted=0;
  if(options.apply){const deletion=await client.query('DELETE FROM public.bd_store_search_log WHERE id=ANY($1::int[]) RETURNING id',[selected]);deleted=deletion.rowCount;if(deleted!==selected.length)throw Error('Selected telemetry rows changed before deletion');}
  await client.query('COMMIT');
  return {mode:options.apply?'apply':'preview',database,cutoffUtc:options.before,batchLimit:options.limit,...counts,selectedLogs:selected.length,selectedFeedback:cascadeFeedback,deletedLogs:deleted,deletedFeedback:options.apply?cascadeFeedback:0,remainingEligibleEstimate:Math.max(0,counts.eligible_logs-deleted),previewReadOnly:!options.apply,policy:'explicit cutoff, no automatic retention period; protect old requests with recent feedback; one bounded batch',businessTablesTouched:false};
 }catch(error){await client.query('ROLLBACK');throw error;}
}
export async function runRetentionCli(args=process.argv.slice(2),env=process.env){
 const options=retentionOptions(args),target=retentionTarget(env.NEXT_DATABASE_URL??'',env.NEXT_TEST_ISOLATED);
 if(options.apply&&options.confirmDatabase!==target.database)throw Error('Confirmation does not match the selected database');
 // A localhost URL can be an SSH tunnel: prove this connection uses the existing local PG cluster.
 const {Pool}=require('pg'),pool=new Pool({connectionString:target.url.href,max:1,application_name:'store-search-log-retention-local',connectionTimeoutMillis:3000});const client=await pool.connect();
 try{
  const container=env.NEXT_TEST_PG_CONTAINER??'store-system-local-db-1';if(!/^[a-zA-Z0-9][a-zA-Z0-9_.-]*$/.test(container))throw Error('Invalid local database container');
  const actual=(await client.query('SELECT system_identifier::text AS id FROM pg_control_system()')).rows[0].id;
  const probe=spawnSync('docker',['exec',container,'psql','-XAt','-U',target.user,'-d','postgres','-c','SELECT system_identifier::text FROM pg_control_system()'],{encoding:'utf8',timeout:10000});
  if(probe.error||probe.status!==0||probe.stdout.trim()!==actual)throw Error('Database does not match the existing local PostgreSQL cluster');
  const report=await retainSearchTelemetry(client,options,target.database);console.log(JSON.stringify({...report,localClusterVerified:true,productionAccessed:false},null,2));return report;
 }finally{client.release();await pool.end();}
}
if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href){runRetentionCli().catch(()=>{console.error('[search-retention] Refused or rolled back; verify local target, telemetry schema and explicit options. No credentials or record content logged.');process.exitCode=1;});}
