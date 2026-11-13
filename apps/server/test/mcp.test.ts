import 'reflect-metadata';
import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash, randomBytes } from 'node:crypto';
import type { AddressInfo } from 'node:net';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { createApp } from '../src/main';
import { Database } from '../src/database';
import { loadConfig } from '../src/config';
import { McpActor, MCP_LEADER_TOOLS, MCP_STAFF_TOOLS, mcpRoleAllows } from '../src/mcp';

const digest = (raw: string) => createHash('sha256').update(raw).digest('hex');
async function connect(url: string, raw: string) {
  const client = new Client({ name: 'mcp-fixture', version: '1.0.0' });
  try { await client.connect(new StreamableHTTPClientTransport(new URL(url), { requestInit: { headers: { Authorization: `Bearer ${raw}` } } })); }
  catch (error) { await client.close(); throw error; }
  return client;
}
async function call(client: Client, name = 'visit_whoami', args: Record<string, unknown> = {}) {
  const result = await client.callTool({ name, arguments: args });
  assert.ok(Array.isArray(result.content));
  return { result, value: JSON.parse((result.content[0] as { text: string }).text) };
}

test('MCP legacy roles deny admin tools to employees and retain the 6/10 tool boundary', () => {
  assert.equal(MCP_STAFF_TOOLS.size, 6); assert.equal(MCP_LEADER_TOOLS.size, 10);
  for (const tool of MCP_STAFF_TOOLS) assert.equal(mcpRoleAllows('staff', tool), true);
  assert.equal(mcpRoleAllows('staff', 'visit_person'), false);
  assert.equal(mcpRoleAllows('leader', 'visit_task_assign'), true);
  assert.equal(mcpRoleAllows('leader', 'visit_task_return'), false);
  assert.equal(mcpRoleAllows('staff', 'unregistered_tool'), false);
});

test('MCP public resource URL rejects credentials, wrong paths and insecure production configuration', () => {
  const env = { NEXT_DATABASE_URL: 'postgresql://localhost/fixture', NEXT_SESSION_SECRET: 'x'.repeat(40) };
  for (const value of ['https://example.test/api', 'https://secret:password@example.test/mcp', 'https://example.test/mcp?token=secret', 'file:///mcp']) {
    assert.throws(() => loadConfig({ ...env, NEXT_MCP_PUBLIC_URL: value }));
  }
  assert.throws(() => loadConfig({ ...env, NODE_ENV: 'production', NEXT_COOKIE_SECURE: 'true', NEXT_MCP_PUBLIC_URL: 'http://example.test/mcp' }));
  assert.equal(loadConfig({ ...env, NEXT_MCP_PUBLIC_URL: 'https://example.test/mcp' }).mcpPublicUrl, 'https://example.test/mcp');
});

test('official MCP SDK: stateless identity isolation, legacy envelope, revoked credentials and redacted two-stage audit', async () => {
  const app = await createApp({ databaseUrl: 'postgresql://localhost/mcp_mock_only', sessionSecret: 'x'.repeat(40), cookieSecure: false, port: 3000, host: '127.0.0.1' });
  const db = app.get(Database);
  const adminRaw = 'AdminKey_mock_fixture_001', staffRaw = 'StaffKey_mock_fixture_002';
  const row = (raw: string, id: number, role: McpActor['role']) => ({ token_id: id, uid: id, token_hash: digest(raw), scopes: 'read', revoked_at: null,
    expires_at: new Date(Date.now() + 60000), status: 'active', is_active: true, must_change_password: false,
    person_code: `MCP-${id}`, username: `mcp-${role}`, display_name: `fixture-${role}`, person_name: `person-${id}`, role });
  const rows = [row(adminRaw, 1, 'admin'), row(staffRaw, 2, 'staff')];
  const audits: { id: number; user: number; params: string; ok: boolean | null; code?: string }[] = [];
  let failAudit = false, failRead = false, failUsage = false, changeBinding = false;
  db.query = (async (sql: string, values: any[] = []) => {
    if (sql.includes('FROM api_tokens t')) {
      if (failRead) throw Error('fixture database failure containing secret');
      return { rows: rows.filter(row => [adminRaw, staffRaw][row.uid - 1].slice(0, 8) === values[0]) };
    }
    if (sql.startsWith('INSERT INTO mcp_audit_log')) {
      if (failAudit) throw Error('fixture audit failure containing secret');
      const item = { id: audits.length + 1, user: values[1], params: '{}', ok: null };
      audits.push(item); return { rows: [{ id: item.id }] };
    }
    if (sql.startsWith('UPDATE mcp_audit_log')) {
      Object.assign(audits.find(row => row.id === values[0])!, { ok: values[1], code: values[2] }); return { rows: [] };
    }
    if (sql.startsWith('UPDATE api_tokens')) { if (failUsage) throw Error('usage unavailable'); return { rows: [] }; }
    if (sql.startsWith('SET TRANSACTION')) return { rows: [] };
    throw Error('Unexpected fixture query');
  }) as any;
  db.transaction = async fn => { if (changeBinding) rows[1].person_code = 'MCP-CHANGED'; return fn(db as any); };
  const clients: Client[] = [];
  try {
    await app.listen(0, '127.0.0.1');
    const url = `http://127.0.0.1:${(app.getHttpServer().address() as AddressInfo).port}/mcp`;
    const missing = await fetch(url, { method: 'POST' });
    assert.equal(missing.status, 401); assert.match(missing.headers.get('www-authenticate')!, /resource_metadata=/);
    const discovery = await fetch(new URL('/.well-known/oauth-protected-resource', url));
    assert.equal((await discovery.json()).resource, 'http://127.0.0.1:3000/mcp');
    for (const method of ['GET', 'DELETE']) assert.equal((await fetch(url, { method })).status, 405);
    const invalid = await fetch(url, { method: 'POST', headers: { Authorization: `Bearer ${staffRaw}wrong` } });
    assert.equal(invalid.status, 401, 'matching prefix must not bypass the full digest');
    const [admin, staff] = await Promise.all([connect(url, adminRaw), connect(url, staffRaw)]); clients.push(admin, staff);
    assert.ok((await staff.listTools()).tools.some(tool => tool.name === 'visit_whoami'));
    const [a, b] = await Promise.all([call(admin), call(staff)]);
    assert.equal(a.value.data.role, 'admin'); assert.equal(b.value.data.person_code, 'MCP-2');
    assert.equal(b.value.data.currency, 'JPY'); assert.equal(b.value.data.can_write, false);
    assert.deepEqual(b.value.data.scopes, ['read']); assert.equal(audits.filter(row => row.ok === true).length, 2);
    const forbidden = await call(staff, 'visit_person', { person: 'OTHER', month: 'bad' });
    assert.equal(forbidden.value.error.code, 'FORBIDDEN_TOOL'); assert.equal(forbidden.result.isError, true);
    const args = await call(staff, 'visit_whoami', { secret: staffRaw });
    assert.equal(args.value.error.code, 'BAD_PARAM'); assert.equal(args.value.error.retryable, false);
    assert.ok(audits.every(row => row.params === '{}')); assert.ok(!JSON.stringify(audits).includes(staffRaw));
    failUsage = true; assert.equal((await call(staff)).value.ok, true); failUsage = false;
    failAudit = true; assert.equal((await call(staff)).value.error.code, 'INTERNAL'); failAudit = false;
    changeBinding = true; assert.equal((await call(staff)).value.error.code, 'UNAUTHORIZED'); changeBinding = false; rows[1].person_code = 'MCP-2';
    rows[1].revoked_at = new Date() as any;
    await assert.rejects(staff.listTools(), { code: 401 });
    rows[1].revoked_at = null;
    for (const status of ['leave', 'disabled', 'resigned', 'deleted']) {
      rows[1].status = status; assert.equal((await fetch(url, { method: 'POST', headers: { Authorization: `Bearer ${staffRaw}` } })).status, 401);
    }
    rows[1].status = 'active'; rows[1].expires_at = new Date(Date.now() - 1);
    assert.equal((await fetch(url, { method: 'POST', headers: { Authorization: `Bearer ${staffRaw}` } })).status, 401);
    rows[1].expires_at = new Date(Date.now() + 60000); rows[1].must_change_password = true;
    assert.equal((await fetch(url, { method: 'POST', headers: { Authorization: `Bearer ${staffRaw}` } })).status, 401);
    rows[1].must_change_password = false;
    const hostile = await fetch(url, { method: 'POST', headers: { Authorization: `Bearer ${staffRaw}`, Origin: 'https://hostile.test', Accept: 'application/json, text/event-stream', 'Content-Type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/list' }) });
    assert.equal(hostile.status, 403);
    failRead = true; const unavailable = await fetch(url, { method: 'POST', headers: { Authorization: `Bearer ${staffRaw}` } });
    assert.equal(unavailable.status, 503); assert.ok(!(await unavailable.text()).includes('secret'));
  } finally { await Promise.all(clients.map(client => client.close())); await app.close(); }
});

const databaseUrl = process.env.NEXT_TEST_DATABASE_URL;
test('schema-only PostgreSQL: existing token digest, UTC expiry, live binding, audit and revocation', { skip: !databaseUrl }, async () => {
  assert.match(new URL(databaseUrl!).pathname, /^\/store_test_[0-9]{13}_[a-f0-9]{12}_refactor_p1$/, 'Only the isolation runner may supply the write target');
  const app = await createApp({ databaseUrl: databaseUrl!, sessionSecret: 'x'.repeat(40), cookieSecure: false, port: 3000, host: '127.0.0.1' });
  const db = app.get(Database); const clients: Client[] = []; let uid: number | undefined;
  const raw = randomBytes(32).toString('base64url');
  try {
    await db.query("INSERT INTO persons(code,display_name,created_at) VALUES('MCP-FIXTURE','fixture person',now())");
    uid = (await db.query(`INSERT INTO users(username,password_hash,display_name,role,is_active,status,must_change_password,person_code,lang,created_at)
      VALUES('mcp-fixture','not-a-real-password-hash','fixture user','staff',true,'active',false,'MCP-FIXTURE','zh',now()) RETURNING id`)).rows[0].id;
    const token = (await db.query(`INSERT INTO api_tokens(user_id,name,token_prefix,token_hash,scopes,created_at,expires_at)
      VALUES($1,'fixture',$2,$3,'read',now(),timezone('UTC',now())+interval '1 day') RETURNING id`, [uid, raw.slice(0, 8), digest(raw)])).rows[0].id;
    await app.listen(0, '127.0.0.1');
    const url = `http://127.0.0.1:${(app.getHttpServer().address() as AddressInfo).port}/mcp`;
    const client = await connect(url, raw); clients.push(client);
    assert.equal((await call(client)).value.data.person_name, 'fixture person');
    const audit = (await db.query('SELECT ok,params_json,error_code,duration_ms FROM mcp_audit_log WHERE token_id=$1', [token])).rows;
    assert.equal(audit.length, 1); assert.equal(audit[0].ok, true); assert.equal(audit[0].params_json, '{}'); assert.equal(audit[0].error_code, null);
    assert.ok(Number.isInteger(audit[0].duration_ms));
    assert.ok((await db.query('SELECT last_used_at FROM api_tokens WHERE id=$1', [token])).rows[0].last_used_at instanceof Date);
    await db.query("UPDATE users SET person_code=NULL WHERE id=$1", [uid]);
    const unbound = (await call(client)).value;
    assert.equal(unbound.data.person_code, null); assert.equal(unbound.data.person_name, null);
    await db.query('UPDATE api_tokens SET revoked_at=now() WHERE id=$1', [token]);
    await assert.rejects(client.listTools(), { code: 401 });
  } finally {
    await Promise.all(clients.map(client => client.close()));
    if (uid) {
      await db.query('DELETE FROM mcp_audit_log WHERE user_id=$1', [uid]);
      await db.query('DELETE FROM api_tokens WHERE user_id=$1', [uid]);
      await db.query('DELETE FROM users WHERE id=$1', [uid]);
    }
    await db.query("DELETE FROM persons WHERE code='MCP-FIXTURE'");
    await app.close();
  }
});
