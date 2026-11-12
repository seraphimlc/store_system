import {spawnLegacyPython} from './legacy-python.mjs';
import { spawnSync } from 'node:child_process';
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { consolidateMetricZones } from '../packages/domain/src/zone-rules';
const oracle = spawnLegacyPython( ['legacy/scripts/zone-rules-oracle.py'], { encoding: 'utf8', maxBuffer: 16e6 });
assert.equal(oracle.status, 0, oracle.stderr);
const cases = JSON.parse(oracle.stdout), results: any[] = [];
for (const row of cases) {
    const run = () => consolidateMetricZones(row.geometries, row.counts, row.topology, row.members, {}, row.areaKm2, row.rivers, row.maxRiverWidthM);
    if (row.error) {
        await assert.rejects(run, error => (error as Error).message === row.error);
        results.push({ name: row.name, error: row.error });
    }
    else {
        assert.deepEqual(await run(), row.expected, row.name);
        results.push({ name: row.name, fullContractEqual: true });
    }
}
await mkdir('data/refactor', { recursive: true });
await writeFile('data/refactor/zone-rules-parity.json', JSON.stringify({ ok: true, network: false, databaseTouched: false, cases: results }, null, 2));
console.log(JSON.stringify(results));
