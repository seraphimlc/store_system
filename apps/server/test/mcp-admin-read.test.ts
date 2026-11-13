import 'reflect-metadata';
import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash, randomBytes } from 'node:crypto';
import type { AddressInfo } from 'node:net';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { createApp } from '../src/main';
import { Database } from '../src/database';
import { McpReadService } from '../src/mcp-read';
import type { McpActor } from '../src/mcp';
import { spawnLegacyPython } from '../../../scripts/legacy-python.mjs';

const actor: McpActor = { uid: 1, tokenId: 1, role: 'staff', scopes: ['read'], personCode: null, username: 'fixture', displayName: '', personName: null };
test('admin MCP reads check role before parameters, then reject malformed month/view/limit without querying', async () => {
  const reads = new McpReadService(), db = { query: async () => { throw Error('Unexpected business query'); } } as any;
  for (const role of ['staff', 'leader'] as const) for (const tool of ['visit_overview', 'visit_person']) {
    await assert.rejects(reads.run(db, { ...actor, role }, tool, { name: 'private fixture', month: 'bad', view: 'bad' }), { code: 'FORBIDDEN_TOOL' });
  }
  const admin = { ...actor, role: 'admin' as const };
  await assert.rejects(reads.run(db, admin, 'visit_overview', { month: '2026-13' }), { code: 'BAD_MONTH' });
  await assert.rejects(reads.run(db, admin, 'visit_overview', { view: 'unknown' }), { code: 'BAD_PARAM' });
  await assert.rejects(reads.run(db, admin, 'visit_overview', { view: 'ranking', month: '2026-10', limit: 0 }), { code: 'BAD_PARAM' });
  await assert.rejects(reads.run(db, admin, 'visit_person', { person: '', month: 'bad' }), { code: 'BAD_PARAM' });
  await assert.rejects(reads.run(db, admin, 'visit_person', { person: 'fixture', month: 'bad' }), { code: 'BAD_MONTH' });
});

const databaseUrl = process.env.NEXT_TEST_DATABASE_URL;
test('schema-only PostgreSQL: admin summary/months/ranking and person-by-code/name match legacy read projections', { skip: !databaseUrl }, async () => {
  assert.match(new URL(databaseUrl!).pathname, /^\/store_test_[0-9]{13}_[a-f0-9]{12}_refactor_p1$/);
  const app = await createApp({ databaseUrl: databaseUrl!, sessionSecret: 'x'.repeat(40), cookieSecure: false, host: '127.0.0.1', port: 3000 });
  const db = app.get(Database), users: number[] = [], tasks: number[] = [], clients = new Map<string, Client>();
  const codes = ['MCP-ADMIN-A', 'MCP-ADMIN-B', 'MCP-ADMIN-EMPTY']; let imp: number | undefined;
  const fingerprint = async () => {
    const values: Record<string, string> = {};
    for (const table of ['formal_records', 'month_perf_records', 'person_daily_stats', 'recon_tasks', 'recon_data_rows']) values[table] = (await db.query(`SELECT md5(coalesce(string_agg(row_to_json(t)::text,'' ORDER BY id),'')) AS value FROM ${table} t`)).rows[0].value;
    return values;
  };
  try {
    for (const code of codes) await db.query('INSERT INTO persons(code,display_name,created_at) VALUES($1,$2,now())', [code, code + ' name']);
    for (const role of ['admin', 'leader', 'staff']) users.push((await db.query(`INSERT INTO users(username,password_hash,display_name,role,status,is_active,must_change_password,lang,created_at)
      VALUES($1,'fixture-only-hash',$1,$2,'active',true,false,'zh',now()) RETURNING id`, ['mcp-admin-read-' + role, role])).rows[0].id);
    await db.query(`INSERT INTO month_perf_records(month,person_code,records,p1,p2,points,salary,per_point,rate37,pass37,settle_points,settle_amount,diff_points,diff_amount,created_at) VALUES
      ('2026-10',$1,8,5,3,11,12345,271,0.37,true,10,12000,1,345,now()),
      ('2026-10',$2,9,0,9,18,31000,600,0.75,false,17,30000,1,1000,now()),
      ('2026-11',$1,1,1,0,1,0,500,0,false,0,0,1,0,now())`, codes.slice(0, 2));
    await db.query(`INSERT INTO person_daily_stats(person_code,ref_date,records,p1,p2,points) VALUES
      ($1,'2026-10-02',3,1,2,5),($1,'2026-12-01',2,0,2,4),($2,'2026-10-02',9,0,9,18)`, codes.slice(0, 2));
    imp = (await db.query(`INSERT INTO imports(file_name,file_sha256,file_size,stored_path,uploaded_by,uploaded_at,parsed_sheets,ignored_sheets,total_rows,parsed_rows,status,warnings,errors,created_at)
      VALUES('mcp-admin-fixture','mcp-admin-fixture',0,'fixture',$1,now(),'[]','[]',0,0,'parsed','[]','[]',now()) RETURNING id`, [users[0]])).rows[0].id;
    // Synthetic source rows are created only in the disposable database; no original upload or real raw_records is read.
    for (const [index, code, day, points] of [[0, codes[0], '2026-10-02', 1], [1, codes[1], '2026-10-03', 2], [2, codes[0], '2026-10-04', 7], [3, codes[0], '2027-01-01', 1]] as const) {
      const raw = (await db.query(`INSERT INTO raw_records(import_id,sheet_name,excel_row,store_id_raw,store_name_local_raw,store_name_en_raw,modified_raw,submitter_raw,submitter_code,record_id_raw,original_row,created_at)
        VALUES($1,'fixture',$2,$3,$3,'',$4,'',$5,$3,'{}',now()) RETURNING id`, [imp, index + 1, 'mcp-admin-' + index, day, code])).rows[0].id;
      await db.query('INSERT INTO formal_records(import_id,raw_record_id,person_code,store_id_raw,japan_date,points,created_at) VALUES($1,$2,$3,$4,$5,$6,now())', [imp, raw, code, 'mcp-admin-' + index, day, points]);
    }
    for (const [month, replaced] of [['2026-09', 99], ['2026-10', null], ['2026-10', null], ['2026-11', 0], ['2027-02', false]] as const) {
      const task = (await db.query("INSERT INTO recon_tasks(kind,status,created_by,created_at,params,summary) VALUES('monthly_v3','pending',$1,now(),$2,'{}') RETURNING id", [users[0], JSON.stringify({ month, replaced_by: replaced })])).rows[0].id;
      tasks.push(task);
      if (tasks.length === 1 || tasks.length === 2 || month === '2026-11') await db.query('INSERT INTO recon_data_rows(task_id,ref_date,person_code,points,cnt,created_at) VALUES($1,$2,$3,0,1,now())', [task, month + '-01', codes[0]]);
    }
    await app.listen(0, '127.0.0.1');
    const url = new URL(`http://127.0.0.1:${(app.getHttpServer().address() as AddressInfo).port}/mcp`);
    for (let index = 0; index < users.length; index++) {
      const role = ['admin', 'leader', 'staff'][index], raw = randomBytes(32).toString('base64url');
      await db.query("INSERT INTO api_tokens(user_id,name,token_prefix,token_hash,scopes,created_at) VALUES($1,'read fixture',$2,$3,'read',now())", [users[index], raw.slice(0, 8), createHash('sha256').update(raw).digest('hex')]);
      const client = new Client({ name: 'mcp-admin-fixture', version: '1' }); clients.set(role, client);
      await client.connect(new StreamableHTTPClientTransport(url, { requestInit: { headers: { Authorization: `Bearer ${raw}` } } }));
    }
    const read = async (client: Client, tool: string, args: Record<string, unknown>) => {
      const result = await client.callTool({ name: tool, arguments: args }); return JSON.parse((result.content as { text: string }[])[0].text);
    };
    for (const role of ['staff', 'leader']) {
      const client = clients.get(role)!;
      assert.deepEqual((await client.listTools()).tools.filter(tool => ['visit_whoami','visit_my_perf','visit_my_pay','visit_overview','visit_person'].includes(tool.name)).map(tool => tool.name), ['visit_whoami', 'visit_my_perf', 'visit_my_pay']);
      for (const tool of ['visit_overview', 'visit_person']) assert.equal((await read(client, tool, { name: 'private fixture', month: 'bad' })).error.code, 'FORBIDDEN_TOOL');
    }
    assert.equal((await clients.get('admin')!.listTools()).tools.filter(tool => ['visit_whoami','visit_my_perf','visit_my_pay','visit_overview','visit_person'].includes(tool.name)).length, 5);
    const cases: Record<string, unknown>[] = [{ tool: 'visit_overview', view: 'months' }];
    for (const month of ['2026-09', '2026-10', '2026-11', '2026-12', '2027-01', '2027-02', '2030-01']) {
      cases.push({ tool: 'visit_overview', month });
      for (const limit of [1, 10, 101]) cases.push({ tool: 'visit_overview', view: 'ranking', month, limit });
    }
    for (const code of codes) for (const key of ['person', 'name']) {
      cases.push({ tool: 'visit_person', [key]: key === 'person' ? code : code + ' name' });
      for (const month of ['2026-09', '2026-10', '2026-11', '2026-12', '2030-01']) cases.push({ tool: 'visit_person', [key]: key === 'person' ? code : code + ' name', month });
    }
    const before = await fingerprint();
    const oracle = spawnLegacyPython(['scripts/mcp-read-oracle.py'], { env: { DATABASE_URL: databaseUrl!, NEXT_TEST_DATABASE_URL: databaseUrl!, STAFF_VISIBLE_FROM: '2026-10' }, input: JSON.stringify(cases), encoding: 'utf8', timeout: 30000, maxBuffer: 4 * 1024 * 1024 });
    assert.equal(oracle.status, 0, oracle.stderr || 'legacy admin oracle failed');
    const expected = JSON.parse(oracle.stdout);
    for (let index = 0; index < cases.length; index++) {
      const { tool, ...args } = cases[index]; const value = await read(clients.get('admin')!, tool as string, args);
      assert.equal(value.ok, true, JSON.stringify({ case: cases[index], error: value.error }));
      assert.deepEqual(value.data, expected[index], JSON.stringify(cases[index]));
    }
    assert.equal(cases.length, 65);
    assert.equal((await read(clients.get('admin')!, 'visit_person', { person: 'MISSING', month: '2026-10' })).error.code, 'NOT_FOUND');
    assert.deepEqual(await fingerprint(), before);
  } finally {
    await Promise.all([...clients.values()].map(client => client.close()));
    await db.query('DELETE FROM recon_data_rows WHERE task_id=ANY($1::int[])', [tasks]);
    await db.query('DELETE FROM recon_tasks WHERE id=ANY($1::int[])', [tasks]);
    if (imp) { await db.query('DELETE FROM formal_records WHERE import_id=$1', [imp]); await db.query('DELETE FROM raw_records WHERE import_id=$1', [imp]); await db.query('DELETE FROM imports WHERE id=$1', [imp]); }
    for (const table of ['person_daily_stats', 'month_perf_records']) await db.query(`DELETE FROM ${table} WHERE person_code=ANY($1::text[])`, [codes]);
    await db.query('DELETE FROM mcp_audit_log WHERE user_id=ANY($1::int[])', [users]); await db.query('DELETE FROM api_tokens WHERE user_id=ANY($1::int[])', [users]);
    await db.query('DELETE FROM users WHERE id=ANY($1::int[])', [users]); await db.query('DELETE FROM persons WHERE code=ANY($1::text[])', [codes]);
    await app.close();
  }
});
