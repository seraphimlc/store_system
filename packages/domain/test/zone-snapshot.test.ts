import test from 'node:test';
import assert from 'node:assert/strict';
import { prepareZoneSnapshot, type ZoneSnapshotInput } from '../src/zone-snapshot';
import { gridPointToGeographic, type GridGeometry, type GridPoint } from '../src/grid-geometry';
const point = (x: number, y: number) => gridPointToGeographic([x, y]);
function rectangle(x: number, y: number, width: number, height: number): GridGeometry { return { type: 'Polygon', coordinates: [[[x, y], [x + width, y], [x + width, y + height], [x, y + height], [x, y]].map(p => point(p[0], p[1]))] }; }
const line = (x1: number, y1: number, x2: number, y2: number) => ({ type: 'LineString' as const, coordinates: [point(x1, y1), point(x2, y2)] });
const base: ZoneSnapshotInput = { code: '13104', boundary: rectangle(10000, -40000, 2000, 2000), roads: [line(11000, -40010, 11000, -37990), line(9990, -39000, 12010, -39000)], excluded: [], merchants: [point(10100, -39900), point(11100, -39900), point(11900, -38100)], capturedAt: '2026-11-11T00:00:00Z', source: 'fixed-no-provider' };
test('road snapshots conserve geometry and merchants, preserve reference IDs and keep density out of grouping', async () => {
    const a = await prepareZoneSnapshot(base);
    assert.equal(a.grids.length, 4);
    assert.equal(a.store_count, 3);
    assert.equal(a.area_km2, 4);
    assert.deepEqual(a.issues, []);
    assert.equal(a.generation_version, 4);
    assert.deepEqual(await prepareZoneSnapshot(base), a);
    const ids = a.grids.map(g => g.id), more = await prepareZoneSnapshot({ ...base, merchants: [...base.merchants, ...base.merchants] });
    assert.deepEqual(more.grids.map(g => g.id), ids);
    assert.equal(more.store_count, 6);
    assert.notEqual(more.snapshot_id, a.snapshot_id);
    const reference = { ...a, grids: a.grids.map((g, i) => ({ ...g, id: 'legacy-grid-' + i })) }, mapped = await prepareZoneSnapshot({ ...base, referenceSnapshot: reference });
    assert.deepEqual(mapped.grids.map(g => g.id), reference.grids.map(g => g.id));
    assert.equal(mapped.id_reference_snapshot, reference.snapshot_id);
    await assert.rejects(prepareZoneSnapshot({ ...base, referenceSnapshot: { ...reference, ward_code: '13101' } }), /行政区不一致/);
    await assert.rejects(prepareZoneSnapshot({ ...base, referenceSnapshot: { ...reference, grids: [reference.grids[0], reference.grids[0]] } }), /重复网格/);
});
test('snapshot masks, coastline direction, draft issues and invalid inputs follow legacy geometry rules', async () => {
    const coastBase = { ...base, boundary: rectangle(10000, -40000, 2000, 1000), roads: [], merchants: [point(10500, -39500), point(11500, -39500)] as GridPoint[] };
    const west = await prepareZoneSnapshot({ ...coastBase, coastlines: [line(11000, -40100, 11000, -38900)] }), east = await prepareZoneSnapshot({ ...coastBase, coastlines: [line(11000, -38900, 11000, -40100)] });
    assert.equal(west.store_count, 1);
    assert.equal(east.store_count, 1);
    assert.equal(west.area_km2, 1);
    assert.equal(east.area_km2, 1);
    assert.notEqual(west.grids[0].id, east.grids[0].id);
    const large = await prepareZoneSnapshot({ ...base, roads: [] });
    assert.deepEqual(large.issues, [{ code: 'grid_area', count: 1 }]);
    await assert.rejects(prepareZoneSnapshot({ ...base, excluded: [rectangle(9900, -40100, 2200, 2200)] }), /没有可用道路街块/);
    await assert.rejects(prepareZoneSnapshot({ ...base, code: '../snapshot' }), /请选择东京23区/);
    await assert.rejects(prepareZoneSnapshot({ ...base, merchants: [[181, 0]] }), /坐标无效/);
});
test('snapshot CLI stays offline, requires explicit writes and never overwrites source or reference artifacts', async () => {
    const { mkdtemp, readFile, writeFile, rm } = await import('node:fs/promises'), { tmpdir } = await import('node:os'), { join } = await import('node:path'), { spawnSync } = await import('node:child_process');
    const directory = await mkdtemp(join(tmpdir(), 'zone-snapshot-test-')), source = join(directory, 'input.json'), reference = join(directory, 'reference.json'), output = join(directory, 'snapshot.json');
    const run = (args: string[]) => spawnSync(process.execPath, ['--import', 'tsx', 'scripts/prepare-zone-snapshot.mts', ...args], { encoding: 'utf8' });
    try {
        await writeFile(source, JSON.stringify(base));
        const before = await readFile(source, 'utf8');
        const dry = run(['--input', source]);
        assert.equal(dry.status, 0, dry.stderr);
        assert.equal(JSON.parse(dry.stdout).mode, 'dry-run');
        await assert.rejects(readFile(output), { code: 'ENOENT' });
        const generated = await prepareZoneSnapshot(base), old = { ...generated, grids: generated.grids.map((g, i) => ({ ...g, id: 'existing-' + i })) };
        await writeFile(reference, JSON.stringify(old));
        const saved = run(['--input', source, '--reference', reference, '--write', '--output', output]);
        assert.equal(saved.status, 0, saved.stderr);
        const frozen = await readFile(output, 'utf8');
        assert.deepEqual(JSON.parse(frozen).grids.map((g: any) => g.id), old.grids.map(g => g.id));
        assert.notEqual(run(['--input', source, '--reference', reference, '--write', '--output', output]).status, 0);
        assert.equal(await readFile(output, 'utf8'), frozen);
        assert.notEqual(run(['--input', source, '--write', '--output', source]).status, 0);
        assert.equal(await readFile(source, 'utf8'), before);
        assert.notEqual(run(['--input', source, '--output', output]).status, 0);
    }
    finally {
        await rm(directory, { recursive: true, force: true });
    }
});
