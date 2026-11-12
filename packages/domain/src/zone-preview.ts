/** Ward preview over frozen grids. Whole-Tokyo scope uses a separate algorithm. No persistence. */
import { createHash } from 'node:crypto';
import { arena, union, type GridGeometry, type Scope } from './grid-geometry';
import { project, validProjected, centroid, neighbours, groupAtoms, ZoneSnapshotError, type Group } from './zone-snapshot';
export type ZoneIssue = {
    code: string;
    severity?: string;
    [key: string]: unknown;
};
export interface ZonePlanningSnapshot {
    schema_version: number;
    ward_code: string;
    ward_name: string;
    snapshot_id: string;
    captured_at: string;
    source: unknown;
    store_count: number;
    area_km2: number;
    excluded_area_km2: number;
    road_count: number;
    boundary: GridGeometry;
    work_area: GridGeometry;
    grids: {
        id: string;
        geometry: GridGeometry;
        area_km2: number;
        store_count: number;
    }[];
    issues?: ZoneIssue[];
    scope_ward_count?: number;
}
function compare(a: number[], b: number[]) { for (let i = 0; i < Math.min(a.length, b.length); i++)
    if (a[i] !== b[i])
        return a[i] - b[i]; return a.length - b.length; }
/** Python round(float, digits): exact binary value, decimal scale, ties to even. */
export function rounded(value: number, digits: number) { const sign = value < 0 ? -1 : 1, buffer = new DataView(new ArrayBuffer(8)); buffer.setFloat64(0, Math.abs(value)); const bits = buffer.getBigUint64(0), exponent = Number((bits >> 52n) & 2047n), mantissa = (bits & ((1n << 52n) - 1n)) + (exponent ? 1n << 52n : 0n), shift = (exponent || 1) - 1023 - 52; let numerator = mantissa * 10n ** BigInt(digits), denominator = 1n; if (shift >= 0)
    numerator <<= BigInt(shift);
else
    denominator <<= BigInt(-shift); let quotient = numerator / denominator; const remainder = numerator % denominator; if (remainder * 2n > denominator || (remainder * 2n === denominator && quotient % 2n))
    quotient++; return sign * Number(quotient) / 10 ** digits; }
function components(graph: Map<number, Set<number>>) { const unseen = new Set(graph.keys()), parts: Set<number>[] = []; while (unseen.size) {
    const pending = [Math.min(...unseen)], part = new Set<number>();
    while (pending.length) {
        const i = pending.pop()!;
        if (part.has(i))
            continue;
        part.add(i);
        for (const j of graph.get(i)!)
            if (!part.has(j))
                pending.push(j);
    }
    for (const i of part)
        unseen.delete(i);
    parts.push(part);
} return parts; }
function rebalance(s: Scope, groups: Group[], geoms: number[], counts: number[], maxArea: number): Group[] {
    if (groups.length < 2)
        return groups;
    const graph = neighbours(s, geoms), members = groups.map(g => new Set(g.members)), sizes = geoms.map(p => s.area(p)), meanArea = sizes.reduce((a, b) => a + b, 0) / groups.length, meanCount = Math.max(counts.reduce((a, b) => a + b, 0) / groups.length, 1), areas = members.map(part => [...part].reduce((a, i) => a + sizes[i], 0)), stores = members.map(part => [...part].reduce((a, i) => a + counts[i], 0));
    const connected = (part: Set<number>) => { if (!part.size)
        return false; const seen = new Set<number>(), pending = [Math.min(...part)]; while (pending.length) {
        const i = pending.pop()!;
        if (seen.has(i))
            continue;
        seen.add(i);
        for (const j of graph.get(i)!)
            if (part.has(j) && !seen.has(j))
                pending.push(j);
    } return seen.size === part.size; };
    for (let iteration = 0; iteration < Math.min(geoms.length * 2, 300); iteration++) {
        const owner = new Map<number, number>();
        members.forEach((part, k) => part.forEach(i => owner.set(i, k)));
        let best: number[] | undefined;
        members.forEach((part, a) => {
            if (part.size <= 1)
                return;
            for (const i of [...part].sort((x, y) => x - y)) {
                const adjacent = [...new Set([...graph.get(i)!].map(j => owner.get(j)!).filter(b => b !== a))].sort((x, y) => x - y);
                for (const b of adjacent) {
                    const size = sizes[i], count = counts[i], remaining = new Set([...part].filter(j => j !== i));
                    if (areas[b] + size > maxArea + 1 || !connected(remaining))
                        continue;
                    const delta = 2 * size * (areas[b] - areas[a] + size) / meanArea ** 2 + .15 * 2 * count * (stores[b] - stores[a] + count) / meanCount ** 2, before = Math.max(0, Math.max(...areas) / Math.min(...areas) - 2), updated = [...areas];
                    updated[a] -= size;
                    updated[b] += size;
                    const after = Math.max(0, Math.max(...updated) / Math.min(...updated) - 2), hardDelta = after - before, candidate = [hardDelta, delta, i, a, b];
                    if (!(hardDelta < -1e-8 || (Math.abs(hardDelta) <= 1e-8 && delta < -1e-8)) || (best && compare(candidate, best) >= 0))
                        continue;
                    const receiving = new Set([...members[b], i]), joined = [remaining, receiving].map(values => union(s, [...values].sort((x, y) => x - y).map(j => geoms[j])));
                    if (joined.every(p => s.g.GEOSGeomTypeId(p) === 3 && s.g.GEOSisValid(p) === 1))
                        best = candidate;
                }
            }
        });
        if (!best)
            break;
        const [, , i, a, b] = best;
        members[a].delete(i);
        members[b].add(i);
        areas[a] -= sizes[i];
        areas[b] += sizes[i];
        stores[a] -= counts[i];
        stores[b] += counts[i];
    }
    return members.map((part, k) => ({ geom: union(s, [...part].sort((a, b) => a - b).map(i => geoms[i])), members: part, count: stores[k], version: 0 }));
}
export async function previewWardZones(doc: ZonePlanningSnapshot, targetStores: number, maxAreaKm2: number) {
    if (typeof targetStores !== 'number' || !Number.isFinite(targetStores) || targetStores < 1 || targetStores > 100000 || !Number.isFinite(maxAreaKm2) || maxAreaKm2 < 2 || maxAreaKm2 > 500)
        throw new ZoneSnapshotError('试算参数超出范围');
    if (doc.ward_code === 'tokyo23') {
        if (doc.scope_ward_count !== 23)
            throw new ZoneSnapshotError('东京23区整体快照不完整，不能按单区结果拼接代替');
        throw new ZoneSnapshotError('东京23区整体预览需使用整体覆盖与均衡算法');
    }
    if (doc.schema_version !== 1 || !/^131(?:0[1-9]|1[0-9]|2[0-3])$/.test(doc.ward_code))
        throw new ZoneSnapshotError('请选择东京23区之一');
    if (!doc.grids.length)
        throw new ZoneSnapshotError('该区没有可用任务网格');
    const pending = doc.grids.filter(g => g.area_km2 < .5 - 1e-6 || g.area_km2 > 2 + 1e-6), grids = doc.grids.filter(g => g.area_km2 >= .5 - 1e-6 && g.area_km2 <= 2 + 1e-6);
    if (!grids.length)
        throw new ZoneSnapshotError('没有满足0.5～2km²面积约束的网格；请补充道路数据后重新采集');
    return arena(s => {
        const geoms = grids.map(g => validProjected(s, g.geometry)), counts = grids.map(g => g.store_count);
        if (counts.some(n => !Number.isFinite(n) || n < 0) || geoms.some(g => s.area(g) <= 0))
            throw new ZoneSnapshotError('网格统计或边界无效');
        const sizes = geoms.map(g => s.area(g)), totalArea = sizes.reduce((a, b) => a + b, 0), requested = Math.max(1, Math.ceil(counts.reduce((a, b) => a + b, 0) / targetStores), Math.ceil(totalArea / (maxAreaKm2 * 1e6))), parts = components(neighbours(s, geoms)), componentAreas = parts.map(part => [...part].reduce((area, i) => area + sizes[i], 0)), ratioCap = 2 * Math.min(...componentAreas), impossible = Math.max(...sizes) > ratioCap + 1, cap = impossible ? maxAreaKm2 * 1e6 : Math.min(maxAreaKm2 * 1e6, ratioCap), desired = Math.min(Math.max(requested, componentAreas.length, Math.ceil(totalArea / cap)), grids.length);
        const groups = rebalance(s, groupAtoms(s, geoms, desired, { counts, maxArea: cap }), geoms, counts, cap);
        groups.sort((a, b) => { const x = centroid(s, a.geom), y = centroid(s, b.geom); return x[0] - y[0] || x[1] - y[1]; });
        const issues: ZoneIssue[] = (doc.issues ?? []).map(issue => ({ ...issue })), areas = groups.map(g => s.area(g.geom) / 1e6), ratio = Math.max(...areas) / Math.min(...areas);
        if (ratio > 2 + 1e-5)
            issues.push({ code: impossible ? 'ratio_infeasible' : 'zone_ratio', ratio: rounded(ratio, 3) });
        if (Math.max(...areas) > maxAreaKm2 + 1e-5)
            issues.push({ code: 'zone_area' });
        if (groups.length !== requested)
            issues.push({ code: 'zone_count', requested, actual: groups.length, severity: 'info' });
        if (counts.reduce((a, b) => a + b, 0) === 0)
            issues.push({ code: 'no_stores' });
        const owner = new Map<number, string>(), features = groups.map(group => { const ids = [...group.members].map(i => grids[i].id).sort((a, b) => compare([...a].map(c => c.codePointAt(0)!), [...b].map(c => c.codePointAt(0)!))), id = doc.ward_code + '-Z-' + createHash('sha256').update(ids.join('|')).digest('hex').slice(0, 10); for (const i of group.members)
            owner.set(i, id); return { type: 'Feature', id, geometry: project(s.json(group.geom), true), properties: { id, level: 'zone', ward_code: doc.ward_code, grid_count: ids.length, store_count: group.count, shape: null, area_km2: s.area(group.geom) / 1e6 } }; });
        const gridFeatures = grids.map((g, i) => ({ type: 'Feature', id: g.id, geometry: g.geometry, properties: { id: g.id, level: 'grid', zone_id: owner.get(i)!, area_km2: g.area_km2, store_count: g.store_count } }));
        return { type: 'FeatureCollection', features, grids: gridFeatures, boundary: doc.boundary, work_area: doc.work_area, pending_grids: pending.map(g => ({ type: 'Feature', id: g.id, geometry: g.geometry, properties: { id: g.id, level: 'pending', area_km2: g.area_km2, store_count: g.store_count } })), meta: { ward_code: doc.ward_code, ward_name: doc.ward_name, snapshot_id: doc.snapshot_id, captured_at: doc.captured_at, source: doc.source, store_count: doc.store_count, area_km2: doc.area_km2, excluded_area_km2: doc.excluded_area_km2, road_count: doc.road_count }, summary: { zone_count: groups.length, grid_count: grids.length, area_ratio: ratio, min_area_km2: Math.min(...areas), max_area_km2: Math.max(...areas), target_stores: targetStores, max_area_km2_limit: maxAreaKm2, eligible: !issues.some(i => i.severity !== 'info'), pending_grid_count: pending.length, pending_store_count: pending.reduce((sum, g) => sum + g.store_count, 0), pending_area_km2: pending.reduce((sum, g) => sum + g.area_km2, 0) }, issues, preview_only: true };
    });
}
