import 'reflect-metadata';
import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash, randomBytes } from 'node:crypto';
import type { AddressInfo } from 'node:net';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { createApp } from '../src/main';
import { Database } from '../src/database';
import { McpTaskReadService } from '../src/mcp-task-read';
import { jstToday } from '../src/business-dates';
import type { McpActor } from '../src/mcp';
import { spawnLegacyPython } from '../../../scripts/legacy-python.mjs';

const actor: McpActor = { uid: 1, tokenId: 1, role: 'staff', scopes: ['read'], personCode: 'MCP-TASK-A', username: 'fixture', displayName: '', personName: 'fixture' };
test('MCP task reads deny foreign identity/admin scope before parameters, and reject malformed filters', async () => {
  const service = new McpTaskReadService(), db = { query: async () => { throw Error('Validation must precede business reads'); } } as any;
  await assert.rejects(service.run(db, actor, 'visit_team_tasks', { kw: 'other', line_id: 'bad' }), { code: 'FORBIDDEN_TOOL' });
  for (const role of ['staff', 'leader'] as const) await assert.rejects(service.run(db, { ...actor, role }, 'visit_task_board', { page: 'bad' }), { code: 'FORBIDDEN_TOOL' });
  for (const key of ['person', 'person_code', 'person_id']) await assert.rejects(service.run(db, actor, 'visit_my_tasks', { [key]: 'OTHER' }), { code: 'FORBIDDEN_TOOL' });
  await assert.rejects(service.run(db, { ...actor, personCode: null }, 'visit_my_tasks', {}), { code: 'BAD_PARAM' });
  for (const args of [{ page: 0 }, { per: -1 }, { line_id: 'bad' }, { stale_only: 'yes' }, { kw: 10 }, { tab: 'unknown' }]) await assert.rejects(service.run(db, { ...actor, role: 'admin' }, 'visit_task_board', args), { code: 'BAD_PARAM' });
});

const databaseUrl = process.env.NEXT_TEST_DATABASE_URL;
test('schema-only SDK and legacy task queries: own/team/board, CJK filters, pagination, latest review and live membership', { skip: !databaseUrl }, async () => {
  assert.match(new URL(databaseUrl!).pathname, /^\/store_test_[0-9]{13}_[a-f0-9]{12}_refactor_p1$/);
  const app = await createApp({ databaseUrl: databaseUrl!, sessionSecret: 'x'.repeat(40), cookieSecure: false, host: '127.0.0.1', port: 3000 });
  const db = app.get(Database), clients = new Map<string, Client>();
  const today = jstToday(), day = (offset: number) => new Date(Date.parse(today + 'T00:00:00Z') + offset * 86400000).toISOString().slice(0, 10);
  const codes = { worker: 'MCP-TASK-A', other: 'MCP-TASK-B', leader: 'MCP-TASK-LA', otherLeader: 'MCP-TASK-LB', empty: 'MCP-TASK-EMPTY', future: 'MCP-TASK-FUTURE' };
  const users = new Map<string, number>(), teams: number[] = [], places: number[] = [], stations: number[] = [], lines: number[] = [], tasks: number[] = [];
  let capInserted = false;
  const originals = db.transaction.bind(db); let queries = 0;
  db.transaction = fn => originals(client => fn({ query: (...args: any[]) => { queries++; return (client.query as any)(...args); } } as any));
  const fingerprints = async () => {
    const result: Record<string, string> = {};
    for (const table of ['bd_task', 'bd_task_assign', 'bd_task_progress', 'staff_daily_reports']) result[table] = (await db.query(`SELECT md5(coalesce(string_agg(row_to_json(t)::text,'' ORDER BY id),'')) AS value FROM ${table} t`)).rows[0].value;
    return result;
  };
  try {
    for (const code of Object.values(codes)) await db.query('INSERT INTO persons(code,display_name,created_at) VALUES($1,$2,now())', [code, code + ' name']);
    for (const [kind, code] of [...Object.entries(codes), ['admin', null]] as [string, string | null][]) {
      const role = ['leader', 'otherLeader', 'future'].includes(kind) ? 'leader' : kind === 'admin' ? 'admin' : 'staff';
      users.set(kind, (await db.query(`INSERT INTO users(username,password_hash,display_name,person_code,role,status,is_active,must_change_password,lang,created_at)
        VALUES($1,'fixture-only-hash',$1,$2,$3,'active',true,false,'zh',now()) RETURNING id`, ['mcp-task-' + kind, code, role])).rows[0].id);
    }
    for (const [name, operator] of [['京葉線', 'JR東日本'], ['本線', '京急']]) lines.push((await db.query("INSERT INTO bd_line(name,name_norm,operator,operator_short,created_at,updated_at) VALUES($1,$1,$2,$2,now(),now()) RETURNING id", [name, operator])).rows[0].id);
    for (const [index, name] of ['Alpha', 'Beta', 'Gamma', 'Closed'].entries()) teams.push((await db.query("INSERT INTO bd_team(name,status,created_at,updated_at) VALUES($1,$2,now(),now()) RETURNING id", [name, index === 3 ? 'closed' : 'active'])).rows[0].id);
    for (const [team, code, role, start] of [[teams[0], codes.worker, 'member', day(-30)], [teams[1], codes.other, 'member', day(-30)], [teams[0], codes.leader, 'leader', day(-30)], [teams[1], codes.otherLeader, 'leader', day(-30)], [teams[1], codes.future, 'leader', day(1)]] as const) await db.query('INSERT INTO bd_team_member(team_id,person_code,role,start_date,created_at) VALUES($1,$2,$3,$4,now())', [team, code, role, start]);
    for (let index = 0; index < 15; index++) {
      const name = index === 1 ? '渋谷' : 'Task place ' + index;
      const place = (await db.query('INSERT INTO bd_station_place(name,name_norm,lines_text,status,created_at,updated_at) VALUES($1,$1,$2,$3,now(),now()) RETURNING id', [name, index % 2 ? '京急 本線' : '京葉線', index === 14 ? 'closed' : 'active'])).rows[0].id;
      places.push(place);
      stations.push((await db.query("INSERT INTO bd_station(name,name_norm,line,line_id,place_id,created_at,updated_at) VALUES($1,$1,$2,$3,$4,now(),now()) RETURNING id", [name + ' station', index % 2 ? '本線' : '京葉線', lines[index % 2], place])).rows[0].id);
    }
    // Cross-line physical station, plus a task attached to a legacy station rather than a place.
    stations.push((await db.query("INSERT INTO bd_station(name,name_norm,line,line_id,place_id,created_at,updated_at) VALUES('Cross-line','Cross-line','京葉線',$1,$2,now(),now()) RETURNING id", [lines[0], places[1]])).rows[0].id);
    const specs = [
      { team: teams[0], state: 'unassigned', pct: 0 },
      { team: teams[0], state: 'doing', pct: 40, person: codes.worker, dispatch: day(-3), progress: day(-1), reported: 60 },
      { team: teams[0], state: 'done', pct: 100, person: codes.worker, dispatch: today, progress: today, reported: 100, review: 'confirmed' },
      { team: teams[0], state: 'done', pct: 100, person: codes.worker, dispatch: day(-1), progress: day(-1), reported: 100, review: 'confirmed' },
      { team: teams[0], state: 'doing', pct: 25, progress: day(-3), reported: 25 },
      { team: teams[0], state: 'doing', pct: 10, person: codes.worker, dispatch: day(-2), progress: day(-2), reported: 0 },
      { team: teams[0], state: 'doing', pct: 0, person: codes.worker, dispatch: null, noDate: true },
      { team: teams[1], state: 'doing', pct: 30, person: codes.other, dispatch: day(-4), progress: day(-3), reported: 40 },
      { team: teams[3], state: 'unassigned', pct: 0 },
      { team: null, state: 'unassigned', pct: 0 },
      { team: null, state: 'doing', pct: 20 },
      { team: teams[2], state: 'unassigned', pct: 0 },
      { team: teams[0], state: 'doing', pct: 30, person: codes.worker, dispatch: day(-4), progress: day(-3), reported: 50, legacy: true },
    ];
    for (const [index, spec] of specs.entries()) {
      const task = (await db.query(`INSERT INTO bd_task(station_id,place_id,team_id,state,pct,assign_date,start_date,done_date,created_at,updated_at)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8,now(),now()) RETURNING id`, [spec.legacy ? stations[index] : null, spec.legacy ? null : places[index], spec.team, spec.state, spec.pct, spec.noDate ? null : day(-6), spec.pct ? day(-5) : null, spec.state === 'done' ? spec.progress : null])).rows[0].id;
      tasks.push(task);
      if (spec.person) await db.query('INSERT INTO bd_task_assign(task_id,person_code,dispatch_date,assigned_at) VALUES($1,$2,$3,now())', [task, spec.person, spec.dispatch]);
      if (spec.progress) await db.query(`INSERT INTO bd_task_progress(task_id,progress_date,pct,reported_pct,review_status,created_at,updated_at)
        VALUES($1,$2,$3,$4,$5,now(),now())`, [task, spec.progress, spec.pct, spec.reported, spec.review || 'pending']);
    }
    await db.query("INSERT INTO bd_task_progress(task_id,progress_date,pct,reported_pct,review_status,created_at,updated_at) VALUES($1,$2,90,100,'pending',now(),now())", [tasks[2], day(-3)]);
    await db.query("INSERT INTO staff_daily_reports(person_code,user_id,report_date,area,p1_cnt,p2_cnt,total_cnt,submitted_at,created_at) VALUES($1,$2,$3,'fixture-area',3,4,7,now(),now())", [codes.worker, users.get('worker'), today]);
    await app.listen(0, '127.0.0.1');
    const url = new URL(`http://127.0.0.1:${(app.getHttpServer().address() as AddressInfo).port}/mcp`);
    for (const [kind, uid] of users) {
      const raw = randomBytes(32).toString('base64url');
      await db.query("INSERT INTO api_tokens(user_id,name,token_prefix,token_hash,scopes,created_at) VALUES($1,'task-read fixture',$2,$3,'read',now())", [uid, raw.slice(0, 8), createHash('sha256').update(raw).digest('hex')]);
      const client = new Client({ name: 'mcp-task-fixture', version: '1' }); clients.set(kind, client);
      await client.connect(new StreamableHTTPClientTransport(url, { requestInit: { headers: { Authorization: `Bearer ${raw}` } } }));
    }
    const call = async (kind: string, tool: string, args: Record<string, unknown> = {}) => {
      const result = await clients.get(kind)!.callTool({ name: tool, arguments: args }); return JSON.parse((result.content as { text: string }[])[0].text);
    };
    const taskNames = ['visit_my_tasks', 'visit_team_tasks', 'visit_task_board'];
    for (const [kind, expected] of [['worker', ['visit_my_tasks']], ['leader', ['visit_my_tasks', 'visit_team_tasks']], ['admin', taskNames]] as const) assert.deepEqual((await clients.get(kind)!.listTools()).tools.filter(tool => taskNames.includes(tool.name)).map(tool => tool.name), expected);
    const cases: { tool: string; kind: string; personCode?: string; teamIds?: number[]; args: Record<string, unknown> }[] = [];
    for (const kind of ['worker', 'other', 'empty'] as const) cases.push({ tool: 'visit_my_tasks', kind, personCode: codes[kind], args: {} });
    const filters: Record<string, unknown>[] = [{}, { kw: '京叶线' }, { line_id: lines[0] }, { line_id: lines[1] }, { kw: '京急本線' }, { kw: 'missing' }, { kw: 'Alpha' }];
    for (const tab of ['', 'unassigned', 'doing', 'done']) {
      for (const filter of filters) cases.push({ tool: 'visit_team_tasks', kind: 'leader', teamIds: [teams[0]], args: { tab, ...filter } });
      cases.push({ tool: 'visit_team_tasks', kind: 'otherLeader', teamIds: [teams[1]], args: { tab } });
      cases.push({ tool: 'visit_team_tasks', kind: 'admin', teamIds: teams, args: { tab } });
    }
    for (const tab of ['', 'unassigned', 'assigned', 'done']) for (const filter of [{}, { line_id: lines[0] }, { kw: '京叶线' }, { kw: '京急本線' }, { kw: 'Alpha' }, { kw: 'missing' }]) for (const stale_only of [false, true]) cases.push({ tool: 'visit_task_board', kind: 'admin', args: { tab, ...filter, stale_only, per: 3 } });
    for (const page of [1, 2, 999]) cases.push({ tool: 'visit_task_board', kind: 'admin', args: { page, per: 2 } });
    assert.equal(cases.length, 90);
    const before = await fingerprints();
    const oracle = spawnLegacyPython(['scripts/mcp-task-read-oracle.py'], { env: { DATABASE_URL: databaseUrl!, NEXT_TEST_DATABASE_URL: databaseUrl! }, input: JSON.stringify({ today, cases }), encoding: 'utf8', timeout: 30000, maxBuffer: 8 * 1024 * 1024 });
    assert.equal(oracle.status, 0, oracle.stderr || 'legacy task oracle failed');
    const expected = JSON.parse(oracle.stdout);
    for (let index = 0; index < cases.length; index++) {
      const value = await call(cases[index].kind, cases[index].tool, cases[index].args);
      assert.equal(value.ok, true, JSON.stringify({ case: cases[index], error: value.error }));
      assert.deepEqual(value.data, expected[index], JSON.stringify(cases[index]));
    }
    assert.deepEqual(await fingerprints(), before, 'Task reads must not alter progress, assignments, task state or daily reports');
    for (const kw of ['Alpha', 'missing']) { queries = 0; const value = await call('leader', 'visit_team_tasks', { kw }); assert.equal(value.ok, true); assert.equal(queries, 5, 'SQL count stays constant for populated/empty task results'); }
    assert.equal((await call('worker', 'visit_task_board', { page: 'bad' })).error.code, 'FORBIDDEN_TOOL');
    assert.equal((await call('worker', 'visit_team_tasks', { team_id: teams[1] })).error.code, 'FORBIDDEN_TOOL');
    assert.equal((await call('worker', 'visit_my_tasks', { person_code: codes.other })).error.code, 'FORBIDDEN_TOOL');
    assert.equal((await call('future', 'visit_team_tasks')).error.code, 'FORBIDDEN_TOOL');
    assert.equal((await call('worker', 'visit_my_perf')).data.month, null, 'financial tool dispatch remains independent');
    await db.query('INSERT INTO bd_task_assign(task_id,person_code,dispatch_date,assigned_at) VALUES($1,$2,$3,now())', [tasks[7], codes.worker, today]);
    assert.ok(!(await call('worker', 'visit_my_tasks')).data.open.some((row: any) => row.task_id === tasks[7]), 'stale cross-team assignment cannot expose another team');
    await db.query('UPDATE bd_team_member SET end_date=$1 WHERE person_code=$2', [today, codes.worker]);
    assert.equal((await call('worker', 'visit_my_tasks')).data.n_open, 0, 'ended membership suppresses stale assignments');
    await db.query('UPDATE bd_team_member SET end_date=$1 WHERE person_code=$2', [today, codes.leader]);
    assert.equal((await call('leader', 'visit_team_tasks')).error.code, 'FORBIDDEN_TOOL');
    await db.query('UPDATE bd_team_member SET end_date=NULL,start_date=$1 WHERE person_code=$2', [day(-30), codes.leader]);
    await db.query("INSERT INTO bd_role_cap(role,capability,allowed,updated_at) VALUES('leader','task.view_team',false,now())"); capInserted = true;
    assert.equal((await call('leader', 'visit_team_tasks')).error.code, 'FORBIDDEN_TOOL');
    await db.query("DELETE FROM bd_role_cap WHERE role='leader' AND capability='task.view_team'"); capInserted = false;
    await db.query("UPDATE users SET role='staff' WHERE id=$1", [users.get('leader')]);
    assert.equal((await call('leader', 'visit_team_tasks')).error.code, 'FORBIDDEN_TOOL');
    const audit = (await db.query('SELECT count(*)::int AS n FROM mcp_audit_log WHERE user_id=ANY($1::int[]) AND (ok IS NULL OR params_json<>\'{}\')', [[...users.values()]])).rows[0]; assert.equal(audit.n, 0);
  } finally {
    await Promise.all([...clients.values()].map(client => client.close()));
    if (capInserted) await db.query("DELETE FROM bd_role_cap WHERE role='leader' AND capability='task.view_team'");
    await db.query('DELETE FROM mcp_audit_log WHERE user_id=ANY($1::int[])', [[...users.values()]]); await db.query('DELETE FROM api_tokens WHERE user_id=ANY($1::int[])', [[...users.values()]]);
    for (const table of ['bd_task_progress', 'bd_task_assign']) await db.query(`DELETE FROM ${table} WHERE task_id=ANY($1::int[])`, [tasks]);
    await db.query('DELETE FROM bd_task WHERE id=ANY($1::int[])', [tasks]); await db.query('DELETE FROM staff_daily_reports WHERE person_code=ANY($1::text[])', [Object.values(codes)]);
    await db.query('DELETE FROM bd_team_member WHERE team_id=ANY($1::int[])', [teams]); await db.query('DELETE FROM bd_team WHERE id=ANY($1::int[])', [teams]);
    await db.query('DELETE FROM bd_station WHERE id=ANY($1::int[])', [stations]); await db.query('DELETE FROM bd_station_place WHERE id=ANY($1::int[])', [places]); await db.query('DELETE FROM bd_line WHERE id=ANY($1::int[])', [lines]);
    await db.query('DELETE FROM users WHERE id=ANY($1::int[])', [[...users.values()]]); await db.query('DELETE FROM persons WHERE code=ANY($1::text[])', [Object.values(codes)]);
    await app.close();
  }
});
