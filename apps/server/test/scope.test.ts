import 'reflect-metadata';
import test from 'node:test';
import assert from 'node:assert/strict';
import request from '../node_modules/supertest';
import { hash } from '@node-rs/argon2';
import { createApp } from '../src/main';
import { Database } from '../src/database';
const url = process.env.NEXT_TEST_DATABASE_URL;

test('task summaries enforce assignment, current team membership and team status', { skip: !url }, async () => {
  assert.match(new URL(url!).pathname, /_refactor_p1$/);
  const app = await createApp({ databaseUrl: url!, sessionSecret: 'scope-test-only-40-character-secret-abcdef', cookieSecure: false, port: 0, host: '127.0.0.1' });
  const db = app.get(Database);
  const password = 'scope-fixture-password';
  const pw = await hash(password);
  const ids: Record<string, number> = {};
  async function insert(table: string, values: Record<string, unknown>) {
    const columns = Object.keys(values); const args = Object.values(values);
    return (await db.query(`INSERT INTO ${table}(${columns.join(',')}) VALUES(${args.map((_,i) => '$'+(i+1)).join(',')}) RETURNING id`, args)).rows[0].id as number;
  }
  try {
    const now = new Date();
    await db.query("INSERT INTO persons(code,display_name,created_at) VALUES ('P1-SCOPE','范围验收员工',now()),('P1-CAPTAIN','范围验收队长',now())");
    ids.user = await insert('users', { username: 'scope-p1-staff', password_hash: pw, display_name: '范围验收员工', role: 'staff', is_active: true, status: 'active', must_change_password: false, person_code: 'P1-SCOPE', lang: 'zh', created_at: now });
    ids.leader = await insert('users', { username: 'scope-p1-leader', password_hash: pw, display_name: '范围验收队长', role: 'staff', is_active: true, status: 'active', must_change_password: false, person_code: 'P1-CAPTAIN', lang: 'zh', created_at: now });
    ids.team = await insert('bd_team', { name: 'P1范围测试团队', created_at: now, updated_at: now });
    ids.otherTeam = await insert('bd_team', { name: 'P1其他测试团队', created_at: now, updated_at: now });
    ids.membership = await insert('bd_team_member', { team_id: ids.team, person_code: 'P1-SCOPE', role: 'member', start_date: '2026-01-01', created_at: now });
    ids.captain = await insert('bd_team_member', { team_id: ids.team, person_code: 'P1-CAPTAIN', role: 'leader', start_date: '2026-01-01', created_at: now });
    const geometry = { type: 'Polygon', coordinates: [[[139,35],[139.01,35],[139.01,35.01],[139,35.01],[139,35]]] };
    ids.release = await insert('bd_zone_release', { fingerprint: 'p1-scope-fixture', source: {}, parameters: {}, audit: {}, reviews: [], created_by: ids.leader, created_at: now });
    ids.region = await insert('bd_region', { release_id: ids.release, source_id: 'p1-scope', name: 'P1验收片区', geometry, area_km2: 1, store_count: 2, review: {}, state: 'ready', revision: 0, updated_at: now });
    ids.grid = await insert('bd_work_grid', { release_id: ids.release, source_id: 'p1-scope', geometry, area_km2: 1, store_count: 2, state: 'ready', progress: 0, task_state: 'pending', revision: 0, updated_at: now });
    for (const [key, team] of [['visible', ids.team], ['hidden', ids.otherTeam]] as const) {
      ids[key] = await insert('bd_main_task', { title: 'P1-' + key, status: 'in_progress', progress: 50, created_by: ids.leader, created_at: now, revision: 0 });
      ids[key+'Region'] = await insert('bd_region_task', { main_task_id: ids[key], region_id: ids.region, snapshot: {}, team_id: team, leader_id: ids.leader, status: 'in_progress', progress: 50, revision: 0, created_at: now });
      ids[key+'Grid'] = await insert('bd_grid_task', { region_task_id: ids[key+'Region'], grid_id: ids.grid, snapshot: {}, assignee_id: ids.user, status: 'in_progress', progress: 50, revision: 0, created_at: now, total_stores: 2, completed_stores: 1 });
    }
    const staff = request.agent(app.getHttpServer());
    await staff.post('/api/auth/login').send({ username: 'scope-p1-staff', password }).expect(201);
    const visible = await staff.get('/api/work/batches').expect(200);
    assert.deepEqual(visible.body.items.map((x: { id: number }) => x.id), [ids.visible]);
    assert.equal(visible.body.items[0].totalStores, 2);
    const leader = request.agent(app.getHttpServer());
    await leader.post('/api/auth/login').send({ username: 'scope-p1-leader', password }).expect(201);
    assert.deepEqual((await leader.get('/api/work/batches').expect(200)).body.items.map((x: { id: number }) => x.id), [ids.visible]);
    await db.query('UPDATE bd_team_member SET end_date=$1 WHERE id=$2', ['2026-01-02', ids.captain]);
    assert.deepEqual((await leader.get('/api/work/batches').expect(200)).body.items, []);
    await db.query('UPDATE bd_team_member SET end_date=$1 WHERE id=$2', ['2026-01-02', ids.membership]);
    assert.deepEqual((await staff.get('/api/work/batches').expect(200)).body.items, []);
    await db.query('UPDATE bd_team_member SET end_date=NULL WHERE id=$1', [ids.membership]);
    await db.query("UPDATE bd_team SET status='disabled' WHERE id=$1", [ids.team]);
    assert.deepEqual((await staff.get('/api/work/batches').expect(200)).body.items, []);
  } finally {
    // Cleanup only explicitly-created fixture IDs, in foreign-key order.
    await db.query('DELETE FROM bd_grid_task WHERE id=ANY($1::int[])', [[ids.visibleGrid, ids.hiddenGrid].filter(Boolean)]);
    await db.query('DELETE FROM bd_region_task WHERE id=ANY($1::int[])', [[ids.visibleRegion, ids.hiddenRegion].filter(Boolean)]);
    await db.query('DELETE FROM bd_main_task WHERE id=ANY($1::int[])', [[ids.visible, ids.hidden].filter(Boolean)]);
    await db.query('DELETE FROM bd_work_grid WHERE id=$1', [ids.grid]);
    await db.query('DELETE FROM bd_region WHERE id=$1', [ids.region]);
    await db.query('DELETE FROM bd_zone_release WHERE id=$1', [ids.release]);
    await db.query('DELETE FROM bd_team_member WHERE id=ANY($1::int[])', [[ids.membership, ids.captain].filter(Boolean)]);
    await db.query('DELETE FROM bd_team WHERE id=ANY($1::int[])', [[ids.team, ids.otherTeam].filter(Boolean)]);
    await db.query('DELETE FROM users WHERE id=ANY($1::int[])', [[ids.user, ids.leader].filter(Boolean)]);
    await db.query("DELETE FROM persons WHERE code IN ('P1-SCOPE','P1-CAPTAIN')");
    await app.close();
  }
});
