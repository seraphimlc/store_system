import 'reflect-metadata';
import test from 'node:test';
import assert from 'node:assert/strict';
import { Database } from '../src/database';
import { importNaturalCandidate, validateNaturalCandidate, type NaturalCandidate } from '../src/natural-grid-import';
import { prepareNaturalGrids, naturalGridCandidate } from '../../../packages/domain/src/natural-grids';
import { bindMerchantRows } from '../../../packages/domain/src/zone-merchant-binding';
import { gridToGeographic, gridPointToGeographic, type GridPolygon } from '../../../packages/domain/src/grid-geometry';
async function fixture(region = 'tokyo23', offset = 0, id = 'merchant-one') {
    const metric: GridPolygon = { type: 'Polygon', coordinates: [[[10000 + offset, -40000], [11000 + offset, -40000], [11000 + offset, -39000], [10000 + offset, -39000], [10000 + offset, -40000]]] }, a = gridPointToGeographic([10500 + offset, -40100]), b = gridPointToGeographic([10500 + offset, -38900]), point = gridPointToGeographic([10250 + offset, -39500]);
    const doc = await prepareNaturalGrids({ boundary: gridToGeographic(metric), elements: [{ type: 'way', id: 1, tags: { highway: 'primary' }, geometry: [{ lon: a[0], lat: a[1] }, { lon: b[0], lat: b[1] }] }], excluded: [], coastlines: [], capturedAt: 'fixed', source: { geometry: 'synthetic' }, regionCode: region });
    const bound = await bindMerchantRows(doc, [{ place_id: id, lat: point[1], lon: point[0] }], { read_at: 'synthetic' });
    return naturalGridCandidate(bound);
}
test('natural import revalidates candidate hashes, geometry coverage and merchant uniqueness rather than trusting audit flags', async () => {
    const candidate = await fixture();
    assert.equal((await validateNaturalCandidate(candidate)).length, 1);
    const before = JSON.stringify(candidate);
    for (const change of [(r: NaturalCandidate) => { r.features[0].properties.store_count++; }, (r: NaturalCandidate) => { r.audit.merchant_pass = false; }, (r: NaturalCandidate) => { r.audit.geometric_pass = false; }]) {
        const broken = structuredClone(candidate);
        change(broken);
        await assert.rejects(validateNaturalCandidate(broken), /候选内容或审计/);
    }
    assert.equal(JSON.stringify(candidate), before);
    const multipart = structuredClone(candidate.source_document);
    const frame: GridPolygon = { type: 'Polygon', coordinates: [[[10000, -40000], [11000, -40000], [11000, -39000], [10000, -39000], [10000, -40000]], [[10200, -39800], [10200, -39200], [10800, -39200], [10800, -39800], [10200, -39800]]] };
    const island: GridPolygon = { type: 'Polygon', coordinates: [[[10400, -39600], [10600, -39600], [10600, -39400], [10400, -39400], [10400, -39600]]] };
    const combined = { type: 'MultiPolygon' as const, coordinates: [frame.coordinates, island.coordinates] };
    multipart.road_cells = [{ ...multipart.road_cells[0], metric_geometry: combined, area_km2: .68, merchant_ids: ['merchant-one'], store_count: 1 }];
    multipart.work_area = gridToGeographic(combined);
    multipart.store_count = 1;
    assert.equal((await validateNaturalCandidate(await naturalGridCandidate(multipart))).length, 1);
    const doc = structuredClone(candidate.source_document);
    doc.road_cells[1].merchant_ids = ['merchant-one'];
    doc.road_cells[1].store_count = 1;
    doc.store_count = 2;
    await assert.rejects(validateNaturalCandidate(await naturalGridCandidate(doc)), /唯一归属/);
});
const url = process.env.NEXT_TEST_DATABASE_URL;
test('natural import preserves unassigned grids, enforces append/replace gates, and rolls back failures in an isolated database', { skip: !url }, async () => {
    assert.equal(process.env.NEXT_TEST_ISOLATED, '1');
    assert.match(new URL(url!).pathname, /\/store_test_[0-9]{13}_[a-f0-9]{12}_refactor_p1$/);
    const db = new Database({ databaseUrl: url!, sessionSecret: 'natural-import-isolated-secret-at-least40', cookieSecure: false, port: 0, host: '127.0.0.1' });
    let actor: number | undefined;
    const apply = (candidate: NaturalCandidate, options = {}) => db.transaction(client => importNaturalCandidate(client, actor!, candidate, options));
    try {
        assert.equal((await db.query('SELECT count(*)::int AS n FROM bd_zone_release')).rows[0].n, 0);
        actor = (await db.query("INSERT INTO users(username,password_hash,display_name,role,status,is_active,must_change_password,created_at) VALUES('natural-import-admin','unused','自然导入','admin','active',true,false,now()) RETURNING id")).rows[0].id;
        const candidate = await fixture(), first = await apply(candidate);
        assert.equal(first.reused, false);
        assert.equal((await db.query('SELECT count(*)::int AS n FROM bd_region')).rows[0].n, 0);
        assert.equal((await db.query('SELECT count(*)::int AS n FROM bd_work_grid WHERE zone_id IS NULL')).rows[0].n, 2);
        assert.equal((await db.query('SELECT count(*)::int AS n FROM bd_grid_store')).rows[0].n, 1);
        await assert.rejects(apply(candidate), /已有正式版本/);
        await assert.rejects(apply(candidate, { appendRegion: true }), /该地区已有/);
        const append = await fixture('kanagawa', 3000, 'merchant-two');
        await assert.rejects(apply(append, { appendRegion: true }), /极端面积/);
        // Only this synthetic transaction test supplies the result of the still-separate quality stage.
        (append.source_document.natural_audit as any).quality_pass = true;
        const appendReady = await naturalGridCandidate(append.source_document);
        const duplicate = await fixture('saitama', 5000, 'merchant-one');
        (duplicate.source_document.natural_audit as any).quality_pass = true;
        await assert.rejects(apply(await naturalGridCandidate(duplicate.source_document), { appendRegion: true }), /已归属其他地区/);
        const overlap = await fixture('chiba', 0, 'merchant-overlap');
        (overlap.source_document.natural_audit as any).quality_pass = true;
        await assert.rejects(apply(await naturalGridCandidate(overlap.source_document), { appendRegion: true }), /空间重叠/);
        const second = await apply(appendReady, { appendRegion: true });
        assert.notEqual(second.id, first.id);
        assert.equal((await db.query('SELECT count(*)::int AS n FROM bd_work_grid')).rows[0].n, 4);
        const replacement = await fixture('tokyo26', 10000, 'merchant-three');
        (replacement.source_document.natural_audit as any).quality_pass = true;
        const ready = await naturalGridCandidate(replacement.source_document), grid = (await db.query('SELECT id FROM bd_work_grid ORDER BY id LIMIT 1')).rows[0].id;
        await db.query('UPDATE bd_work_grid SET progress=1 WHERE id=$1', [grid]);
        await assert.rejects(apply(ready, { replace: true }), /网格作业/);
        await db.query('UPDATE bd_work_grid SET progress=0 WHERE id=$1', [grid]);
        await db.query('UPDATE bd_work_grid SET assignee_id=$1 WHERE id=$2', [actor, grid]);
        await assert.rejects(apply(ready, { replace: true }), /网格作业/);
        await db.query('UPDATE bd_work_grid SET assignee_id=NULL WHERE id=$1', [grid]);
        const main = (await db.query("INSERT INTO bd_main_task(title,status,progress,created_by,created_at,revision) VALUES('natural-import-history','active',0,$1,now(),0) RETURNING id", [actor])).rows[0].id;
        const region = (await db.query("INSERT INTO bd_region(release_id,source_id,name,geometry,area_km2,store_count,review,state,revision,updated_at) VALUES($1,'natural-history-region','history',$2,1,1,'[]','ready',0,now()) RETURNING id", [first.id, JSON.stringify(candidate.features[0].geometry)])).rows[0].id;
        const regionTask = (await db.query("INSERT INTO bd_region_task(main_task_id,region_id,snapshot,status,progress,revision,created_at) VALUES($1,$2,'{}','pending',0,0,now()) RETURNING id", [main, region])).rows[0].id;
        try {
            await assert.rejects(apply(ready, { replace: true }), /巡店批次任务引用/);
        }
        finally {
            await db.query('DELETE FROM bd_region_task WHERE id=$1', [regionTask]);
            await db.query('DELETE FROM bd_main_task WHERE id=$1', [main]);
            await db.query('DELETE FROM bd_region WHERE id=$1', [region]);
        }
        // A failure after clearing assets must restore them and keep historical release rows.
        const original = db.transaction.bind(db);
        await assert.rejects(original(async (client) => { await importNaturalCandidate(client, actor!, ready, { replace: true }); throw Error('forced rollback'); }), /forced rollback/);
        assert.equal((await db.query('SELECT count(*)::int AS n FROM bd_work_grid')).rows[0].n, 4);
        assert.equal((await db.query('SELECT count(*)::int AS n FROM bd_zone_release')).rows[0].n, 2);
        await apply(ready, { replace: true });
        assert.equal((await db.query('SELECT count(*)::int AS n FROM bd_zone_release')).rows[0].n, 3);
        assert.equal((await db.query('SELECT count(*)::int AS n FROM bd_work_grid')).rows[0].n, 2);
        assert.equal((await db.query('SELECT source_id FROM bd_grid_store')).rows[0].source_id, 'merchant-three');
        assert.equal((await db.query('SELECT count(*)::int AS n FROM formal_records')).rows[0].n, 0);
    }
    finally {
        if (actor) {
            await db.query('DELETE FROM bd_region_task WHERE main_task_id IN (SELECT id FROM bd_main_task WHERE created_by=$1)', [actor]);
            await db.query('DELETE FROM bd_main_task WHERE created_by=$1', [actor]);
        }
        await db.query('DELETE FROM bd_grid_store');
        await db.query('DELETE FROM bd_work_grid');
        await db.query('DELETE FROM bd_region');
        await db.query('DELETE FROM bd_zone_release');
        await db.query("DELETE FROM bd_log WHERE actor='natural-import-admin'");
        if (actor)
            await db.query('DELETE FROM users WHERE id=$1', [actor]);
        await db.onModuleDestroy();
    }
});
test('natural import CLI validates offline without a database and rejects implicit replacement and remote apply', async () => {
    const { mkdtemp, writeFile, rm } = await import('node:fs/promises'), { join } = await import('node:path'), { tmpdir } = await import('node:os'), { spawnSync } = await import('node:child_process');
    const directory = await mkdtemp(join(tmpdir(), 'natural-import-cli-')), input = join(directory, 'candidate.json');
    try {
        await writeFile(input, JSON.stringify(await fixture()));
        const run = (extra: string[]) => spawnSync(process.execPath, ['--import', 'tsx', 'scripts/import-natural-grids.mts', '--input', input, ...extra], { encoding: 'utf8', env: { ...process.env, NEXT_DATABASE_URL: 'postgresql://u:unused@remote/db_refactor_p1' } });
        const dry = run([]);
        assert.equal(dry.status, 0, dry.stderr);
        assert.equal(JSON.parse(dry.stdout).databaseTouched, false);
        assert.equal(JSON.parse(dry.stdout).applied, false);
        assert.equal(JSON.parse(dry.stdout).qualityPass, false);
        assert.notEqual(run(['--replace']).status, 0);
        assert.notEqual(run(['--apply']).status, 0);
        const remote = run(['--apply', '--admin-user', 'admin']);
        assert.notEqual(remote.status, 0);
        assert.match(remote.stderr, /explicit local refactor/);
    }
    finally {
        await rm(directory, { recursive: true, force: true });
    }
});
