import 'reflect-metadata';
import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash, randomBytes } from 'node:crypto';
import type { AddressInfo } from 'node:net';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { createApp } from '../src/main';
import { Database } from '../src/database';
import type { McpActor } from '../src/mcp';
import { McpReadService } from '../src/mcp-read';
import { spawnLegacyPython } from '../../../scripts/legacy-python.mjs';

const actor: McpActor = { uid: 1, tokenId: 1, role: 'staff', scopes: ['read'], personCode: 'MCP-READ-A', username: 'fixture', displayName: 'fixture', personName: 'fixture' };
test('personal MCP reads reject foreign identity before malformed parameters and require binding', async () => {
  const reads = new McpReadService();
  const db = { query: async () => { throw Error('Validation must precede business queries'); } } as any;
  for (const tool of ['visit_my_perf', 'visit_my_pay']) {
    for (const key of ['person', 'person_code', 'person_id']) {
      await assert.rejects(reads.run(db, actor, tool, { [key]: 'OTHER', month: 'bad' }), { code: 'FORBIDDEN_TOOL' });
    }
    await assert.rejects(reads.run(db, actor, tool, { month: '2026-13' }), { code: 'BAD_MONTH' });
    await assert.rejects(reads.run(db, actor, tool, { month: '' }), { code: 'BAD_MONTH' });
    await assert.rejects(reads.run(db, actor, tool, { secret: 'private fixture value' }), { code: 'BAD_PARAM' });
    await assert.rejects(reads.run(db, { ...actor, personCode: null }, tool, {}), { code: 'BAD_PARAM' });
  }
  await assert.rejects(reads.run(db, actor, 'visit_my_perf', { view: 'unknown' }), { code: 'BAD_PARAM' });
});

const databaseUrl = process.env.NEXT_TEST_DATABASE_URL;
test('schema-only PostgreSQL and legacy Python: personal performance/pay outputs, hidden months, null wages and cross-month trace', { skip: !databaseUrl }, async () => {
  assert.match(new URL(databaseUrl!).pathname, /^\/store_test_[0-9]{13}_[a-f0-9]{12}_refactor_p1$/);
  const app = await createApp({ databaseUrl: databaseUrl!, sessionSecret: 'x'.repeat(40), cookieSecure: false, host: '127.0.0.1', port: 3000 });
  const db = app.get(Database), clients = new Map<string, Client>();
  const codes = ['MCP-READ-A', 'MCP-READ-B', 'MCP-READ-HIDDEN', 'MCP-READ-DAILY', 'MCP-READ-EMPTY'];
  const userIds: number[] = [];
  let configId: number | undefined;
  const businessTables = ['month_perf_records', 'person_daily_stats', 'payroll_adjusts', 'payroll_payments', 'payroll_settlement_links'];
  const fingerprint = async () => {
    const result: Record<string, string> = {};
    for (const table of businessTables) result[table] = (await db.query(`SELECT md5(coalesce(string_agg(row_to_json(t)::text,'' ORDER BY id),'')) AS value FROM ${table} t`)).rows[0].value;
    return result;
  };
  try {
    configId = (await db.query("INSERT INTO sys_configs(config_month,staff_visible_from,updated_at) VALUES('2026-10','2026-10',now()) RETURNING id")).rows[0].id;
    for (const code of codes) {
      await db.query('INSERT INTO persons(code,display_name,created_at) VALUES($1,$2,now())', [code, code + ' name']);
      const user = (await db.query(`INSERT INTO users(username,password_hash,display_name,role,is_active,status,must_change_password,person_code,lang,created_at)
        VALUES($1,'fixture-only-hash',$1,'staff',true,'active',false,$2,'zh',now()) RETURNING id`, [code.toLowerCase(), code])).rows[0];
      userIds.push(user.id);
    }
    await db.query(`INSERT INTO month_perf_records(month,person_code,records,p1,p2,points,salary,per_point,rate37,pass37,settle_points,settle_amount,diff_points,diff_amount,created_at) VALUES
      ('2026-10',$1,8,5,3,11,12345,271,0.37,true,10,12300,1,45,now()),
      ('2026-11',$1,2,2,0,2,0,500,0,false,0,0,2,0,now()),
      ('2026-10',$2,99,99,0,99,99999,999,0.5,false,0,0,0,0,now()),
      ('2026-09',$3,1,1,0,1,250,250,0,false,0,0,0,0,now())`, codes.slice(0, 3));
    await db.query(`INSERT INTO person_daily_stats(person_code,ref_date,records,p1,p2,points) VALUES
      ($1,'2026-10-02',3,1,2,5),($1,'2026-10-15',1,1,0,1),($1,'2026-12-01',4,2,2,6),
      ($2,'2026-10-02',99,99,0,99),($3,'2026-09-01',1,1,0,1),($4,'2026-10-01',2,0,2,4)`, codes.slice(0, 4));
    const a = (await db.query(`INSERT INTO payroll_adjusts(source_month,person_code,adjust_amount,settled_amount,remaining,status,settled_at,updated_at) VALUES
      ('2026-09',$1,-500,-200,-300,'in_progress',NULL,now()),
      ('2026-10',$1,100,100,0,'settled','2026-11-15 03:04:05.123456',now()),
      ('2026-11',$1,-50,0,-50,'in_progress',NULL,now()),
      ('2026-10',$2,-9999,0,-9999,'in_progress',NULL,now()) RETURNING id`, [codes[0], codes[1]])).rows;
    const p = (await db.query(`INSERT INTO payroll_payments(month,person_code,seq,points,amount,bonus,adjust_applied,adjust_leftover,paid_at) VALUES
      ('2026-10',$1,1,5,1000,300,-200,-300,'2026-10-15 03:04:05.123456'),
      ('2026-10',$1,2,6,2000,0,-50,NULL,'2026-10-31 03:04:05'),
      ('2026-11',$1,1,2,500,0,100,0,'2026-11-15 03:04:05'),
      ('2026-10',$2,1,99,99999,0,0,0,'2026-10-15 03:04:05') RETURNING id`, [codes[0], codes[1]])).rows;
    await db.query(`INSERT INTO payroll_settlement_links(adjust_id,payment_id,month,person_code,amount,created_at) VALUES
      ($1,$4,'2026-10',$7,-200,now()),($2,$6,'2026-11',$7,100,now()),($3,$5,'2026-10',$7,-50,now())`, [a[0].id, a[1].id, a[2].id, p[0].id, p[1].id, p[2].id, codes[0]]);
    await app.listen(0, '127.0.0.1');
    const url = new URL(`http://127.0.0.1:${(app.getHttpServer().address() as AddressInfo).port}/mcp`);
    for (let index = 0; index < codes.length; index++) {
      const raw = randomBytes(32).toString('base64url');
      await db.query(`INSERT INTO api_tokens(user_id,name,token_prefix,token_hash,scopes,created_at) VALUES($1,'readonly fixture',$2,$3,'read',now())`, [userIds[index], raw.slice(0, 8), createHash('sha256').update(raw).digest('hex')]);
      const client = new Client({ name: 'mcp-read-fixture', version: '1' }); clients.set(codes[index], client);
      await client.connect(new StreamableHTTPClientTransport(url, { requestInit: { headers: { Authorization: `Bearer ${raw}` } } }));
    }
    const cases: { tool: string; personCode: string; view?: string; month?: string }[] = [];
    for (const code of codes) {
      for (const view of ['month', 'daily']) {
        cases.push({ tool: 'visit_my_perf', personCode: code, view });
        for (const month of ['2026-09', '2026-10', '2026-11', '2026-12', '2030-01']) cases.push({ tool: 'visit_my_perf', personCode: code, view, month });
      }
      cases.push({ tool: 'visit_my_pay', personCode: code });
      for (const month of ['2026-09', '2026-10', '2026-11', '2030-01']) cases.push({ tool: 'visit_my_pay', personCode: code, month });
    }
    const before = await fingerprint();
    const oracle = spawnLegacyPython(['scripts/mcp-read-oracle.py'], {
      env: { DATABASE_URL: databaseUrl!, NEXT_TEST_DATABASE_URL: databaseUrl!, STAFF_VISIBLE_FROM: '2026-10' },
      input: JSON.stringify(cases), encoding: 'utf8', timeout: 30000, maxBuffer: 4 * 1024 * 1024,
    });
    assert.equal(oracle.status, 0, oracle.stderr || 'legacy oracle failed');
    const expected = JSON.parse(oracle.stdout);
    for (let index = 0; index < cases.length; index++) {
      const { personCode, tool, ...args } = cases[index];
      const response = await clients.get(personCode)!.callTool({ name: tool, arguments: args });
      const value = JSON.parse((response.content as { text: string }[])[0].text);
      assert.equal(value.ok, true, JSON.stringify({ case: cases[index], error: value.error }));
      assert.deepEqual(value.data, expected[index], JSON.stringify(cases[index]));
    }
    assert.equal(cases.length, 85);
    const forbidden = await clients.get(codes[0])!.callTool({ name: 'visit_my_pay', arguments: { person: codes[1], month: 'bad' } });
    assert.equal(JSON.parse((forbidden.content as { text: string }[])[0].text).error.code, 'FORBIDDEN_TOOL');
    const same = await clients.get(codes[0])!.callTool({ name: 'visit_my_perf', arguments: { person_code: codes[0], month: '2026-10' } });
    assert.equal(JSON.parse((same.content as { text: string }[])[0].text).data.summary.salary, 12345);
    assert.deepEqual(await fingerprint(), before, 'MCP reads must not change financial/statistics tables');
    const badAudits = (await db.query("SELECT count(*)::int AS count FROM mcp_audit_log WHERE user_id=ANY($1::int[]) AND (params_json<>'{}' OR ok IS NULL)", [userIds])).rows[0].count;
    assert.equal(badAudits, 0);
  } finally {
    await Promise.all([...clients.values()].map(client => client.close()));
    if (userIds.length) {
      await db.query('DELETE FROM mcp_audit_log WHERE user_id=ANY($1::int[])', [userIds]);
      await db.query('DELETE FROM api_tokens WHERE user_id=ANY($1::int[])', [userIds]);
      for (const table of ['payroll_settlement_links', 'payroll_payments', 'payroll_adjusts', 'person_daily_stats', 'month_perf_records']) await db.query(`DELETE FROM ${table} WHERE person_code=ANY($1::text[])`, [codes]);
      await db.query('DELETE FROM users WHERE id=ANY($1::int[])', [userIds]);
    }
    await db.query('DELETE FROM persons WHERE code=ANY($1::text[])', [codes]);
    if (configId) await db.query('DELETE FROM sys_configs WHERE id=$1', [configId]);
    await app.close();
  }
});
