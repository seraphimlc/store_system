import {spawnLegacyPython} from '../../../scripts/legacy-python.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtemp, writeFile, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { prepareOsmScopeGeometry, osmRelationPolygon, osmExclusionFaces, osmWardRelations, type OsmScopeGeometryInput } from '../src/osm-zone-source';
import { prepareScopeSource } from '../src/zone-source';
function oracle() { const result = spawnLegacyPython( ['legacy/scripts/osm-zone-source-oracle.py'], { encoding: 'utf8', maxBuffer: 16e6 }); assert.equal(result.status, 0, result.stderr); return JSON.parse(result.stdout); }
test('raw OSM parsing preserves water holes, ignores woodland/river centerlines and requires all official wards/roads', async () => {
    const row = oracle(), before = JSON.stringify(row.input), result = await prepareOsmScopeGeometry(row.input);
    assert.equal(result.wards.length, 23);
    assert.equal(result.roads.length, 23);
    assert.equal(result.coastlines.length, 1);
    assert.equal(result.excluded.length, 2);
    assert.equal(JSON.stringify(row.input), before);
    assert.equal(osmWardRelations(row.input.boundaries.elements).size, 23);
    const polygon = await osmRelationPolygon(row.relation);
    assert.equal(polygon.type, 'Polygon');
    assert.equal(polygon.coordinates.length, 2);
    assert.equal((await osmExclusionFaces(row.water)).length, 2);
    const missing = structuredClone(row.input);
    missing.boundaries.elements = missing.boundaries.elements.filter((element: any) => element.id !== 100);
    await assert.rejects(prepareOsmScopeGeometry(missing), /边界不完整/);
    const noRoads = structuredClone(row.input);
    noRoads.wards['13101'].elements = noRoads.wards['13101'].elements.filter((item: any) => !item.tags.highway);
    await assert.rejects(prepareOsmScopeGeometry(noRoads), /道路缺失/);
    const incomplete = structuredClone(row.input);
    incomplete.wards['13101'].remark = 'timeout';
    await assert.rejects(prepareOsmScopeGeometry(incomplete), /不完整结果/);
    const bad = structuredClone(row.input);
    bad.boundaries.elements[0].id = '../private';
    await assert.rejects(prepareOsmScopeGeometry(bad), /关系标识无效/);
    const source = await prepareScopeSource(result, [{ place_id: 'fixed-shop', name: '合成店', lon: '139.016', lat: '35.005' }]);
    assert.equal(source.scope_ward_count, 23);
    assert.equal(source.store_count, 1);
    assert.equal(source.merchant_audit.mapped_to_faces, 1);
    assert.ok(source.road_cells.length >= 23);
});
test('OSM cache CLI reads exact ward files with v1 fallback, rejects corrupted v2 and writes only new geometry', async () => {
    const row = oracle(), input: OsmScopeGeometryInput = row.input, root = await mkdtemp(join(tmpdir(), 'osm-zone-cache-')), output = join(root, 'prepared.json');
    const run = (extra: string[] = []) => spawnSync(process.execPath, ['--import', 'tsx', 'scripts/prepare-osm-zone-geometry.mts', '--directory', root, '--captured-at', '2026-11-11T00:00:00Z', ...extra], { encoding: 'utf8' });
    try {
        await writeFile(join(root, 'boundaries.source.json'), JSON.stringify(input.boundaries));
        await writeFile(join(root, 'coastlines.source.json'), JSON.stringify(input.coastlines));
        for (const [code, relation] of osmWardRelations(input.boundaries.elements)) {
            await writeFile(join(root, code + '-' + relation.id + (Number(code) % 2 ? '.roads-v1.source.json' : '.roads-v2.source.json')), JSON.stringify(input.wards[code]));
        }
        const dry = run();
        assert.equal(dry.status, 0, dry.stderr);
        assert.equal(JSON.parse(dry.stdout).network, false);
        assert.equal(JSON.parse(dry.stdout).files, 25);
        await assert.rejects(readFile(output), { code: 'ENOENT' });
        const saved = run(['--write', '--output', output]);
        assert.equal(saved.status, 0, saved.stderr);
        const frozen = await readFile(output, 'utf8');
        assert.equal(JSON.parse(frozen).code, 'tokyo23');
        assert.equal(JSON.parse(frozen).roads.length, 23);
        assert.notEqual(run(['--write', '--output', output]).status, 0);
        assert.equal(await readFile(output, 'utf8'), frozen);
        assert.notEqual(run(['--write', '--output', join(root, 'boundaries.source.json')]).status, 0);
        const naturalPath = join(root, 'natural-input.json');
        const natural = run(['--natural-input', '--write', '--output', naturalPath]);
        assert.equal(natural.status, 0, natural.stderr);
        const naturalInput = JSON.parse(await readFile(naturalPath, 'utf8'));
        assert.ok(naturalInput.elements.some((element: any) => element.tags?.railway === 'rail'));
        assert.equal(naturalInput.source.railway_collected, false);
        await writeFile(join(root, 'railways.source.json'), JSON.stringify({ elements: [{ type: 'way', id: 8888, tags: { railway: 'rail' }, geometry: [{ lon: 139, lat: 35 }, { lon: 139.01, lat: 35.01 }] }] }));
        const extraRail = run(['--natural-input']);
        assert.equal(extraRail.status, 0, extraRail.stderr);
        assert.equal(JSON.parse(extraRail.stdout).files, 26);
        await writeFile(join(root, '13101-100.roads-v2.source.json'), JSON.stringify({ remark: 'partial', elements: [] }));
        const corrupted = run();
        assert.notEqual(corrupted.status, 0);
        assert.match(corrupted.stderr, /incomplete/);
    }
    finally {
        await rm(root, { recursive: true, force: true });
    }
});
