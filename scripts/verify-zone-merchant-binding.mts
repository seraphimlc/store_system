import {spawnLegacyPython} from './legacy-python.mjs';
/** Fixed binding-row comparison only; no database or actual merchant reads. */
import { spawnSync } from 'node:child_process';
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { bindMerchantRows } from '../packages/domain/src/zone-merchant-binding';
const oracle = spawnLegacyPython( ['legacy/scripts/zone-merchant-binding-oracle.py'], { encoding: 'utf8', maxBuffer: 32e6 });
assert.equal(oracle.status, 0, oracle.stderr);
const rows = JSON.parse(oracle.stdout), results: any[] = [];
for (const row of rows) {
    const before = JSON.stringify(row.document), actual = await bindMerchantRows(row.document, row.rows, row.sourceInfo);
    assert.deepEqual(actual, row.expected, row.name);
    assert.equal(JSON.stringify(row.document), before);
    results.push({ name: row.name, rows: row.rows.length, fullContractEqual: true, snapshotIdEqual: true, inputGeometryUnchanged: true });
}
await mkdir('data/refactor', { recursive: true });
await writeFile('data/refactor/zone-merchant-binding-parity.json', JSON.stringify({ ok: true, databaseTouched: false, network: false, cases: results }, null, 2));
console.log(JSON.stringify(results));
