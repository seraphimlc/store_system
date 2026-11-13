import 'reflect-metadata';
import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash, randomBytes } from 'node:crypto';
import type { AddressInfo } from 'node:net';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { createApp } from '../src/main';
import { Database } from '../src/database';
import { McpManagementReadService } from '../src/mcp-management-read';
import type { McpActor } from '../src/mcp';
import { spawnLegacyPython } from '../../../scripts/legacy-python.mjs';
const actor: McpActor = { uid: 1, tokenId: 1, role: 'staff', scopes: ['read'], personCode: null, username: 'fixture', displayName: '', personName: null };
test('management reads reject unauthorized callers and unrecovered actions before touching business data', async () => {
  const service = new McpManagementReadService(), db = { query: async () => { throw Error('Unexpected business read'); } } as any;
  for (const tool of ['visit_files', 'visit_staff', 'visit_config', 'visit_store']) for (const role of ['staff', 'leader'] as const) await assert.rejects(service.run(db, { ...actor, role }, tool, { action: 'set', view: 'bad' }), { code: 'FORBIDDEN_TOOL' });
  const admin = { ...actor, role: 'admin' as const };
  for (const [tool, args] of [['visit_staff', { action: 'set_status' }], ['visit_config', { action: 'set' }], ['visit_store', { action: 'merge', pair_id: 1, keep: 2 }]] as const) await assert.rejects(service.run(db, admin, tool, args), { code: 'NOT_FOUND' });
  for (const [tool, args] of [['visit_files', { view: 'layout' }], ['visit_files', { view: 'unknown' }], ['visit_staff', { status: 'deleted' }], ['visit_store', { q: 'fixture', limit: 0 }], ['visit_store', { view: 'pairs', kind: 'unknown' }], ['visit_config', { secret: 'value' }]] as const) await assert.rejects(service.run(db, admin, tool, args), { code: 'BAD_PARAM' });
});

const databaseUrl = process.env.NEXT_TEST_DATABASE_URL;
test('schema-only management SDK: legacy files/layout/report/tasks, staff/config/store projections and private-data boundaries', { skip: !databaseUrl }, async () => {
  assert.match(new URL(databaseUrl!).pathname, /^\/store_test_[0-9]{13}_[a-f0-9]{12}_refactor_p1$/);
  const app = await createApp({ databaseUrl: databaseUrl!, sessionSecret: 'x'.repeat(40), cookieSecure: false, host: '127.0.0.1', port: 3000 });
  const db = app.get(Database), clients = new Map<string, Client>(), users: number[] = [], imports: number[] = [], tasks: number[] = [], entities: number[] = [], pairs: number[] = [];
  let configId: number | undefined; const code = 'MCP-MGMT-PERSON';
  const fingerprint = async () => {
    const output: Record<string, string> = {};
    for (const table of ['imports', 'raw_records', 'formal_records', 'appeals', 'recon_tasks', 'sys_configs', 'users', 'store_entities', 'store_pairs']) output[table] = (await db.query(`SELECT md5(coalesce(string_agg(row_to_json(t)::text,'' ORDER BY id),'')) AS value FROM ${table} t`)).rows[0].value;
    return output;
  };
  try {
    await db.query('INSERT INTO persons(code,display_name,created_at) VALUES($1,\'fixture person\',now())', [code]);
    for (const [index, status] of ['active', 'leave', 'disabled', 'resigned'].entries()) users.push((await db.query(`INSERT INTO users(username,password_hash,display_name,role,status,is_active,must_change_password,person_code,lang,created_at)
      VALUES($1,'private-password-hash',$1,$2,$3,$4,false,$5,'zh','2026-10-01 01:02:03.123456') RETURNING id`, ['mcp-mgmt-' + index, index === 0 ? 'admin' : index === 1 ? 'leader' : 'staff', status, index !== 2, code])).rows[0].id);
    const layout = { source: 'fixture', header_row: 4, cols: { store_id: 'A', visible: 'C' }, value_map: { visible: { YES: true, '': false }, deploy: { '01': 'yes', '': 'no', '2': 2 } }, point_rules: [{ visible: ['YES', ''], deploy: null, points: 2 }, 'ignored', { visible: 'NO', deploy: ['X', ''], points: 0 }] };
    for (let index = 0; index < 3; index++) imports.push((await db.query(`INSERT INTO imports(file_name,file_sha256,file_size,stored_path,uploaded_by,uploaded_at,format,header_row,data_start_row,parsed_sheets,ignored_sheets,total_rows,parsed_rows,status,warnings,errors,created_at,layout)
      VALUES($1,$1,0,'/private/fixture/source.xlsx',$2,'2026-10-01 01:02:03.123456','xlsx',2,5,'["main"]','["ignored"]',$3,$3,$4,'["fixture warning"]','[]','2026-10-01 01:02:03.123456',$5) RETURNING id`, ['mgmt-fixture-' + index + '.xlsx', users[0], index === 0 ? 14 : 0, index === 1 ? 'pending' : 'parsed', JSON.stringify(index === 0 ? layout : {})])).rows[0].id);
    const rawIds: number[] = [];
    const statuses = ['valid', 'valid', 'valid', 'valid', 'valid', 'valid', 'valid', 'visible_blank', 'visible_blank', 'master_late', 'from_sub', 'cross_file_dup', 'no_ref', null];
    for (const [index, status] of statuses.entries()) {
      const raw = (await db.query(`INSERT INTO raw_records(import_id,sheet_name,excel_row,store_id_raw,store_name_local_raw,store_name_en_raw,modified_raw,submitter_raw,submitter_code,record_id_raw,original_row,clean_status,filter_reason,created_at)
        VALUES($1,'fixture-sheet',$2,$3,$3,'',$4,'fixture submitter',$5,$3,'{}',$6,'fixture reason',now()) RETURNING id`, [imports[0], index + 5, 'mgmt-store-' + index, index === 13 ? '' : (index === 12 ? '2026-11' : '2026-10') + '-02 03:04:05', code, status])).rows[0].id;
      rawIds.push(raw);
      if (index < 2) await db.query('INSERT INTO formal_records(import_id,raw_record_id,person_code,store_id_raw,japan_date,points,created_at) VALUES($1,$2,$3,$4,$5,1,now())', [imports[0], raw, code, 'mgmt-store-' + index, index === 0 ? '2026-10-02' : '2026-12-02']);
    }
    await db.query("INSERT INTO appeals(raw_record_id,import_id,submitter_code,reason,status,created_at) VALUES($1,$2,$3,'fixture appeal','pending',now())", [rawIds[9], imports[0], code]);
    for (const [index, selected] of ['2026-10', '2026-10', '2026-11'].entries()) tasks.push((await db.query(`INSERT INTO recon_tasks(kind,status,created_by,created_at,finished_at,params,summary)
      VALUES('monthly_v3',$1,$2,'2026-10-01 01:02:03',$3,$4,'{}') RETURNING id`, [index === 2 ? 'pending' : 'done', users[0], index === 2 ? null : '2026-10-02 03:04:05', JSON.stringify({ month: selected, file: 'mgmt-recon-' + index + '.xlsx', version: index + 1 })])).rows[0].id);
    await db.query("UPDATE recon_tasks SET params=params::jsonb||$1::jsonb WHERE id=$2", [JSON.stringify({ replaced_by: tasks[1] }), tasks[0]]);
    configId = (await db.query("INSERT INTO sys_configs(config_month,per_point,bonus_group,bonus_amount,staff_visible_from,updated_at) VALUES('2026-10',301,71,1300,'2026-09',now()) RETURNING id")).rows[0].id;
    for (let index = 0; index < 4; index++) {
      const id = (await db.query("SELECT nextval('store_entities_id_seq')::int AS id")).rows[0].id;
      entities.push(id);
      await db.query(`INSERT INTO store_entities(id,store_id_raw,name_local,name_norm,city,address_local,master_id,master_store_id,created_at,updated_at)
        VALUES($1,$2,$3,$3,$4,$5,$6,$2,now(),now())`, [id, 'MGMT-STORE-' + index, index === 0 ? '渋谷店' : 'fixture store ' + index, index === 1 ? 'Tokyo' : null, index === 2 ? null : 'fixture address', index === 3 ? entities[0] : id]);
    }
    for (const [a, b, kind, status] of [[entities[0], entities[1], 'exact', 'pending'], [entities[1], entities[2], 'fuzzy', 'pending'], [entities[0], entities[3], 'exact', 'pending'], [entities[0], entities[2], 'fuzzy', 'skipped']] as const) pairs.push((await db.query('INSERT INTO store_pairs(entity_a,entity_b,kind,status,created_at) VALUES($1,$2,$3,$4,now()) RETURNING id', [a, b, kind, status])).rows[0].id);
    await app.listen(0, '127.0.0.1'); const url = new URL(`http://127.0.0.1:${(app.getHttpServer().address() as AddressInfo).port}/mcp`);
    for (const [role, uid] of [['admin', users[0]], ['leader', users[1]], ['staff', users[3]]] as const) {
      // The active staff identity is separate from the resigned list fixture.
      let actualUid = uid;
      if (role === 'leader' || role === 'staff') { actualUid = (await db.query(`INSERT INTO users(username,password_hash,display_name,role,status,is_active,must_change_password,lang,created_at)
        VALUES($1,'private-password-hash',$1,$2,'active',true,false,'zh',now()) RETURNING id`, ['mcp-mgmt-client-' + role, role])).rows[0].id; users.push(actualUid); }
      const raw = randomBytes(32).toString('base64url');
      await db.query("INSERT INTO api_tokens(user_id,name,token_prefix,token_hash,scopes,created_at,last_used_at) VALUES($1,'private-token-name',$2,$3,'read',now(),'2026-10-02 03:04:05')", [actualUid, raw.slice(0, 8), createHash('sha256').update(raw).digest('hex')]);
      const client = new Client({ name: 'mcp-mgmt-fixture', version: '1' }); clients.set(role, client); await client.connect(new StreamableHTTPClientTransport(url, { requestInit: { headers: { Authorization: `Bearer ${raw}` } } }));
    }
    const call = async (role: string, tool: string, args: Record<string, unknown> = {}) => { const result = await clients.get(role)!.callTool({ name: tool, arguments: args }); return JSON.parse((result.content as { text: string }[])[0].text); };
    const toolNames = ['visit_files', 'visit_staff', 'visit_config', 'visit_store'];
    for (const role of ['staff', 'leader']) { assert.ok(!(await clients.get(role)!.listTools()).tools.some(tool => toolNames.includes(tool.name))); for (const tool of toolNames) assert.equal((await call(role, tool, { view: 'bad', action: 'set' })).error.code, 'FORBIDDEN_TOOL'); }
    const cases: { tool: string; args: Record<string, unknown> }[] = [];
    for (const selected of [null, '', '2026-10', '2026-11', '2026-12', '2030-01']) cases.push({ tool: 'visit_files', args: selected === null ? {} : { month: selected } });
    for (const id of imports) for (const view of ['layout', 'report']) cases.push({ tool: 'visit_files', args: { view, import_id: id } });
    for (const kind of [null, 'daily_records', 'recon']) for (const selected of [null, '2026-10', '2030-01']) for (const status of [null, 'pending', 'done']) cases.push({ tool: 'visit_files', args: { view: 'tasks', kind, month: selected, status } });
    for (const status of [null, 'active', 'leave', 'disabled', 'resigned']) cases.push({ tool: 'visit_staff', args: { status } });
    cases.push({ tool: 'visit_config', args: {} });
    for (const q of ['', '  ', 'fixture', 'MGMT', 'Tokyo', '渋谷', 'missing', '%']) for (const limit of [1, 101]) cases.push({ tool: 'visit_store', args: { q, limit } });
    for (const kind of [null, 'exact', 'fuzzy']) cases.push({ tool: 'visit_store', args: { view: 'pairs', kind } });
    assert.equal(cases.length, 64);
    const before = await fingerprint();
    // Feed identical last-used state to both implementations; reset after each call because MCP usage bookkeeping is expected.
    const initialUsed = (await db.query("SELECT id,to_char(last_used_at,'YYYY-MM-DD HH24:MI:SS.US') AS last_used_at FROM api_tokens WHERE user_id=ANY($1::int[])", [users])).rows;
    const oracle = spawnLegacyPython(['scripts/mcp-management-read-oracle.py'], { env: { DATABASE_URL: databaseUrl!, NEXT_TEST_DATABASE_URL: databaseUrl! }, input: JSON.stringify(cases), encoding: 'utf8', timeout: 30000, maxBuffer: 8 * 1024 * 1024 });
    assert.equal(oracle.status, 0, oracle.stderr || 'legacy management oracle failed'); const expected = JSON.parse(oracle.stdout);
    for (let index = 0; index < cases.length; index++) {
      for (const row of initialUsed) await db.query('UPDATE api_tokens SET last_used_at=$1 WHERE id=$2', [row.last_used_at, row.id]);
      const response = await call('admin', cases[index].tool, cases[index].args); assert.equal(response.ok, true, JSON.stringify({ case: cases[index], error: response.error }));
      assert.deepEqual(response.data, expected[index], JSON.stringify(cases[index])); assert.ok(!JSON.stringify(response.data).includes('private-password-hash')); assert.ok(!JSON.stringify(response.data).includes('/private/fixture/source.xlsx'));
    }
    assert.deepEqual(await fingerprint(), before);
    await db.query("UPDATE raw_records SET clean_status='blank' WHERE import_id=$1 AND excel_row=13", [imports[0]]);
    const normalized = (await call('admin', 'visit_files')).data.files.find((file: any) => file.file_id === imports[0]);
    assert.equal(normalized.judge_counts.blank, 2, 'Both legacy blank statuses must add rather than overwrite');
    for (const [tool, action] of [['visit_staff', 'set_status'], ['visit_config', 'set'], ['visit_store', 'merge']]) assert.equal((await call('admin', tool, { action })).error.code, 'FORBIDDEN_TOOL');
    assert.equal((await call('admin', 'visit_files', { view: 'layout', import_id: 2147483647 })).error.code, 'NOT_FOUND');
    const deleted = (await db.query("INSERT INTO users(username,password_hash,display_name,role,status,is_active,must_change_password,lang,created_at) VALUES('mcp-mgmt-deleted','private-password-hash','deleted fixture','staff','deleted',false,false,'zh',now()) RETURNING id")).rows[0].id; users.push(deleted);
    assert.ok(!(await call('admin', 'visit_staff')).data.rows.some((row: any) => row.id === deleted));
    await db.query('UPDATE imports SET warnings=$1,errors=$2 WHERE id=$3', [JSON.stringify(['cannot read /data/uploads/private.xlsx secret=private-value']), JSON.stringify(['Bearer privateTokenValue']), imports[1]]);
    const redacted = (await call('admin', 'visit_files', { view: 'layout', import_id: imports[1] })).data;
    assert.ok(!JSON.stringify(redacted).includes('/data/uploads/private.xlsx')); assert.ok(!JSON.stringify(redacted).includes('private-value')); assert.ok(!JSON.stringify(redacted).includes('privateTokenValue'));
  } finally {
    await Promise.all([...clients.values()].map(client => client.close()));
    await db.query('DELETE FROM mcp_audit_log WHERE user_id=ANY($1::int[])', [users]); await db.query('DELETE FROM api_tokens WHERE user_id=ANY($1::int[])', [users]);
    await db.query('DELETE FROM store_pairs WHERE id=ANY($1::int[])', [pairs]); await db.query('UPDATE store_entities SET master_id=id WHERE id=ANY($1::int[])', [entities]); await db.query('DELETE FROM store_entities WHERE id=ANY($1::int[])', [entities]);
    await db.query('DELETE FROM appeals WHERE import_id=ANY($1::int[])', [imports]); await db.query('DELETE FROM formal_records WHERE import_id=ANY($1::int[])', [imports]); await db.query('DELETE FROM raw_records WHERE import_id=ANY($1::int[])', [imports]); await db.query('DELETE FROM imports WHERE id=ANY($1::int[])', [imports]);
    await db.query('DELETE FROM recon_tasks WHERE id=ANY($1::int[])', [tasks]); if (configId) await db.query('DELETE FROM sys_configs WHERE id=$1', [configId]);
    await db.query('DELETE FROM users WHERE id=ANY($1::int[])', [users]); await db.query('DELETE FROM persons WHERE code=$1', [code]); await app.close();
  }
});
