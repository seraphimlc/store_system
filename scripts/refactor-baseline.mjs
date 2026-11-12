/** Legacy database evidence only. Never initializes schema or reads raw_records rows. */
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

export function baselineSql() {
  return `BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '30s';
SET LOCAL lock_timeout = '2s';
SELECT json_build_object(
 'database',current_database(), 'server_version',current_setting('server_version'),
 'read_only',current_setting('transaction_read_only'),
 'migration',(SELECT json_agg(version_num ORDER BY version_num) FROM alembic_version),
 'columns',(SELECT json_agg(x ORDER BY x.table_name,x.ordinal_position) FROM (
   SELECT table_name,ordinal_position,column_name,data_type,udt_name,is_nullable,column_default,character_maximum_length
   FROM information_schema.columns WHERE table_schema='public'
 ) x),
 'constraints',(SELECT json_agg(x ORDER BY x.table_name,x.constraint_name) FROM (
   SELECT c.relname AS table_name,co.conname AS constraint_name,co.contype AS type,pg_get_constraintdef(co.oid) AS definition
   FROM pg_constraint co JOIN pg_class c ON c.oid=co.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='public'
 ) x),
 'indexes',(SELECT json_agg(x ORDER BY x.tablename,x.indexname) FROM (
   SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public'
 ) x),
 'formal_months',(SELECT json_agg(x ORDER BY x.month) FROM (
   SELECT to_char(japan_date,'YYYY-MM') AS month,count(*) AS records,sum(points) AS points FROM formal_records GROUP BY 1
 ) x),
 'performance_months',(SELECT json_agg(x ORDER BY x.month) FROM (
   SELECT month,count(*) AS people,sum(records) AS records,sum(p1) AS p1,sum(p2) AS p2,sum(points) AS points,sum(salary) AS salary
   FROM month_perf_records GROUP BY month
 ) x),
 'payments',(SELECT json_agg(x ORDER BY x.month,x.seq) FROM (
   SELECT month,seq,count(*) AS rows,sum(amount) AS amount,sum(adjust_applied) AS adjust_applied FROM payroll_payments GROUP BY month,seq
 ) x),
 'adjusts',(SELECT json_agg(x ORDER BY x.source_month,x.status) FROM (
   SELECT source_month,status,count(*) AS rows,sum(adjust_amount) AS amount,sum(settled_amount) AS settled,sum(remaining) AS remaining
   FROM payroll_adjusts GROUP BY source_month,status
 ) x),
 'task_counts',json_build_object(
   'batches',(SELECT count(*) FROM bd_main_task),'regions',(SELECT count(*) FROM bd_region_task),
   'grids',(SELECT count(*) FROM bd_grid_task),'stores',(SELECT count(*) FROM bd_store_task)),
 'invariants',json_build_object(
   'month_points_arithmetic',(SELECT count(*) FROM month_perf_records WHERE points<>p1+p2*2),
   'report_counts_arithmetic',(SELECT count(*) FROM staff_daily_reports WHERE total_cnt<>p1_cnt+p2_cnt),
   'debt_arithmetic',(SELECT count(*) FROM payroll_adjusts WHERE remaining<>adjust_amount-settled_amount),
   'orphan_settlement_links',(SELECT count(*) FROM payroll_settlement_links l LEFT JOIN payroll_adjusts a ON a.id=l.adjust_id LEFT JOIN payroll_payments p ON p.id=l.payment_id WHERE a.id IS NULL OR p.id IS NULL),
   'grid_store_counts',(SELECT count(*) FROM bd_grid_task g LEFT JOIN (
     SELECT grid_task_id,count(*) AS total,count(*) FILTER(WHERE status='completed') AS completed FROM bd_store_task GROUP BY grid_task_id
   ) s ON s.grid_task_id=g.id WHERE g.total_stores<>coalesce(s.total,0) OR g.completed_stores<>coalesce(s.completed,0)),
   'orphan_store_tasks',(SELECT count(*) FROM bd_store_task s LEFT JOIN bd_grid_task g ON g.id=s.grid_task_id WHERE g.id IS NULL)),
 'mcp_clients',(SELECT json_agg(x ORDER BY x.calls DESC) FROM (
   SELECT CASE WHEN coalesce(client_info,'') ~* 'pentest|probe|^p/|^i[0-9]' THEN 'test-labelled'
     WHEN client_info LIKE 'visit-settle/%' THEN 'visit-settle' ELSE 'other-or-unknown' END AS client_group,
     count(*) AS calls,count(*) FILTER(WHERE ok) AS success,min(created_at) AS first,max(created_at) AS last
   FROM mcp_audit_log GROUP BY 1
 ) x),
 'mcp_tools',(SELECT json_agg(x ORDER BY x.tool,x.ok) FROM (
   SELECT tool,ok,count(*) AS calls,min(created_at) AS first,max(created_at) AS last FROM mcp_audit_log GROUP BY tool,ok
 ) x)
);
ROLLBACK;`;
}

export function summarize(snapshot) {
  if (snapshot.read_only !== 'on') throw new Error('Read-only transaction was not confirmed');
  const schema = { columns: snapshot.columns, constraints: snapshot.constraints, indexes: snapshot.indexes };
  return {
    database: snapshot.database,
    schemaSha256: createHash('sha256').update(JSON.stringify(schema)).digest('hex'),
    tables: new Set(snapshot.columns.map(c => c.table_name)).size,
    columns: snapshot.columns.length,
    migration: snapshot.migration,
    invariants: snapshot.invariants,
    taskCounts: snapshot.task_counts,
    // Nonzero legacy invariants are evidence, not permission to repair the database.
    deviations: Object.entries(snapshot.invariants).filter(([, value]) => Number(value) !== 0).map(([name, count]) => ({ name, count })),
  };
}

export async function main(argv = process.argv.slice(2)) {
  const allowed = new Set(['--container', '--database', '--user', '--output']);
  const opts = {};
  for (let i = 0; i < argv.length; i += 2) {
    if (!allowed.has(argv[i]) || !argv[i + 1] || argv[i + 1].startsWith('--')) throw new Error('Expected --container/--database/--user/--output value');
    opts[argv[i]] = argv[i + 1];
  }
  const output = resolve(opts['--output'] ?? 'data/refactor/baseline.json');
  // Explicit Docker target; never auto-load .env or fall back to a production connection string.
  const args = ['exec', '-i', opts['--container'] ?? 'store-system-local-db-1', 'psql', '-X', '-A', '-t', '-q',
    '-v', 'ON_ERROR_STOP=1', '-U', opts['--user'] ?? 'store_settle', '-d', opts['--database'] ?? 'store_settle'];
  const result = spawnSync('docker', args, { input: baselineSql(), encoding: 'utf8', maxBuffer: 8 * 1024 * 1024, timeout: 60000 });
  if (result.error || result.status !== 0) throw new Error(`Read-only baseline failed (exit ${result.status ?? 'unavailable'}); no snapshot written`);
  const snapshot = JSON.parse(result.stdout.trim());
  const summary = summarize(snapshot);
  await mkdir(dirname(output), { recursive: true });
  await writeFile(output, JSON.stringify({ capturedAt: new Date().toISOString(), summary, snapshot }, null, 2) + '\n', { mode: 0o600 });
  console.log(JSON.stringify({ output, ...summary }, null, 2));
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().catch(error => { console.error(error.message); process.exitCode = 1; });
}
