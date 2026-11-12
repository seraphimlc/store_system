import {spawnLegacyPython} from './legacy-python.mjs';
/** Full scope-preview comparison on fixed synthetic documents; no database or providers. */
import { spawnSync } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
import { previewScopeZones } from '../packages/domain/src/zone-scope';
import { arena, gridToMetric, normalizedWkb, type GridGeometry } from '../packages/domain/src/grid-geometry';
const oracle = spawnLegacyPython( ['legacy/scripts/zone-scope-oracle.py'], { encoding: 'utf8', maxBuffer: 32 * 1024 * 1024 });
assert.equal(oracle.status, 0, oracle.stderr);
const rows = JSON.parse(oracle.stdout), results: any[] = [];
async function equal(a: any, b: any, path: string): Promise<void> {
    if (typeof a === 'number' && typeof b === 'number') {
        assert.ok(Math.abs(a - b) < 1e-9, path + ': ' + a + ' vs ' + b);
        return;
    }
    if (a && b && typeof a === 'object' && typeof b === 'object') {
        if (['Polygon', 'MultiPolygon'].includes(a.type) && ['Polygon', 'MultiPolygon'].includes(b.type)) {
            await arena(s => { const p = (value: GridGeometry) => s.own(s.g.GEOSGeom_setPrecision(s.read(gridToMetric(value)), .001, 0)); assert.equal(normalizedWkb(s, p(a)), normalizedWkb(s, p(b)), path); });
            return;
        }
        assert.deepEqual(Object.keys(a).sort(), Object.keys(b).sort(), path + ':keys');
        for (const k of Object.keys(a))
            await equal(a[k], b[k], path + '.' + k);
        return;
    }
    assert.deepEqual(a, b, path);
}
for (const row of rows) {
    const o = row.balance, options = { balance: o ? { targetAreaKm2: o.target_area, targetStores: o.target_stores, areaWeight: o.area_weight, loadMin: o.load_min, loadMax: o.load_max } : null, includeMemberships: row.includeMemberships, generationRules: !!row.generationRules,riverFaces:row.riverFaces };
    if (row.error) {
        await assert.rejects(previewScopeZones(row.document, row.targetStores, row.maxAreaKm2, options), error => (error as Error).message === row.error);
        results.push({ name: row.name, error: row.error });
        continue;
    }
    const result = await previewScopeZones(row.document, row.targetStores, row.maxAreaKm2, options);
    await equal(result, row.expected, row.name);
    results.push({ name: row.name, regions: result.features.length, tasks: result.grids.length, confirmationReady: result.summary.confirmation_ready, geometryPass: result.audit.geometric_pass, merchantPass: result.audit.merchant_pass, fullContractEqual: true });
}
await mkdir('data/refactor', { recursive: true });
await writeFile('data/refactor/zone-scope-parity.json', JSON.stringify({ ok: true, network: false, databaseTouched: false, includesGenerationRules: true, cases: results }, null, 2));
console.log(JSON.stringify(results));
