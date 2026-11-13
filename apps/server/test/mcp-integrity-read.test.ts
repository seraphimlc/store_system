import 'reflect-metadata';
import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash, randomBytes } from 'node:crypto';
import type { AddressInfo } from 'node:net';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { createApp } from '../src/main';
import { Database } from '../src/database';
import { McpIntegrityReadService } from '../src/mcp-integrity-read';
import type { McpActor } from '../src/mcp';
import { spawnLegacyPython } from '../../../scripts/legacy-python.mjs';
const actor: McpActor = { uid: 1, tokenId: 1, role: 'admin', scopes: ['read'], personCode: null, username: 'fixture', displayName: '', personName: null };
test('integrity rejects non-admin before parameters and does not accept repair instructions', async () => {
  const service = new McpIntegrityReadService(), db = { query: async () => { throw Error('No business query expected'); } } as any;
  for (const role of ['staff', 'leader'] as const) await assert.rejects(service.run(db, { ...actor, role }, 'visit_verify', { repair: true }), { code: 'FORBIDDEN_TOOL' });
  await assert.rejects(service.run(db, actor, 'visit_verify', { repair: true }), { code: 'BAD_PARAM' });
});
const url = process.env.NEXT_TEST_DATABASE_URL;
test('schema-only verify SDK: nine legacy checks, pass/info/fail, five-sample cap and no repair', { skip: !url }, async () => {
  assert.match(new URL(url!).pathname, /^\/store_test_[0-9]{13}_[a-f0-9]{12}_refactor_p1$/);
  const app = await createApp({ databaseUrl: url!, sessionSecret: 'x'.repeat(40), cookieSecure: false, host: '127.0.0.1', port: 3000 });
  const db = app.get(Database); let uid: number | undefined, client: Client | undefined;
  const code = 'MCP-INTEGRITY';
  const fingerprint = async () => { const result: Record<string, string> = {}; for (const table of ['payroll_adjusts', 'payroll_payments', 'payroll_settlement_links', 'payroll_period_rows']) result[table] = (await db.query(`SELECT md5(coalesce(string_agg(row_to_json(t)::text,'' ORDER BY id),'')) AS hash FROM ${table} t`)).rows[0].hash; return result; };
  try {
    uid = (await db.query("INSERT INTO users(username,password_hash,display_name,role,status,is_active,must_change_password,lang,created_at) VALUES('mcp-integrity','fixture-only','fixture','admin','active',true,false,'zh',now()) RETURNING id")).rows[0].id;
    const raw = randomBytes(32).toString('base64url'); await db.query("INSERT INTO api_tokens(user_id,name,token_prefix,token_hash,scopes,created_at) VALUES($1,'fixture',$2,$3,'read',now())", [uid, raw.slice(0, 8), createHash('sha256').update(raw).digest('hex')]);
    await db.query("INSERT INTO persons(code,display_name,created_at) VALUES($1,'fixture',now())", [code]);
    await app.listen(0, '127.0.0.1'); client = new Client({ name: 'integrity-fixture', version: '1' });
    await client.connect(new StreamableHTTPClientTransport(new URL(`http://127.0.0.1:${(app.getHttpServer().address() as AddressInfo).port}/mcp`), { requestInit: { headers: { Authorization: `Bearer ${raw}` } } }));
    const verify = async () => {
      const before = await fingerprint();
      const oracle = spawnLegacyPython(['scripts/mcp-management-read-oracle.py'], { env: { DATABASE_URL: url!, NEXT_TEST_DATABASE_URL: url! }, input: JSON.stringify([{ tool: 'visit_verify' }]), encoding: 'utf8', timeout: 30000 });
      assert.equal(oracle.status, 0, oracle.stderr); const result = await client!.callTool({ name: 'visit_verify', arguments: {} }); const value = JSON.parse((result.content as { text: string }[])[0].text);
      assert.equal(value.ok, true, 'check failure is data, not tool transport failure'); assert.deepEqual(value.data, JSON.parse(oracle.stdout)[0]); assert.deepEqual(await fingerprint(), before); return value.data;
    };
    let result = await verify(); assert.equal(result.ok, true); assert.equal(result.checks.length, 9); assert.equal(result.summary.passed, 9);
    const period = (await db.query("INSERT INTO payroll_period_rows(month,person_code,diff_amount,adjust_amount,prev_adjust_amount,half1_amount,half2_amount,updated_at) VALUES('2026-10',$1,-500,-500,-500,1000,2000,now()) RETURNING id", [code])).rows[0].id;
    const adjust = (await db.query("INSERT INTO payroll_adjusts(source_month,person_code,adjust_amount,settled_amount,remaining,status,updated_at) VALUES('2026-10',$1,-500,-200,-300,'in_progress',now()) RETURNING id", [code])).rows[0].id;
    const pay = (await db.query("INSERT INTO payroll_payments(month,person_code,seq,amount,adjust_applied,paid_at,adjust_source_row_id,adjust_source_month) VALUES('2026-10',$1,1,1000,-200,now(),$2,'2026-10') RETURNING id", [code, period])).rows[0].id;
    await db.query("INSERT INTO payroll_settlement_links(adjust_id,payment_id,month,person_code,amount,created_at) VALUES($1,$2,'2026-10',$3,-200,now())", [adjust, pay, code]);
    result = await verify(); assert.equal(result.summary.passed, 9);
    await db.query('UPDATE payroll_payments SET amount=800 WHERE id=$1', [pay]); result = await verify(); assert.equal(result.ok, true); assert.equal(result.summary.info, 1); assert.equal(result.summary.failed, 0);
    await db.query("UPDATE payroll_adjusts SET adjust_amount=-600,remaining=0,status='in_progress' WHERE id=$1", [adjust]);
    await db.query("UPDATE payroll_payments SET adjust_applied=-100,adjust_source_row_id=2147483647,adjust_source_month='2030-01',adjust_source_task_id=2147483647 WHERE id=$1", [pay]);
    await db.query('UPDATE payroll_period_rows SET prev_adjust_amount=100,adjust_amount=0 WHERE id=$1', [period]);
    await db.query('UPDATE payroll_settlement_links SET amount=-150 WHERE payment_id=$1', [pay]);
    for (let index = 0; index < 7; index++) await db.query('INSERT INTO payroll_adjusts(source_month,person_code,adjust_amount,settled_amount,remaining,status,updated_at) VALUES($1,$2,1,0,1,\'in_progress\',now())', ['2030-' + String(index + 1).padStart(2, '0'), code]);
    result = await verify(); assert.equal(result.ok, false); assert.equal(result.summary.failed, 8); assert.equal(result.summary.info, 1);
    const a1 = result.checks.find((row: any) => row.id === 'A1'); assert.equal(a1.count, 8); assert.equal(a1.samples.length, 5);
  } finally {
    await client?.close(); await db.query('DELETE FROM payroll_settlement_links WHERE person_code=$1', [code]); await db.query('DELETE FROM payroll_payments WHERE person_code=$1', [code]); await db.query('DELETE FROM payroll_adjusts WHERE person_code=$1', [code]); await db.query('DELETE FROM payroll_period_rows WHERE person_code=$1', [code]); await db.query('DELETE FROM persons WHERE code=$1', [code]);
    if (uid) { await db.query('DELETE FROM mcp_audit_log WHERE user_id=$1', [uid]); await db.query('DELETE FROM api_tokens WHERE user_id=$1', [uid]); await db.query('DELETE FROM users WHERE id=$1', [uid]); } await app.close();
  }
});
