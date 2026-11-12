import { prepareNaturalGrids, naturalGridCandidate } from '../src/natural-grids';
import test from 'node:test';
import assert from 'node:assert/strict';
import { bindMerchantRows, type MerchantBindingDocument } from '../src/zone-merchant-binding';
import { gridToGeographic, gridPointToGeographic, type GridPolygon } from '../src/grid-geometry';
export function bindingFixture(): MerchantBindingDocument { const metric: GridPolygon = { type: 'Polygon', coordinates: [[[10000, -40000], [11000, -40000], [11000, -39000], [10000, -39000], [10000, -40000]]] }; return { metric_crs: 'EPSG:6677', captured_at: 'geometry-date', source: { geometry: 'synthetic' }, boundary: gridToGeographic(metric), work_area: gridToGeographic(metric), road_cells: [{ id: 'cell-one', metric_geometry: metric, store_count: 0, merchant_ids: [] }] }; }
const point = gridPointToGeographic([10500, -39500]);
test('merchant binding trims identities, preserves geometry and produces explicit audit/hash without mutating its source', async () => {
    const doc = bindingFixture(), before = JSON.stringify(doc), rows = [{ place_id: ' one ', name: '店', lat: point[1], lon: point[0] }, { place_id: 'one', lat: point[1], lon: point[0] }, { place_id: 'bad', lat: null, lon: null }], result = await bindMerchantRows(doc, rows, { table: 'public.synthetic', read_at: 'read-date' });
    assert.equal(result.store_count, 1);
    assert.deepEqual(result.road_cells[0].merchant_ids, ['one']);
    assert.equal(result.merchant_audit.source_duplicates, 1);
    assert.equal(result.merchant_audit.invalid_coordinates, 1);
    assert.equal(result.review_merchants[0].reason, 'invalid_coordinates');
    assert.equal(result.review_merchants[0].lat, null);
    assert.equal(result.geometry_captured_at, 'geometry-date');
    assert.equal(result.captured_at, 'read-date');
    assert.equal(result.source.merchants, 'PostgreSQL / public.synthetic');
    assert.equal(JSON.stringify(doc), before);
    const a = gridPointToGeographic([10500, -40100]), b = gridPointToGeographic([10500, -38900]);
    const natural = await prepareNaturalGrids({ boundary: doc.boundary, elements: [{ type: 'way', id: 1, tags: { highway: 'primary' }, geometry: [{ lon: a[0], lat: a[1] }, { lon: b[0], lat: b[1] }] }], excluded: [], coastlines: [], capturedAt: 'geometry-date', source: { geometry: 'synthetic' } });
    const boundNatural = await bindMerchantRows(natural, [rows[0]], { read_at: 'read-date' }), candidate = await naturalGridCandidate(boundNatural);
    assert.equal(candidate.audit.merchant_pass, true);
    assert.equal(candidate.features.reduce((n, f) => n + f.properties.store_count, 0), 1);
    assert.deepEqual(boundNatural.road_cells.map(c => c.metric_geometry), natural.road_cells.map(c => c.metric_geometry));
    assert.deepEqual(result.road_cells[0].metric_geometry, doc.road_cells[0].metric_geometry);
    async function* interrupted() { yield rows[0]; throw Error('interrupted'); }
    await assert.rejects(bindMerchantRows(doc, interrupted(), {}), /interrupted/);
    assert.equal((await bindMerchantRows(doc, [], {})).store_count, 0);
    await assert.rejects(bindMerchantRows({ ...doc, metric_crs: 'EPSG:4326' }, [], {}), /米制道路基础面/);
});
