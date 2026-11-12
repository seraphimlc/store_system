import {spawnLegacyPython} from './legacy-python.mjs';
/** Synthetic fixed ward preview comparison. Python is an oracle only; no database/network. */
import { spawnSync } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
import { previewWardZones } from '../packages/domain/src/zone-preview';
import { arena, gridToMetric, normalizedWkb, type GridGeometry } from '../packages/domain/src/grid-geometry';
const oracle = spawnLegacyPython( ['legacy/scripts/zone-preview-oracle.py'], { encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 });
assert.equal(oracle.status, 0, oracle.stderr);
const cases = JSON.parse(oracle.stdout), results: any[] = [];
async function equal(a: any, b: any, path: string): Promise<void> {
    if (typeof a === 'number' && typeof b === 'number') {
        assert.ok(Math.abs(a - b) < 1e-9, path + ': ' + a + ' vs ' + b);
        return;
    }
    if (a && b && typeof a === 'object' && typeof b === 'object') {
        if ((a.type === 'Polygon' || a.type === 'MultiPolygon') && (b.type === 'Polygon' || b.type === 'MultiPolygon')) {
            await arena(s => { const read = (g: GridGeometry) => s.own(s.g.GEOSGeom_setPrecision(s.read(gridToMetric(g)), .001, 0)); assert.equal(normalizedWkb(s, read(a)), normalizedWkb(s, read(b)), path); });
            return;
        }
        assert.deepEqual(Object.keys(a).sort(), Object.keys(b).sort(), path + ':keys');
        for (const key of Object.keys(a))
            await equal(a[key], b[key], path + '.' + key);
        return;
    }
    assert.deepEqual(a, b, path);
}
for (const row of cases) {
    if (row.error) {
        await assert.rejects(previewWardZones(row.snapshot, row.targetStores, row.maxAreaKm2), error => (error as Error).message === row.error);
        results.push({ name: row.name, error: row.error });
        continue;
    }
    const result = await previewWardZones(row.snapshot, row.targetStores, row.maxAreaKm2);
    await equal(result, row.expected, row.name);
    results.push({ name: row.name, zones: result.features.length, grids: result.grids.length, pending: result.pending_grids.length, eligible: result.summary.eligible, membersAndIdsEqual: true });
}
await mkdir('data/refactor', { recursive: true });
await writeFile('data/refactor/zone-preview-parity.json', JSON.stringify({ ok: true, databaseTouched: false, network: false, cases: results }, null, 2));
console.log(JSON.stringify(results));
