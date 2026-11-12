import {spawnLegacyPython} from './legacy-python.mjs';
/** Fixed offline legacy comparison for natural preparation and audited candidates. */
import { spawnSync } from 'node:child_process';
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { prepareNaturalGrids, naturalBoundaryKind, naturalGridCandidate } from '../packages/domain/src/natural-grids';
import { arena, gridToMetric, normalizedWkb, type GridGeometry } from '../packages/domain/src/grid-geometry';
const oracle = spawnLegacyPython( ['legacy/scripts/natural-grid-oracle.py'], { encoding: 'utf8', maxBuffer: 16e6 });
assert.equal(oracle.status, 0, oracle.stderr);
const rows = JSON.parse(oracle.stdout), results: any[] = [];
for (const row of rows.classifications)
    assert.equal(naturalBoundaryKind(row.tags, row.length === null ? undefined : row.length), row.expected, JSON.stringify(row));
async function geometry(a: any, b: any, label: string, metric = true) { if (['Polygon', 'MultiPolygon'].includes(a.type) && ['Polygon', 'MultiPolygon'].includes(b.type)) {
    await arena(s => { const read = (g: GridGeometry) => s.own(s.g.GEOSGeom_setPrecision(s.read(metric ? g : gridToMetric(g)), .001, 0)); assert.equal(normalizedWkb(s, read(a)), normalizedWkb(s, read(b)), label); });
}
else
    await arena(s => assert.ok(s.g.GEOSEquals(s.read(a), s.read(b)) === 1, label)); }
for (const row of rows.cases) {
    if (row.error) {
        await assert.rejects(prepareNaturalGrids(row.input), error => (error as Error).message === row.error);
        results.push({ name: row.name, error: row.error });
        continue;
    }
    const actual = await prepareNaturalGrids({ ...row.input, referenceSnapshot: row.expected }), expected = row.expected;
    assert.equal(actual.road_cells.length, expected.road_cells.length, row.name);
    for (let i = 0; i < actual.road_cells.length; i++) {
        const a = actual.road_cells[i], b = expected.road_cells[i];
        assert.equal(a.id, b.id, row.name + ':id');
        assert.ok(Math.abs(a.area_km2 - b.area_km2) < 1e-10);
        await geometry(a.metric_geometry, b.metric_geometry, row.name + ':face-' + i);
    }
    await geometry(actual.work_area, expected.work_area, row.name + ':work', false);
    for (const key of ['retained_boundaries', 'skipped', 'additional_boundaries', 'geometric_pass', 'main_road_classes', 'junction_links', 'grade_policy', 'tertiary_evidence'])
        assert.deepEqual((actual.natural_audit as any)[key], expected.natural_audit[key], row.name + ':' + key);
    for (const key of ['overlap_m2', 'uncovered_m2', 'outside_m2'])
        assert.ok(Math.abs((actual.natural_audit as any)[key] - expected.natural_audit[key]) < 1e-8, row.name + ':' + key);
    for (const key of ['cuts', 'dangles', 'invalid_rings', 'uncovered', 'outside'])
        await geometry((actual.natural_audit as any)[key], expected.natural_audit[key], row.name + ':' + key);
    const candidate = await naturalGridCandidate(actual);
    assert.equal(candidate.status, 'candidate');
    assert.equal(candidate.applied, false);
    assert.equal(candidate.audit.merchant_pass, false);
    results.push({ name: row.name, faces: actual.road_cells.length, geometryEqual: true, legacyIdsPreserved: true, auditEqual: true, candidateStillUnbound: true });
}
await mkdir('data/refactor', { recursive: true });
await writeFile('data/refactor/natural-grid-parity.json', JSON.stringify({ ok: true, network: false, databaseTouched: false, classifications: rows.classifications.length, cases: results }, null, 2));
console.log(JSON.stringify(results));
