import test from 'node:test';
import assert from 'node:assert/strict';
import { setTimeout as delay } from 'node:timers/promises';
import { prepareScopeSource, type ScopeSourceInput } from '../src/zone-source';
import { gridToGeographic, gridPointToGeographic, mergeMetricGrids, type GridPolygon } from '../src/grid-geometry';
import { previewScopeZones } from '../src/zone-scope';
const box = (x: number, y: number, w = 1000, h = 1000): GridPolygon => ({ type: 'Polygon', coordinates: [[[x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y]]] });
const input: ScopeSourceInput = { boundary: gridToGeographic(box(10000, -40000, 2000, 1000)), roads: [{ type: 'LineString', coordinates: [gridPointToGeographic([11000, -40100]), gridPointToGeographic([11000, -38900])] }], excluded: [], capturedAt: '2026-11-11T00:00:00Z', source: 'fixed-source' };
function merchant(id: string, x: number, y: number) { const [lon, lat] = gridPointToGeographic([x, y]); return { place_id: id, name: id, lon: String(lon), lat: String(lat) }; }
test('source preparation binds each unique merchant once, preserves all road faces and legacy identity references', async () => {
    const rows = [merchant('one', 10500, -39500), merchant('two', 11500, -39500), merchant('one', 10600, -39500), merchant('', 10600, -39500), { place_id: 'bad', lat: 'nan', lon: '139' }, merchant('outside', 14000, -39500)], before = JSON.stringify(rows), source = await prepareScopeSource(input, rows);
    assert.equal(source.road_cells.length, 2);
    assert.equal(source.merchant_audit.inside_work, 2);
    assert.equal(source.merchant_audit.source_duplicates, 1);
    assert.equal(source.merchant_audit.invalid_coordinates, 2);
    assert.deepEqual(source.road_cells.map(cell => cell.merchant_ids), [['one'], ['two']]);
    assert.equal(JSON.stringify(rows), before);
    assert.equal(source.generation_version, 6);
    assert.deepEqual(await prepareScopeSource({ ...input, referenceSnapshot: source }, rows), { ...source, id_reference_snapshot: source.snapshot_id });
    const preview = await previewScopeZones(source, 2, 2, { generationRules: true, includeMemberships: true });
    assert.equal(preview.audit.geometric_pass, true);
    assert.equal(preview.audit.merchant_pass, false, 'Duplicate/invalid source rows remain blockers');
    assert.equal(preview.audit.source_duplicates, 1);
    const valid = await prepareScopeSource(input, rows.slice(0, 2)), ready = await previewScopeZones(valid, 2, 2, { generationRules: true, includeMemberships: true });
    assert.equal(ready.audit.merchant_pass, true);
    assert.equal(ready.audit.all_cells_assigned_once, true);
    assert.equal(ready.features.reduce((n, g) => n + g.properties.store_count, 0), 2);
});
test('source keeps excluded merchants as explicit reviews and retains GEOS resources across asynchronous rows/errors', async () => {
    const excluded = { ...input, excluded: [gridToGeographic(box(11400, -39600, 200, 200))] }, source = await prepareScopeSource(excluded, [merchant('water', 11500, -39500), merchant('land', 10500, -39500)]);
    assert.equal(source.merchant_audit.excluded_unreviewed, 1);
    assert.equal(source.store_count, 1);
    assert.equal(source.review_merchants[0].store_id, 'water');
    assert.equal(source.review_merchants[0].reason, 'excluded_terrain');
    async function* stream() { yield merchant('async-one', 10500, -39500); await delay(5); await mergeMetricGrids([box(0, 0)]); yield merchant('async-two', 11500, -39500); }
    const asyncSource = await prepareScopeSource(input, stream());
    assert.equal(asyncSource.store_count, 2);
    async function* failed() { yield merchant('partial', 10500, -39500); throw Error('stream interrupted'); }
    await assert.rejects(prepareScopeSource(input, failed()), /stream interrupted/);
    assert.equal((await prepareScopeSource(input, [])).road_cells.length, 2);
    await assert.rejects(prepareScopeSource({ ...input, excluded: [input.boundary] }, []), /没有完整道路基础面/);
    await assert.rejects(prepareScopeSource({ ...input, referenceSnapshot: { ...source, ward_code: '13104' } }, []), /投影或范围不一致/);
});
