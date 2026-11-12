import test from 'node:test';
import assert from 'node:assert/strict';
import { BalanceOptions, balanceScopeFaces, faceGraph, connectedFaces } from '../src/zone-balance';
import type { GridPolygon } from '../src/grid-geometry';
function cells(width: number, height: number): GridPolygon[] { return Array.from({ length: width * height }, (_, i) => { const x = (i % width) * 1000, y = Math.floor(i / width) * 1000; return { type: 'Polygon', coordinates: [[[x, y], [x + 1000, y], [x + 1000, y + 1000], [x, y + 1000], [x, y]]] }; }); }
test('scope workload preserves historical calibration, nonlinear marginal costs and recommended-area semantics', () => {
    const calibrated = new BalanceOptions({ areaWeight: .25 });
    for (const [area, stores] of [[20, 8000], [30, 6000], [50, 3500], [50, 4000], [50, 4500]]) {
        const load = calibrated.score(area * 1e6, stores);
        assert.ok(load >= calibrated.loadMin && load <= calibrated.loadMax);
    }
    const o = new BalanceOptions();
    assert.equal(o.areaWeight, .15);
    assert.equal(o.seedCount(50e6, 4000, 100), 1);
    assert.ok(o.seedCount(25e6, 24000, 100) > o.seedCount(25e6, 8000, 100));
    assert.ok(o.marginal(40e6, 4000, 10e6, 0) > o.marginal(20e6, 4000, 10e6, 0));
    assert.ok(o.marginal(20e6, 12000, 0, 2000) > o.marginal(20e6, 6000, 0, 2000));
    assert.notEqual(o.score(20e6, 4000) + o.score(10e6, 2000), o.score(30e6, 6000));
    assert.ok(Object.isFrozen(o));
    for (const config of [{ areaWeight: 0 }, { areaWeight: 1 }, { loadMin: 2, loadMax: 1 }, { targetAreaKm2: NaN }, { targetStores: true }, { loadMax: Infinity }, { targetStores: null }])
        assert.throws(() => new BalanceOptions(config as any), /综合负载参数/);
});
test('scope partition preserves every face and store, follows real shared edges and keeps inputs unchanged', async () => {
    const geoms = cells(8, 8), counts = geoms.map((_, i) => i % 8 < 4 ? 70 : 10), graph = faceGraph(geoms), topology = geoms.map((_, i) => [...graph.get(i)!]), before = JSON.stringify({ geoms, counts, topology }), parts = await balanceScopeFaces(geoms, counts, { targetAreaKm2: 10, targetStores: 600, areaWeight: .25, loadMin: .5, loadMax: 1.5 }, topology);
    assert.deepEqual(parts.flatMap(p => p.members).sort((a, b) => a - b), geoms.map((_, i) => i));
    assert.equal(parts.reduce((n, p) => n + p.stores, 0), counts.reduce((a, b) => a + b, 0));
    assert.ok(parts.every(p => connectedFaces(graph, new Set(p.members))));
    assert.equal(JSON.stringify({ geoms, counts, topology }), before);
    const corner = cells(2, 2);
    assert.equal(faceGraph([corner[0], corner[3]]).get(0)!.size, 0);
    const gap = structuredClone(cells(2, 1));
    gap[1].coordinates = gap[1].coordinates.map(r => r.map(([x, y]) => [x + .001, y]));
    assert.equal(faceGraph(gap).get(0)!.size, 0);
    await assert.rejects(balanceScopeFaces(geoms, [1]), /统计无效/);
    await assert.rejects(balanceScopeFaces(geoms, counts, {}, topology.map((r, i) => i === 0 ? [] : r)), /拓扑无效/);
});
test('commercial long regions may split below the load ceiling, while isolated and overloaded faces remain assigned', async () => {
    const geoms = cells(8, 2), counts = geoms.map(() => 200), options = new BalanceOptions();
    assert.ok(options.score(16e6, 3200) < options.loadMax);
    const parts = await balanceScopeFaces(geoms, counts);
    assert.ok(parts.length >= 2);
    assert.deepEqual(parts.flatMap(p => p.members).sort((a, b) => a - b), geoms.map((_, i) => i));
    const disconnected = [cells(1, 1)[0], { type: 'Polygon' as const, coordinates: [[[10000, 0], [11000, 0], [11000, 1000], [10000, 1000], [10000, 0]]] as GridPolygon['coordinates'] }];
    const preserved = await balanceScopeFaces(disconnected, [0, 100000]);
    assert.equal(preserved.length, 2);
    assert.equal(preserved.reduce((n, p) => n + p.stores, 0), 100000);
    assert.ok(preserved.some(p => p.workload > options.loadMax));
    assert.ok(preserved.some(p => p.stores === 0));
});
