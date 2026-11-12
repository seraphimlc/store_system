import test from 'node:test';
import assert from 'node:assert/strict';
import { prepareZoneSnapshot } from '../src/zone-snapshot';
import { gridPointToGeographic, type GridGeometry } from '../src/grid-geometry';
import { previewWardZones, type ZonePlanningSnapshot } from '../src/zone-preview';
const point = (x: number, y: number) => gridPointToGeographic([x, y]);
const boundary: GridGeometry = { type: 'Polygon', coordinates: [[point(-10000, -40000), point(-6000, -40000), point(-6000, -36000), point(-10000, -36000), point(-10000, -40000)]] };
async function fixture() { return prepareZoneSnapshot({ code: '13104', boundary, roads: [...[-9000, -8000, -7000].map(x => ({ type: 'LineString' as const, coordinates: [point(x, -40010), point(x, -35990)] })), ...[-39000, -38000, -37000].map(y => ({ type: 'LineString' as const, coordinates: [point(-10010, y), point(-5990, y)] }))], excluded: [], merchants: Array.from({ length: 16 }, (_, i) => Array.from({ length: 4 }, (_, k) => point(-9900 + (i % 4) * 1000 + k * 100, -39900 + Math.floor(i / 4) * 1000))).flat(), capturedAt: '2026-11-11T00:00:00Z', source: 'synthetic' }); }
test('ward preview keeps whole grid identities, area/store conservation and soft requested zone counts', async () => {
    const snapshot = await fixture(), before = JSON.stringify(snapshot), result = await previewWardZones(snapshot, 16, 25);
    assert.equal(result.features.length, 4);
    assert.equal(result.summary.eligible, true);
    assert.equal(result.preview_only, true);
    assert.equal(result.grids.length, 16);
    assert.equal(result.features.reduce((n, f) => n + f.properties.store_count, 0), 64);
    assert.equal(result.features.reduce((n, f) => n + f.properties.grid_count, 0), 16);
    assert.ok(result.summary.area_ratio <= 2 + 1e-5);
    assert.equal(new Set(result.grids.map(g => g.id)).size, 16);
    assert.ok(result.grids.every(g => result.features.some(z => z.id === g.properties.zone_id)));
    assert.equal(JSON.stringify(snapshot), before);
    assert.deepEqual(await previewWardZones(snapshot, 16, 25), result);
    const one = await previewWardZones(snapshot, 64, 25), capped = await previewWardZones(snapshot, 64, 4), many = await previewWardZones(snapshot, 1, 25);
    assert.equal(one.features.length, 1);
    assert.ok(capped.features.length >= 4);
    assert.ok(capped.features.every(f => f.properties.area_km2 <= 4 + 1e-5));
    assert.equal(many.features.length, 16);
    assert.ok(many.issues.some(i => i.code === 'zone_count' && i.severity === 'info'));
    assert.equal(many.summary.eligible, true);
});
test('ward preview rejects invalid parameters and scope substitution, preserves pending and no-store diagnostics', async () => {
    const snapshot = await fixture();
    for (const [stores, area] of [[0, 25], [100001, 25], [20, 1], [20, NaN], [true, 25]])
        await assert.rejects(previewWardZones(snapshot, stores as number, area as number), /参数超出范围/);
    const empty = { ...snapshot, store_count: 0, grids: snapshot.grids.map(g => ({ ...g, store_count: 0 })) };
    const noStores = await previewWardZones(empty, 400, 25);
    assert.equal(noStores.summary.eligible, false);
    assert.ok(noStores.issues.some(i => i.code === 'no_stores'));
    const pending = { ...snapshot, grids: [...snapshot.grids, { ...snapshot.grids[0], id: 'pending', area_km2: .25, store_count: 3 }] };
    const result = await previewWardZones(pending, 16, 25);
    assert.equal(result.pending_grids.length, 1);
    assert.equal(result.summary.pending_store_count, 3);
    assert.equal(result.grids.length, 16);
    await assert.rejects(previewWardZones({ ...snapshot, grids: [] }, 10, 25), /没有可用任务网格/);
    await assert.rejects(previewWardZones({ ...snapshot, ward_code: 'tokyo23' }, 10, 25), /整体快照不完整/);
    await assert.rejects(previewWardZones({ ...snapshot, ward_code: 'tokyo23', scope_ward_count: 23 } as ZonePlanningSnapshot, 10, 25), /整体覆盖与均衡算法/);
});
test('ward preview CLI accepts generated snapshots and writes only explicit immutable preview files', async () => {
    const { mkdtemp, writeFile, readFile, rm } = await import('node:fs/promises'), { tmpdir } = await import('node:os'), { join } = await import('node:path'), { spawnSync } = await import('node:child_process');
    const dir = await mkdtemp(join(tmpdir(), 'zone-preview-test-')), input = join(dir, 'snapshot.json'), output = join(dir, 'preview.json');
    const run = (extra: string[]) => spawnSync(process.execPath, ['--import', 'tsx', 'scripts/preview-zone-snapshot.mts', '--input', input, '--stores', '16', '--max-area-km2', '25', ...extra], { encoding: 'utf8' });
    try {
        await writeFile(input, JSON.stringify(await fixture()));
        const source = await readFile(input, 'utf8'), dry = run([]);
        assert.equal(dry.status, 0, dry.stderr);
        assert.equal(JSON.parse(dry.stdout).previewOnly, true);
        await assert.rejects(readFile(output), { code: 'ENOENT' });
        const saved = run(['--write', '--output', output]);
        assert.equal(saved.status, 0, saved.stderr);
        const frozen = await readFile(output, 'utf8');
        assert.equal(JSON.parse(frozen).summary.zone_count, 4);
        assert.notEqual(run(['--write', '--output', output]).status, 0);
        assert.equal(await readFile(output, 'utf8'), frozen);
        assert.notEqual(run(['--write', '--output', input]).status, 0);
        assert.equal(await readFile(input, 'utf8'), source);
        assert.notEqual(run(['--output', output]).status, 0);
    }
    finally {
        await rm(dir, { recursive: true, force: true });
    }
});
