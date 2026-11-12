import {spawnLegacyPython} from './legacy-python.mjs';
/** Development-only nonlinear balance parity; no database or live provider calls. */
import { spawnSync } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
import { balanceScopeFaces } from '../packages/domain/src/zone-balance';
import { arena, normalizedWkb } from '../packages/domain/src/grid-geometry';
const oracle = spawnLegacyPython( ['legacy/scripts/zone-balance-oracle.py'], { encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 });
assert.equal(oracle.status, 0, oracle.stderr);
const cases = JSON.parse(oracle.stdout), results: any[] = [];
for (const row of cases) {
    const o = row.options, actual = await balanceScopeFaces(row.geometries, row.counts, { targetAreaKm2: o.target_area, targetStores: o.target_stores, areaWeight: o.area_weight, loadMin: o.load_min, loadMax: o.load_max });
    assert.equal(actual.length, row.expected.length, row.name + ':groups');
    for (let i = 0; i < actual.length; i++) {
        const a = actual[i], b = row.expected[i];
        assert.deepEqual(a.members, b.members, row.name + ':members:' + i);
        assert.equal(a.stores, b.stores, row.name + ':stores:' + i);
        assert.ok(Math.abs(a.areaM2 - b.area) < .001, row.name + ':area:' + i);
        assert.ok(Math.abs(a.workload - b.load) < 1e-10, row.name + ':load:' + i);
        await arena(s => assert.equal(normalizedWkb(s, s.read(a.geometry)), normalizedWkb(s, s.read(b.geometry)), row.name + ':geometry:' + i));
    }
    results.push({ name: row.name, groups: actual.length, faces: row.geometries.length, membershipEqual: true, geometryEqual: true, storesEqual: true, loadEqual: true });
}
await mkdir('data/refactor', { recursive: true });
await writeFile('data/refactor/zone-balance-parity.json', JSON.stringify({ ok: true, network: false, databaseTouched: false, cases: results }, null, 2));
console.log(JSON.stringify(results));
