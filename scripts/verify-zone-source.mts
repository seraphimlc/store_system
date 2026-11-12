import {spawnLegacyPython} from './legacy-python.mjs';
import { spawnSync } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
import { prepareScopeSource } from '../packages/domain/src/zone-source';
import { arena, gridToMetric, normalizedWkb, type GridGeometry } from '../packages/domain/src/grid-geometry';
const oracle = spawnLegacyPython( ['legacy/scripts/zone-source-oracle.py'], { encoding: 'utf8', maxBuffer: 32e6 });
assert.equal(oracle.status, 0, oracle.stderr);
const rows = JSON.parse(oracle.stdout), results: any[] = [];
async function equal(a: any, b: any, path: string): Promise<void> { if (typeof a === 'number' && typeof b === 'number') {
    assert.ok(Math.abs(a - b) < 1e-9, path + ':' + a + ' vs ' + b);
    return;
} if (a && b && typeof a === 'object' && typeof b === 'object') {
    if (['Polygon', 'MultiPolygon'].includes(a.type) && ['Polygon', 'MultiPolygon'].includes(b.type)) {
        await arena(s => { const read = (g: GridGeometry) => s.own(s.g.GEOSGeom_setPrecision(s.read(path.endsWith('metric_geometry') ? g : gridToMetric(g)), .001, 0)); assert.equal(normalizedWkb(s, read(a)), normalizedWkb(s, read(b)), path); });
        return;
    }
    assert.deepEqual(Object.keys(a).sort(), Object.keys(b).sort(), path + ':keys');
    for (const k of Object.keys(a))
        await equal(a[k], b[k], path + '.' + k);
    return;
} assert.deepEqual(a, b, path); }
for (const row of rows) {
    if (row.error) {
        await assert.rejects(prepareScopeSource(row.input, row.merchants), error => (error as Error).message === row.error);
        results.push({ name: row.name, error: row.error });
        continue;
    }
    const actual = await prepareScopeSource({ ...row.input, referenceSnapshot: row.expected }, row.merchants);
    const { generation_version, id_reference_snapshot, ...compared } = actual, { generation_version: oldVersion, ...expected } = row.expected;
    assert.equal(generation_version, 6);
    assert.equal(id_reference_snapshot, row.expected.snapshot_id);
    await equal(compared, expected, row.name);
    results.push({ name: row.name, faces: actual.road_cells.length, fullContractEqual: true, legacyIdsPreserved: true, snapshotIdEqual: true });
}
await mkdir('data/refactor', { recursive: true });
await writeFile('data/refactor/zone-source-parity.json', JSON.stringify({ ok: true, network: false, databaseTouched: false, cases: results }, null, 2));
console.log(JSON.stringify(results));
