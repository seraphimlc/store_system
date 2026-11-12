import test from 'node:test';
import assert from 'node:assert/strict';
import { previewScopeZones, type ScopeSource } from '../src/zone-scope';
import { gridToGeographic, type GridPolygon } from '../src/grid-geometry';
import { faceGraph } from '../src/zone-balance';
function fixture(): ScopeSource {
    const square = (x: number, y: number, width = 500, height = 500): GridPolygon => ({ type: 'Polygon', coordinates: [[[x, y], [x + width, y], [x + width, y + height], [x, y + height], [x, y]]] }), geoms = [square(0, 0), square(500, 0), square(0, 500), square(500, 500)], work = gridToGeographic(square(0, 0, 1000, 1000)), topology = faceGraph(geoms);
    return { ward_code: 'tokyo23', ward_name: '东京23区整体', scope_ward_count: 23, snapshot_id: 'fixed-scope', captured_at: '2026-11-11T00:00:00Z', source: 'synthetic', store_count: 400, area_km2: 1, excluded_area_km2: 0, road_count: 0, boundary: work, work_area: work, metric_crs: 'EPSG:6677', coverage_revision: 2, road_cells: geoms.map((geometry, i) => ({ id: 'cell-' + i, metric_geometry: geometry, store_count: 100, merchant_ids: Array.from({ length: 100 }, (_, k) => 'store-' + i + '-' + k) })), topology: geoms.map((_, i) => [...topology.get(i)!]), merchant_audit: { inside_boundary: 400, inside_work: 400, excluded_unreviewed: 0, source_duplicates: 0, invalid_coordinates: 0, unlocated_in_work: 0 }, review_merchants: [] };
}
test('complete scope preview conserves cells and merchants and confirms only when geometry, source, shape and load pass', async () => {
    const doc = fixture(), before = JSON.stringify(doc), result = await previewScopeZones(doc, 400, 2, { includeMemberships: true });
    assert.equal(result.summary.confirmation_ready, true);
    assert.equal(result.preview_only, true);
    assert.equal(result.summary.max_area_km2_limit, null);
    assert.deepEqual(result.issues, []);
    assert.equal(result.features.length, 1);
    assert.equal(result.grids.length, 1);
    assert.equal(result.features[0].properties.store_count, 400);
    assert.deepEqual(result.grids[0].properties.cell_ids, ['cell-0', 'cell-1', 'cell-2', 'cell-3']);
    assert.equal(result.audit.all_cells_assigned_once, true);
    assert.equal(result.audit.all_task_cells_assigned_once, true);
    assert.equal(result.audit.merchant_pass, true);
    assert.equal(result.audit.geometric_pass, true);
    assert.equal(JSON.stringify(doc), before);
    assert.deepEqual(await previewScopeZones(doc, 400, 2, { includeMemberships: true }), result);
    const without = await previewScopeZones(doc, 400, 2);
    assert.equal(Object.hasOwn(without.grids[0].properties, 'cell_ids'), false);
});
test('scope failures remain assigned and never become eligible by omitting merchant or coverage audit', async () => {
    const cases: [
        string,
        (doc: ScopeSource) => void
    ][] = [['source_audit_missing', doc => { delete doc.merchant_audit; delete doc.coverage_revision; }], ['merchant_coverage_failed', doc => { doc.road_cells![1].merchant_ids![0] = doc.road_cells![0].merchant_ids![0]; }], ['merchant_coverage_failed', doc => { doc.road_cells![0].store_count--; }], ['excluded_merchant_review', doc => { doc.merchant_audit!.excluded_unreviewed = 1; }], ['coverage_failed', doc => { doc.road_cells![1].id = doc.road_cells![0].id; }], ['coverage_failed', doc => { doc.work_area = gridToGeographic({ type: 'Polygon', coordinates: [[[0, 0], [1500, 0], [1500, 1000], [0, 1000], [0, 0]]] }); }]];
    for (const [code, change] of cases) {
        const doc = fixture();
        change(doc);
        const result = await previewScopeZones(doc, 400, 2, { includeMemberships: true });
        assert.equal(result.summary.confirmation_ready, false, code);
        assert.ok(result.issues.some(issue => issue.code === code), code);
        assert.equal(result.pending_grids.length, 0);
        assert.equal(result.grids.flatMap(g => g.properties.cell_ids!).length, 4);
        assert.equal(result.audit.all_task_cells_assigned_once, true);
    }
    const doc = fixture();
    await assert.rejects(previewScopeZones(doc, 400, 2, { generationRules: true, balance: null }), /必须使用综合负载参数/);
    await assert.rejects(previewScopeZones({ ...doc, metric_crs: 'EPSG:4326' }, 400, 2), /投影声明不正确/);
    await assert.rejects(previewScopeZones({ ...doc, scope_ward_count: 22 }, 400, 2), /整体快照不完整/);
    await assert.rejects(previewScopeZones(doc, 0, 2), /参数超出范围/);
});
test('scope preview CLI selects the scope algorithm, preserves explicit load parameters and immutable file outputs', async () => {
    const { mkdtemp, writeFile, readFile, rm } = await import('node:fs/promises'), { tmpdir } = await import('node:os'), { join } = await import('node:path'), { spawnSync } = await import('node:child_process');
    const directory = await mkdtemp(join(tmpdir(), 'zone-scope-test-')), input = join(directory, 'scope.json'), output = join(directory, 'preview.json'), run = (extra: string[]) => spawnSync(process.execPath, ['--import', 'tsx', 'scripts/preview-zone-snapshot.mts', '--input', input, '--stores', '400', '--max-area-km2', '2', ...extra], { encoding: 'utf8' });
    try {
        await writeFile(input, JSON.stringify(fixture()));
        const dry = run([]);
        assert.equal(dry.status, 0, dry.stderr);
        assert.equal(JSON.parse(dry.stdout).summary.confirmation_ready, true);
        await assert.rejects(readFile(output), { code: 'ENOENT' });
        const saved = run(['--include-memberships', '--area-weight', '.3', '--load-min', '.5', '--load-max', '1.5', '--write', '--output', output]);
        assert.equal(saved.status, 0, saved.stderr);
        const frozen = await readFile(output, 'utf8'), result = JSON.parse(frozen);
        assert.equal(result.summary.balance.area_weight, .3);
        assert.equal(result.summary.balance.load_min, .5);
        assert.equal(result.grids[0].properties.cell_ids.length, 4);
        assert.notEqual(run(['--write', '--output', output]).status, 0);
        assert.equal(await readFile(output, 'utf8'), frozen);
        assert.notEqual(run(['--write', '--output', input]).status, 0);
        assert.notEqual(run(['--legacy-area', '--area-weight', '.3']).status, 0);
        assert.equal(run(['--legacy-area']).status, 0);
        const generated = run(['--generation-rules', '--river-directory', directory]);
        assert.equal(generated.status, 0, generated.stderr);
        const generation = JSON.parse(generated.stdout);
        assert.equal(generation.summary.balance.area_weight, .15);
        assert.equal(generation.riverEvidence.files, 0);
        assert.equal(generation.databaseTouched, false);
        assert.notEqual(run(['--generation-rules', '--legacy-area']).status, 0);
        assert.notEqual(run(['--max-river-width-m', '100']).status, 0);
    }
    finally {
        await rm(directory, { recursive: true, force: true });
    }
});
test('generation rules remove only unbound zero-store land and keep the exclusion explicit in coverage audit', async () => {
    const doc = fixture(), extra = { id: 'external-empty', metric_geometry: { type: 'Polygon' as const, coordinates: [[[3000, 0], [3500, 0], [3500, 500], [3000, 500], [3000, 0]]] as GridPolygon['coordinates'] }, store_count: 0, merchant_ids: [] as string[] };
    doc.road_cells!.push(extra);
    doc.topology!.push([]);
    doc.work_area = { type: 'MultiPolygon', coordinates: [(doc.work_area as GridPolygon).coordinates, gridToGeographic(extra.metric_geometry).coordinates as GridPolygon['coordinates']] };
    const result = await previewScopeZones(doc, 400, 2, { generationRules: true, includeMemberships: true });
    assert.deepEqual(result.audit.excluded_empty_cell_ids, ['external-empty']);
    assert.equal(result.audit.generation_rules?.excluded_cell_count, 1);
    assert.equal(result.audit.geometric_pass, true);
    assert.equal(result.audit.merchant_pass, true);
    assert.equal(result.audit.all_task_cells_assigned_once, true);
    assert.equal(result.work_area.type, 'Polygon');
    extra.merchant_ids.push('hidden-binding');
    await assert.rejects(previewScopeZones(doc, 400, 2, { generationRules: true }), /仍有商户绑定/);
});
