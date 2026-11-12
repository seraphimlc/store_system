import {spawnLegacyPython} from '../../../scripts/legacy-python.mjs';
import 'reflect-metadata';
import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { Database } from '../src/database';
import { formalZoneRelease, zoneReleaseFingerprint, type ZoneCandidate } from '../src/zone-release';
import { previewScopeZones, type ScopeSource } from '../../../packages/domain/src/zone-scope';
import { gridToGeographic, type GridPolygon } from '../../../packages/domain/src/grid-geometry';
function source(): ScopeSource { const geometries = [0, 3000].map(x => ({ type: 'Polygon', coordinates: [[[x, 0], [x + 1000, 0], [x + 1000, 1000], [x, 1000], [x, 0]]] } as GridPolygon)), work = { type: 'MultiPolygon' as const, coordinates: geometries.map(g => gridToGeographic(g).coordinates as GridPolygon['coordinates']) }; return { ward_code: 'tokyo23', ward_name: '东京23区整体', scope_ward_count: 23, snapshot_id: '固定来源-01', captured_at: '2026-11-11T00:00:00Z', source: { geometry: 'synthetic' }, store_count: 8, area_km2: 2, excluded_area_km2: 0, road_count: 0, boundary: work, work_area: work, metric_crs: 'EPSG:6677', coverage_revision: 2, road_cells: geometries.map((geometry, i) => ({ id: 'cell-' + i, metric_geometry: geometry, store_count: 4, merchant_ids: Array.from({ length: 4 }, (_, k) => 'store-' + i + '-' + k) })), topology: [[], []], merchant_audit: { inside_boundary: 8, inside_work: 8 }, review_merchants: [] }; }
test('release fingerprint matches Python identity serialization including float and non-ASCII source', async () => {
    const doc = source(), result = await previewScopeZones(doc, 4, 2, { includeMemberships: true }), raw = JSON.stringify({ source: doc.snapshot_id, parameters: result.summary.balance, zones: result.features.map(f => f.id).sort(), grids: result.grids.map(f => f.id).sort() }), oracle = spawnLegacyPython( ['-c', "import json,hashlib,sys; d=json.loads(sys.stdin.read()); [d['parameters'].__setitem__(k,float(d['parameters'][k])) for k in ('target_area','area_weight','load_min','load_max')]; print(hashlib.sha256(json.dumps(d,sort_keys=True).encode()).hexdigest())"], { encoding: 'utf8', input: raw });
    assert.equal(oracle.status, 0, oracle.stderr);
    assert.equal(zoneReleaseFingerprint(result, doc), oracle.stdout.trim());
});
const url = process.env.NEXT_TEST_DATABASE_URL;
test('formal zone release is transactional, idempotent, bd-only and protects replacement history/team mappings', { skip: !url }, async () => {
    assert.equal(process.env.NEXT_TEST_ISOLATED, '1');
    assert.match(new URL(url!).pathname, /\/store_test_[0-9]{13}_[a-f0-9]{12}_refactor_p1$/);
    const db = new Database({ databaseUrl: url!, sessionSecret: 'zone-release-test-secret-at-least-40-characters', cookieSecure: false, port: 0, host: '127.0.0.1' }), doc = source(), split = await previewScopeZones(doc, 4, 2, { includeMemberships: true }), merged = structuredClone(split);
    let admin: number | undefined, staff: number | undefined, team: number | undefined;
    const apply = (result = merged, replace = false, actor = admin!) => db.transaction(client => formalZoneRelease(client, actor, result, doc, replace));
    try {
        assert.equal((await db.query('SELECT count(*)::int AS n FROM bd_zone_release')).rows[0].n, 0, 'Temporary test database must not contain another release');
        admin = (await db.query("INSERT INTO users(username,password_hash,display_name,role,status,is_active,must_change_password,created_at) VALUES('zone-release-admin','unused','发布管理员','admin','active',true,false,now()) RETURNING id")).rows[0].id;
        staff = (await db.query("INSERT INTO users(username,password_hash,display_name,role,status,is_active,must_change_password,created_at) VALUES('zone-release-staff','unused','员工','staff','active',true,false,now()) RETURNING id")).rows[0].id;
        const financial = async () => (await db.query('SELECT (SELECT count(*) FROM formal_records)::int AS formal,(SELECT count(*) FROM person_daily_stats)::int AS daily,(SELECT count(*) FROM month_perf_records)::int AS monthly,(SELECT count(*) FROM payroll_period_rows)::int AS payroll')).rows[0], before = await financial();
        // Existing whole-region identity may split into multiple new regions; no geometry is inferred from names.
        merged.features = [{ ...merged.features[0], id: 'merged-region', geometry: doc.work_area, properties: { ...merged.features[0].properties, id: 'merged-region', area_km2: 2, store_count: 8, shape_valid: false, needs_review: true, review_reason: 'shape_conflict' } }];
        merged.grids.forEach(g => g.properties.zone_id = 'merged-region');
        await assert.rejects(apply(merged, false, staff), /仅管理员/);
        const broken = structuredClone(merged);
        broken.audit.geometric_pass = false;
        await assert.rejects(apply(broken), /覆盖或商户/);
        const first = await apply();
        assert.equal(first.reused, false);
        assert.equal((await db.query('SELECT count(*)::int AS n FROM bd_grid_store')).rows[0].n, 8);
        assert.equal((await db.query('SELECT count(*)::int AS n FROM bd_region')).rows[0].n, 1);
        const review = (await db.query('SELECT review FROM bd_region')).rows[0].review;
        assert.ok(review.includes('shape_conflict'));
        const duplicate = await Promise.all([apply(), apply()]);
        assert.ok(duplicate.every(r => r.id === first.id && r.reused));
        assert.equal((await db.query("SELECT count(*)::int AS n FROM bd_log WHERE domain='zone' AND action='generate'")).rows[0].n, 1);
        await assert.rejects(apply(split), /已有正式版本/);
        team = (await db.query("INSERT INTO bd_team(name,status,created_at,updated_at) VALUES('release-carry-team','active',now(),now()) RETURNING id")).rows[0].id;
        await db.query('UPDATE bd_region SET team_id=$1,leader_id=$2', [team, admin]);
        const badMap = structuredClone(split);
        badMap.grids[0].properties.cell_ids = ['unknown-cell'];
        await assert.rejects(apply(badMap, true), /缺少新片区归属/);
        assert.equal((await db.query('SELECT id FROM bd_zone_release')).rows[0].id, first.id);
        // Failure after deletion must roll back the complete old release and its assignment.
        const invalid = structuredClone(split);
        invalid.audit.merchant_pass = false;
        await assert.rejects(apply(invalid, true), /覆盖或商户/);
        assert.equal((await db.query('SELECT id FROM bd_zone_release')).rows[0].id, first.id);
        assert.equal((await db.query('SELECT team_id FROM bd_region')).rows[0].team_id, team);
        const replacement = await apply(split, true);
        assert.equal(replacement.reused, false);
        const regions = (await db.query('SELECT team_id,leader_id,revision FROM bd_region ORDER BY id')).rows;
        assert.equal(regions.length, 2);
        assert.ok(regions.every(r => r.team_id === team && r.leader_id === admin && r.revision === 1));
        assert.equal((await db.query('SELECT count(*)::int AS n FROM bd_zone_release')).rows[0].n, 1);
        const grid = (await db.query('SELECT id FROM bd_work_grid ORDER BY id LIMIT 1')).rows[0].id;
        await db.query('UPDATE bd_work_grid SET progress=1 WHERE id=$1', [grid]);
        await assert.rejects(apply(split, true), /巡店进展/);
        await db.query('UPDATE bd_work_grid SET progress=0 WHERE id=$1', [grid]);
        await db.query('UPDATE bd_work_grid SET assignee_id=$1 WHERE id=$2', [staff, grid]);
        await assert.rejects(apply(split, true), /巡店进展/);
        await db.query('UPDATE bd_work_grid SET assignee_id=NULL WHERE id=$1', [grid]);
        const history = (await db.query("INSERT INTO bd_main_task(title,status,progress,created_by,created_at,revision) VALUES('release-history-guard','active',0,$1,now(),0) RETURNING id", [admin])).rows[0].id;
        try {
            await assert.rejects(apply(split, true), /已有巡店批次历史/);
        }
        finally {
            await db.query('DELETE FROM bd_main_task WHERE id=$1', [history]);
        }
        // Two old teams cannot be silently mapped to one new owner.
        const team2 = (await db.query("INSERT INTO bd_team(name,status,created_at,updated_at) VALUES('release-conflict-team','active',now(),now()) RETURNING id")).rows[0].id;
        try {
            await db.query('UPDATE bd_region SET team_id=$1 WHERE id=(SELECT max(id) FROM bd_region)', [team2]);
            await assert.rejects(apply(merged, true), /团队归属冲突/);
        }
        finally {
            await db.query('UPDATE bd_region SET team_id=$1', [team]);
            await db.query('DELETE FROM bd_team WHERE id=$1', [team2]);
        }
        assert.deepEqual(await financial(), before);
    }
    finally {
        await db.query('DELETE FROM bd_grid_store');
        await db.query('DELETE FROM bd_work_grid');
        await db.query('DELETE FROM bd_region');
        await db.query('DELETE FROM bd_zone_release');
        await db.query("DELETE FROM bd_log WHERE actor IN ('zone-release-admin','zone-release-staff')");
        if (team)
            await db.query('DELETE FROM bd_team WHERE id=$1', [team]);
        await db.query('DELETE FROM users WHERE id=ANY($1::int[])', [[admin, staff].filter(Boolean)]);
        await db.onModuleDestroy();
    }
});
test('release CLI defaults to file-only verification and rejects unapproved replacement/remote targets', async () => {
    const { mkdtemp, writeFile, rm } = await import('node:fs/promises'), { join } = await import('node:path'), { tmpdir } = await import('node:os');
    const dir = await mkdtemp(join(tmpdir(), 'zone-release-test-')), doc = source(), candidate = await previewScopeZones(doc, 4, 2, { includeMemberships: true }), input = join(dir, 'source.json'), output = join(dir, 'candidate.json');
    const run = (extra: string[], url = 'postgresql://u:p@remote/db_refactor_p1') => spawnSync(process.execPath, ['--import', 'tsx', 'scripts/apply-zone-release.mts', '--source', input, '--candidate', output, ...extra], { encoding: 'utf8', env: { ...process.env, NEXT_DATABASE_URL: url } });
    try {
        await writeFile(input, JSON.stringify(doc));
        await writeFile(output, JSON.stringify(candidate));
        const dry = run([]);
        assert.equal(dry.status, 0, dry.stderr);
        assert.equal(JSON.parse(dry.stdout).databaseTouched, false);
        assert.equal(JSON.parse(dry.stdout).fingerprint, zoneReleaseFingerprint(candidate, doc));
        assert.notEqual(run(['--replace-unused']).status, 0);
        assert.notEqual(run(['--apply']).status, 0);
        const remote = run(['--apply', '--admin-user', 'admin']);
        assert.notEqual(remote.status, 0);
        assert.match(remote.stderr, /limited to an explicit local/);
    }
    finally {
        await rm(dir, { recursive: true, force: true });
    }
});
