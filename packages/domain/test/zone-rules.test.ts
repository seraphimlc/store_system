import test from 'node:test';
import assert from 'node:assert/strict';
import { consolidateMetricZones, zoneCountLimit } from '../src/zone-rules';
import type { GridPolygon } from '../src/grid-geometry';
import { faceGraph } from '../src/zone-balance';
const box = (x: number, y: number, w = 1000, h = 1000): GridPolygon => ({ type: 'Polygon', coordinates: [[[x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y]]] });
test('formal count policy merges actual neighbours and excludes only external zero-store groups without mutation', async () => {
    assert.equal(zoneCountLimit(600, 25), 72);
    assert.equal(zoneCountLimit(582.102, 25), 69);
    assert.equal(zoneCountLimit(24, 25), 2);
    assert.throws(() => zoneCountLimit(0, 25));
    const geoms = [box(0, 0), box(1000, 0), box(2000, 0)], counts = [2, 3, 4], members = [[0], [1], [2]], topology = [...faceGraph(geoms).values()].map(edges => [...edges]), before = JSON.stringify({ geoms, counts, members, topology }), result = await consolidateMetricZones(geoms, counts, topology, members, {}, 3);
    assert.deepEqual(result.groups, [[0, 1, 2]]);
    assert.equal(result.policy.adjacent_merges, 2);
    assert.deepEqual(result.excluded, []);
    assert.equal(JSON.stringify({ geoms, counts, members, topology }), before);
    const excluded = await consolidateMetricZones([box(0, 0), box(3000, 0)], [5, 0], [[], []], [[0], [1]], {}, 2);
    assert.deepEqual(excluded.excluded, [1]);
    assert.equal(excluded.policy.excluded_area_km2, 1);
    await assert.rejects(consolidateMetricZones(geoms, counts, topology, [[0], [0], [2]], {}, 3), /完整且唯一/);
    await assert.rejects(consolidateMetricZones([box(0, 0)], [0], [[]], [[0]], {}, 1), /没有商户/);
});
test('formal river policy retains actual water gaps, exact width rejection and load/shape constraints', async () => {
    const geoms = [box(0, 0), box(1060, 0)], rivers = [box(1000, -100, 60, 1200)], result = await consolidateMetricZones(geoms, [5, 5], [[], []], [[0], [1]], {}, 2, rivers);
    assert.deepEqual(result.groups, [[0, 1]]);
    assert.equal(result.policy.river_merges[0].width_m, 60);
    assert.equal(result.policy.river_merges[0].merged_merchants, 10);
    assert.equal(result.policy.river_geometry_available, true);
    for (const evidence of [[], [box(1000, -100, 30, 1200)], [box(3000, -100, 60, 1200)]])
        await assert.rejects(consolidateMetricZones(geoms, [5, 5], [[], []], [[0], [1]], {}, 2, evidence), /禁止跨外部水域/);
    await assert.rejects(consolidateMetricZones(geoms, [5, 5], [[], []], [[0], [1]], {}, 2, rivers, 0), /不超过0米/);
    await assert.rejects(consolidateMetricZones([box(0, 0), box(1000, 0)], [8000, 8000], [[1], [0]], [[0], [1]], {}, 2), /负载上限/);
});
