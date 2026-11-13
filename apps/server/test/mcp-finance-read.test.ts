import 'reflect-metadata';
import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash, randomBytes } from 'node:crypto';
import type { AddressInfo } from 'node:net';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { createApp } from '../src/main';
import { Database } from '../src/database';
import { McpFinanceReadService } from '../src/mcp-finance-read';
import type { McpActor } from '../src/mcp';
import { spawnLegacyPython } from '../../../scripts/legacy-python.mjs';

const actor: McpActor = { uid: 1, tokenId: 1, role: 'staff', scopes: ['read'], personCode: null, username: 'fixture', displayName: '', personName: null };
test('MCP finance reads reject role before arguments and require explicit valid view/month/task identity', async () => {
  const service = new McpFinanceReadService({} as any), db = { query: async () => { throw Error('Unexpected business read'); } } as any;
  for (const role of ['staff', 'leader'] as const) for (const tool of ['visit_payroll', 'visit_recon']) await assert.rejects(service.run(db, { ...actor, role }, tool, { month: 'bad', task_id: 'bad' }), { code: 'FORBIDDEN_TOOL' });
  const admin = { ...actor, role: 'admin' as const };
  for (const args of [{}, { month: '2026-13' }, { month: '2026/10' }]) await assert.rejects(service.run(db, admin, 'visit_payroll', args), { code: 'BAD_MONTH' });
  await assert.rejects(service.run(db, admin, 'visit_payroll', { month: '2026-10', view: 'unknown' }), { code: 'BAD_PARAM' });
  await assert.rejects(service.run(db, admin, 'visit_recon', { view: 'interpret' }), { code: 'BAD_PARAM' });
  await assert.rejects(service.run(db, admin, 'visit_recon', { task_id: -1 }), { code: 'BAD_PARAM' });
  await assert.rejects(service.run(db, admin, 'visit_recon', { month: '' }), { code: 'BAD_MONTH' });
});

const databaseUrl = process.env.NEXT_TEST_DATABASE_URL;
test('schema-only finance SDK: legacy rows/adjusts/trace and recon status/diff/interpret, frozen snapshots and read-only ledgers', { skip: !databaseUrl }, async () => {
  assert.match(new URL(databaseUrl!).pathname, /^\/store_test_[0-9]{13}_[a-f0-9]{12}_refactor_p1$/);
  const keys = ['BONUS_GROUP', 'BONUS_AMOUNT', 'BONUS_GROUP_SCHEDULE', 'BONUS_AMOUNT_SCHEDULE'];
  const environment = Object.fromEntries(keys.map(key => [key, process.env[key]]));
  Object.assign(process.env, { BONUS_GROUP: '3', BONUS_AMOUNT: '100', BONUS_GROUP_SCHEDULE: '2026-09=5', BONUS_AMOUNT_SCHEDULE: '2026-09=250' });
  const app = await createApp({ databaseUrl: databaseUrl!, sessionSecret: 'x'.repeat(40), cookieSecure: false, host: '127.0.0.1', port: 3000 });
  const db = app.get(Database), users: number[] = [], tasks: number[] = [], clients = new Map<string, Client>();
  const codes = ['MCP-FIN-A', 'MCP-FIN-B', 'MCP-FIN-NO-WAGE', 'MCP-FIN-EMPTY'];
  let imp: number | undefined, configuration: number | undefined;
  const transaction = db.transaction.bind(db); let queries = 0;
  db.transaction = fn => transaction(client => fn({ query: (...args: any[]) => { queries++; return (client.query as any)(...args); } } as any));
  const fingerprint = async () => {
    const result: Record<string, string> = {};
    for (const table of ['payroll_period_rows', 'month_perf_records', 'payroll_adjusts', 'payroll_payments', 'payroll_settlement_links', 'adjust_records', 'recon_tasks', 'recon_results', 'recon_day_rows', 'recon_data_rows', 'formal_records']) result[table] = (await db.query(`SELECT md5(coalesce(string_agg(row_to_json(t)::text,'' ORDER BY id),'')) AS value FROM ${table} t`)).rows[0].value;
    return result;
  };
  try {
    for (const code of codes) await db.query('INSERT INTO persons(code,display_name,created_at) VALUES($1,$2,now())', [code, code === codes[3] ? '' : code + ' name']);
    for (const role of ['admin', 'leader', 'staff']) users.push((await db.query(`INSERT INTO users(username,password_hash,display_name,role,status,is_active,must_change_password,lang,created_at)
      VALUES($1,'fixture-only-hash',$1,$2,'active',true,false,'zh',now()) RETURNING id`, ['mcp-fin-' + role, role])).rows[0].id);
    configuration = (await db.query("INSERT INTO sys_configs(config_month,per_point,bonus_group,bonus_amount,staff_visible_from,updated_at) VALUES('2026-10',999,2,9999,'2026-10',now()) RETURNING id")).rows[0].id;
    await db.query(`INSERT INTO month_perf_records(month,person_code,points,salary,per_point,diff_amount,created_at) VALUES
      ('2026-09',$1,73,12345,271,-600,now()),('2026-09',$2,100,22222,999,300,now()),('2026-10',$1,75,0,300,900,now())`, codes.slice(0, 2));
    await db.query(`INSERT INTO payroll_period_rows(month,person_code,half1_points,half2_points,half1_amount,half2_amount,half1_bonus,half2_bonus,
      half1_records,half1_p1,half1_p2,half2_records,half2_p1,half2_p2,prev_adjust_points,prev_adjust_amount,diff_points,diff_amount,adjust_points,adjust_amount,updated_at) VALUES
      ('2026-07',$1,1,2,700,800,10,20,3,2,1,4,3,1,11,-1000,4,-600,1,100,now()),
      ('2026-08',$1,2,3,900,1000,30,40,4,3,1,5,4,1,17,-400,8,-900,2,200,now()),
      ('2026-09',$1,10,20,1234,5678,90,110,12,8,4,22,18,4,99,0,5,-500,1,100,now()),
      ('2026-09',$2,4,5,1100,1200,0,0,3,2,1,4,3,1,9,-700,7,-700,2,200,now()),
      ('2026-10',$1,2,8,600,2000,0,0,1,0,1,5,2,3,88,-9000,2,300,1,100,now()),
      ('2026-10',$2,4,4,1000,1000,0,0,4,4,0,4,4,0,77,-8000,3,-100,0,0,now()),
      ('2026-11',$1,1,2,500,600,0,0,1,1,0,2,2,0,0,0,0,0,0,0,now())`, codes.slice(0, 2));
    for (const [index, selected] of ['2026-09', '2026-09', '2026-10', '2026-11', '2027-02', '2026-10'].entries()) tasks.push((await db.query(`INSERT INTO recon_tasks(kind,status,created_by,created_at,finished_at,params,summary)
      VALUES($1,$2,$3,'2026-10-01 01:02:03.123456',$4,$5,$6) RETURNING id`, [index === 5 ? 'legacy_fixture' : 'monthly_v3', index === 4 ? 'pending' : 'done', users[0], index === 4 ? null : '2026-10-02 03:04:05.123456',
      JSON.stringify({ month: selected, file: 'fixture-' + index + '.xlsx', version: index + 1 }), JSON.stringify({ compared: 3, diff_count: index === 3 ? 0 : 2, ...(index === 1 ? { sys_only: [codes[3], codes[1]], ai_interpret: { text: 'stored fixture interpretation', model: 'fixture-model', at: '2026-10-02T03:04:05Z' } } : {}) })])).rows[0].id);
    await db.query("UPDATE recon_tasks SET params=params::jsonb||$1::jsonb WHERE id=$2", [JSON.stringify({ replaced_by: tasks[1] }), tasks[0]]);
    for (const task of tasks.slice(0, 3)) {
      await db.query(`INSERT INTO recon_results(task_id,submitter_code,system_value,report_value,diff,status,confirmed,note) VALUES
        ($1,$2,8,10,-2,'diff',false,$3),($1,$4,15,14,1,'diff',true,''),($1,$5,0,0,0,'same',false,''),($1,NULL,NULL,NULL,NULL,'same',false,'')`, [task, codes[0], codes[0] + ' name 点数差异', codes[1], codes[3]]);
      await db.query(`INSERT INTO recon_day_rows(task_id,ref_date,person_code,sys_points,rep_points,sys_count,rep_count,diff,side,note,created_at)
        VALUES($1,'2026-09-02',$2,0,0,1,0,0,'sys_only','fixture count difference',now())`, [task, codes[2]]);
      await db.query(`INSERT INTO recon_data_rows(task_id,ref_date,person_code,person_name,points,cnt,created_at) VALUES
        ($1,'2026-09-02',$2,'',6,2,now()),($1,'2026-09-03',$2,'later name',4,3,now()),($1,'2026-09-02',$3,'',1,1,now())`, [task, codes[0], codes[3]]);
    }
    await db.query(`INSERT INTO adjust_records(month,applied_to_month,person_code,amount,per_point,amount_adj,reason,source_task_id,source_points_diff,created_at) VALUES
      ('2026-09','2026-10',$1,-2,300,-600,'fixture confirmed',$2,2,'2026-09-30 03:04:05.123456'),
      ('2026-10','2026-11',$1,3,300,900,'fixture confirmed',$3,-3,'2026-10-31 03:04:05')`, [codes[0], tasks[1], tasks[2]]);
    const adjustments = (await db.query(`INSERT INTO payroll_adjusts(source_month,person_code,adjust_amount,settled_amount,remaining,status,settled_at,source_task_id,updated_at) VALUES
      ('2026-09',$1,-500,-500,0,'settled','2026-10-15 03:04:05.123000',$3,now()),
      ('2026-09',$2,-700,-250,-450,'in_progress',NULL,$3,now()),
      ('2026-09',$5,-400,0,-400,'in_progress',NULL,$3,now()),
      ('2026-10',$1,300,300,0,'settled','2026-11-15 03:04:05',$4,now()) RETURNING id`, [codes[0], codes[1], tasks[1], tasks[2], codes[2]])).rows;
    const payments = (await db.query(`INSERT INTO payroll_payments(month,person_code,seq,points,amount,bonus,adjust_applied,adjust_leftover,paid_at) VALUES
      ('2026-10',$1,1,2,100,0,-500,0,'2026-10-15 03:04:05'),('2026-10',$2,1,4,800,0,-200,-500,'2026-10-15 03:04:05'),
      ('2026-10',$2,2,4,950,0,-50,-450,'2026-10-31 03:04:05'),('2026-11',$1,1,1,800,0,300,NULL,'2026-11-15 03:04:05') RETURNING id`, codes.slice(0, 2))).rows;
    for (const [adjust, payment, selected, code, amount] of [[adjustments[0].id, payments[0].id, '2026-10', codes[0], -500], [adjustments[1].id, payments[1].id, '2026-10', codes[1], -200], [adjustments[1].id, payments[2].id, '2026-10', codes[1], -50], [adjustments[3].id, payments[3].id, '2026-11', codes[0], 300]] as const) await db.query('INSERT INTO payroll_settlement_links(adjust_id,payment_id,month,person_code,amount,created_at) VALUES($1,$2,$3,$4,$5,now())', [adjust, payment, selected, code, amount]);
    imp = (await db.query(`INSERT INTO imports(file_name,file_sha256,file_size,stored_path,uploaded_by,uploaded_at,parsed_sheets,ignored_sheets,total_rows,parsed_rows,status,warnings,errors,created_at)
      VALUES('mcp-fin-fixture','mcp-fin-fixture',0,'fixture',$1,now(),'[]','[]',0,0,'parsed','[]','[]',now()) RETURNING id`, [users[0]])).rows[0].id;
    for (const [index, selected, points] of [[0, '2026-09', 1], [1, '2026-09', 2], [2, '2026-09', 4], [3, '2026-10', 9]] as const) {
      const raw = (await db.query(`INSERT INTO raw_records(import_id,sheet_name,excel_row,store_id_raw,store_name_local_raw,store_name_en_raw,modified_raw,submitter_raw,submitter_code,record_id_raw,original_row,created_at)
        VALUES($1,'fixture',$2,$3,$3,'',$4,'',$5,$3,'{}',now()) RETURNING id`, [imp, index + 1, 'mcp-fin-' + index, selected + '-02', codes[0]])).rows[0].id;
      await db.query('INSERT INTO formal_records(import_id,raw_record_id,person_code,store_id_raw,japan_date,points,created_at) VALUES($1,$2,$3,$4,$5,$6,now())', [imp, raw, codes[0], 'mcp-fin-' + index, selected + '-02', points]);
    }
    await app.listen(0, '127.0.0.1');
    const url = new URL(`http://127.0.0.1:${(app.getHttpServer().address() as AddressInfo).port}/mcp`);
    for (const [index, role] of ['admin', 'leader', 'staff'].entries()) {
      const raw = randomBytes(32).toString('base64url');
      await db.query("INSERT INTO api_tokens(user_id,name,token_prefix,token_hash,scopes,created_at) VALUES($1,'finance read fixture',$2,$3,'read',now())", [users[index], raw.slice(0, 8), createHash('sha256').update(raw).digest('hex')]);
      const client = new Client({ name: 'mcp-fin-fixture', version: '1' }); clients.set(role, client);
      await client.connect(new StreamableHTTPClientTransport(url, { requestInit: { headers: { Authorization: `Bearer ${raw}` } } }));
    }
    const call = async (role: string, tool: string, args: Record<string, unknown>) => { const response = await clients.get(role)!.callTool({ name: tool, arguments: args }); return JSON.parse((response.content as { text: string }[])[0].text); };
    for (const role of ['leader', 'staff']) {
      assert.ok(!(await clients.get(role)!.listTools()).tools.some(tool => ['visit_payroll', 'visit_recon'].includes(tool.name)));
      for (const tool of ['visit_payroll', 'visit_recon']) assert.equal((await call(role, tool, { month: 'bad', task_id: 'bad' })).error.code, 'FORBIDDEN_TOOL');
    }
    const months = ['2026-07', '2026-08', '2026-09', '2026-10', '2026-11', '2026-12', '2030-01'];
    const cases: { tool: string; args: Record<string, unknown> }[] = [];
    for (const selected of months) for (const view of ['rows', 'adjusts', 'trace']) cases.push({ tool: 'visit_payroll', args: { month: selected, view } });
    cases.push({ tool: 'visit_recon', args: {} });
    for (const selected of months) cases.push({ tool: 'visit_recon', args: { month: selected } });
    for (const id of tasks) for (const view of ['diff', 'interpret']) cases.push({ tool: 'visit_recon', args: { task_id: id, view } });
    for (const selected of months) cases.push({ tool: 'visit_recon', args: { month: selected, view: 'diff' } });
    for (const selected of ['2026-09', '2026-10', '2026-11', '2027-02']) cases.push({ tool: 'visit_recon', args: { month: selected, view: 'interpret' } });
    cases.push({ tool: 'visit_recon', args: { task_id: tasks[2], month: '2026-09', view: 'diff' } });
    cases.push({ tool: 'visit_recon', args: { task_id: tasks[2], month: 'ignored', view: 'interpret' } });
    assert.equal(cases.length, 54);
    const before = await fingerprint();
    const oracle = spawnLegacyPython(['scripts/mcp-finance-read-oracle.py'], { env: { DATABASE_URL: databaseUrl!, NEXT_TEST_DATABASE_URL: databaseUrl! }, input: JSON.stringify(cases), encoding: 'utf8', timeout: 30000, maxBuffer: 8 * 1024 * 1024 });
    assert.equal(oracle.status, 0, oracle.stderr || 'legacy financial oracle failed'); const expected = JSON.parse(oracle.stdout);
    for (let index = 0; index < cases.length; index++) { const value = await call('admin', cases[index].tool, cases[index].args); assert.equal(value.ok, true, JSON.stringify({ case: cases[index], error: value.error })); assert.deepEqual(value.data, expected[index], JSON.stringify(cases[index])); }
    assert.deepEqual(await fingerprint(), before, 'Queries must not register payments, allocate balances, recalculate or generate AI');
    const october = (await call('admin', 'visit_payroll', { month: '2026-10' })).data;
    assert.equal(october.rows.find((row: any) => row.person_code === codes[0]).carry_amount, -500, 'fully recovered current-month withholding remains included');
    assert.equal(october.rows.find((row: any) => row.person_code === codes[1]).carry_amount, -700, 'remaining plus both registered recoveries');
    assert.equal(october.rows.find((row: any) => row.person_code === codes[0]).half1_amount, 600, 'period snapshot is not rebuilt from daily/formal data');
    for (const selected of ['2026-09', '2030-01']) { queries = 0; assert.equal((await call('admin', 'visit_payroll', { month: selected, view: 'trace' })).ok, true); assert.equal(queries, 5, 'trace uses three batch queries plus transaction and actor checks'); }
    await db.query("UPDATE recon_tasks SET summary=jsonb_set(summary::jsonb,'{sys_only}',$1::jsonb) WHERE id=$2", [JSON.stringify([{ code: codes[1], name: 'stored snapshot name', points: 100 }]), tasks[2]]);
    const currentShapeBefore = await fingerprint();
    const currentShape = (await call('admin', 'visit_recon', { task_id: tasks[2], view: 'diff' })).data;
    assert.deepEqual(currentShape.system_only, [{ person_code: codes[1], name: codes[1] + ' name' }], 'new worker structured summary normalizes to the legacy MCP shape');
    assert.deepEqual(await fingerprint(), currentShapeBefore);
    assert.equal((await call('admin', 'visit_recon', { task_id: 2147483647, view: 'diff' })).error.code, 'NOT_FOUND');
  } finally {
    await Promise.all([...clients.values()].map(client => client.close()));
    await db.query('DELETE FROM mcp_audit_log WHERE user_id=ANY($1::int[])', [users]); await db.query('DELETE FROM api_tokens WHERE user_id=ANY($1::int[])', [users]);
    await db.query('DELETE FROM payroll_settlement_links WHERE person_code=ANY($1::text[])', [codes]); await db.query('DELETE FROM payroll_payments WHERE person_code=ANY($1::text[])', [codes]); await db.query('DELETE FROM payroll_adjusts WHERE person_code=ANY($1::text[])', [codes]);
    await db.query('DELETE FROM adjust_records WHERE person_code=ANY($1::text[])', [codes]); await db.query('DELETE FROM payroll_period_rows WHERE person_code=ANY($1::text[])', [codes]); await db.query('DELETE FROM month_perf_records WHERE person_code=ANY($1::text[])', [codes]);
    for (const table of ['recon_results', 'recon_day_rows', 'recon_data_rows']) await db.query(`DELETE FROM ${table} WHERE task_id=ANY($1::int[])`, [tasks]); await db.query('DELETE FROM recon_tasks WHERE id=ANY($1::int[])', [tasks]);
    if (imp) { await db.query('DELETE FROM formal_records WHERE import_id=$1', [imp]); await db.query('DELETE FROM raw_records WHERE import_id=$1', [imp]); await db.query('DELETE FROM imports WHERE id=$1', [imp]); }
    if (configuration) await db.query('DELETE FROM sys_configs WHERE id=$1', [configuration]); await db.query('DELETE FROM users WHERE id=ANY($1::int[])', [users]); await db.query('DELETE FROM persons WHERE code=ANY($1::text[])', [codes]);
    await app.close(); for (const key of keys) { if (environment[key] === undefined) delete process.env[key]; else process.env[key] = environment[key]; }
  }
});
