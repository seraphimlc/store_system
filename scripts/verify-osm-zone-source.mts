import {spawnLegacyPython} from './legacy-python.mjs';
/** Compare raw cached OSM extraction to legacy fixed-input helpers; no network/database. */
import { spawnSync } from 'node:child_process';
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { prepareOsmScopeGeometry, osmRelationPolygon, osmExclusionFaces, osmWardRelations } from '../packages/domain/src/osm-zone-source';
import { arena, gridToMetric, normalizedWkb, type GridGeometry } from '../packages/domain/src/grid-geometry';
const processResult = spawnLegacyPython( ['legacy/scripts/osm-zone-source-oracle.py'], { encoding: 'utf8', maxBuffer: 16e6 });
assert.equal(processResult.status, 0, processResult.stderr);
const row = JSON.parse(processResult.stdout);
async function geometry(a: GridGeometry, b: GridGeometry, label: string) { await arena(s => { const read = (g: GridGeometry) => s.own(s.g.GEOSGeom_setPrecision(s.read(gridToMetric(g)), .001, 0)); assert.equal(normalizedWkb(s, read(a)), normalizedWkb(s, read(b)), label); }); }
const actual = await prepareOsmScopeGeometry(row.input);
assert.equal(actual.wards.length, 23);
assert.equal(osmWardRelations(row.input.boundaries.elements).has('27100'), false);
await geometry(actual.boundary, row.expected.boundary, 'whole scope');
assert.deepEqual(actual.roads, row.expected.roads);
assert.deepEqual(actual.coastlines, row.expected.coastlines);
assert.equal(actual.excluded.length, row.expected.excluded.length);
for (let i = 0; i < actual.excluded.length; i++)
    await geometry(actual.excluded[i], row.expected.excluded[i], 'excluded-' + i);
await geometry(await osmRelationPolygon(row.relation), row.relationExpected, 'split outer relation with hole');
const water = await osmExclusionFaces(row.water);
assert.equal(water.length, row.waterExpected.length);
for (let i = 0; i < water.length; i++)
    await geometry(water[i], row.waterExpected[i], 'tagged-water-' + i);
const missing = structuredClone(row.input);
missing.boundaries.elements = missing.boundaries.elements.filter((item: any) => item.id !== 100);
await assert.rejects(prepareOsmScopeGeometry(missing), /23区行政边界不完整/);
const noRoads = structuredClone(row.input);
noRoads.wards['13101'].elements = [];
await assert.rejects(prepareOsmScopeGeometry(noRoads), /道路缺失/);
const incomplete = structuredClone(row.input);
incomplete.coastlines.remark = 'timeout';
await assert.rejects(prepareOsmScopeGeometry(incomplete), /不完整结果/);
const evidence = { ok: true, network: false, databaseTouched: false, wards: 23, roads: actual.roads.length, relationHolesEqual: true, waterTagsEqual: true, completeScopeEqual: true, wrongCityRejected: true, missingWardRejected: true, missingRoadRejected: true, incompleteResponseRejected: true };
await mkdir('data/refactor', { recursive: true });
await writeFile('data/refactor/osm-zone-source-parity.json', JSON.stringify(evidence, null, 2));
console.log(JSON.stringify(evidence));
