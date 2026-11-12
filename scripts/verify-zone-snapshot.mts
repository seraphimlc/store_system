import {spawnLegacyPython} from './legacy-python.mjs';
/** Fixed offline Python/TypeScript parity check. No database or network. */
import { spawnSync } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
import { prepareZoneSnapshot } from '../packages/domain/src/zone-snapshot';
import { arena, gridToMetric, normalizedWkb, type GridGeometry } from '../packages/domain/src/grid-geometry';
const processResult = spawnLegacyPython( ['legacy/scripts/zone-snapshot-oracle.py'], { encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 });
if (processResult.status !== 0)
    throw Error('Offline snapshot oracle failed');
const cases = JSON.parse(processResult.stdout), results: any[] = [];
for (const row of cases) {
    if (row.error) {
        await assert.rejects(prepareZoneSnapshot(row.input), error => (error as Error).message === row.error);
        results.push({ name: row.name, error: row.error });
        continue;
    }
    const expected = row.expected, actual = await prepareZoneSnapshot({ ...row.input, referenceSnapshot: expected });
    assert.equal(actual.generation_version, 4);
    for (const key of ['schema_version', 'ward_code', 'ward_name', 'captured_at', 'source', 'coastline_applied', 'store_count', 'issues', 'road_count'])
        assert.deepEqual((actual as any)[key], expected[key], row.name + ':' + key);
    for (const key of ['area_km2', 'excluded_area_km2'])
        assert.ok(Math.abs((actual as any)[key] - expected[key]) < 1e-10, row.name + ':' + key);
    for (const key of ['boundary', 'work_area'] as const)
        await arena(s => { const metric = (value: GridGeometry) => s.own(s.g.GEOSGeom_setPrecision(s.read(gridToMetric(value)), .001, 0)); assert.equal(normalizedWkb(s, metric(actual[key] as GridGeometry)), normalizedWkb(s, metric(expected[key])), row.name + ':' + key); });
    assert.equal(actual.grids.length, expected.grids.length, row.name + ':grids');
    for (let i = 0; i < actual.grids.length; i++) {
        const a = actual.grids[i], b = expected.grids[i];
        assert.equal(a.id, b.id, row.name + ':legacy-id:' + i);
        assert.equal(a.store_count, b.store_count, row.name + ':stores:' + i);
        assert.ok(Math.abs(a.area_km2 - b.area_km2) < 1e-10, row.name + ':area:' + i);
        await arena(s => { const metric = (value: GridGeometry) => s.own(s.g.GEOSGeom_setPrecision(s.read(gridToMetric(value)), .001, 0)), ap = metric(a.geometry as GridGeometry), bp = metric(b.geometry); assert.equal(normalizedWkb(s, ap), normalizedWkb(s, bp), row.name + ':geometry:' + i); });
    }
    results.push({ name: row.name, grids: actual.grids.length, legacyIdsPreserved: true, generationVersion: actual.generation_version, contentIdUsesNewSerialization: true });
}
await mkdir('data/refactor', { recursive: true });
await writeFile('data/refactor/zone-snapshot-parity.json', JSON.stringify({ ok: true, network: false, databaseTouched: false, cases: results }, null, 2));
console.log(JSON.stringify(results));
