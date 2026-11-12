/** Formal generation policy. Keeps real water gaps; shells are containment tests only. */
import { arena, union, shapeMetrics, type Scope, type GridGeometry, type GridPolygon } from './grid-geometry';
import { parts, index, ZoneSnapshotError } from './zone-snapshot';
import { BalanceOptions, compare, sum, narrowNeck, type BalanceConfig, type FaceGraph } from './zone-balance';
import { rounded } from './zone-preview';
const sorted = (part: Iterable<number>) => [...part].sort((a, b) => a - b);
export function zoneCountLimit(areaKm2: number, targetAreaKm2: number) { if (!Number.isFinite(areaKm2) || areaKm2 <= 0 || !Number.isFinite(targetAreaKm2) || targetAreaKm2 <= 0)
    throw new ZoneSnapshotError('片区面积参数必须为正数'); return Math.max(1, Math.floor(areaKm2 / targetAreaKm2 * 3)); }
function shell(s: Scope, p: number) { return union(s, parts(s, p).map(piece => s.read({ type: 'Polygon', coordinates: [(s.json(piece) as GridPolygon).coordinates[0]] })), 0); }
function crossing(s: Scope, left: number, right: number, rivers: number | undefined, width: number) { if (!rivers || s.g.GEOSisEmpty(rivers) || width <= 0)
    return null; const sequence = s.g.GEOSNearestPoints(left, right); if (!sequence)
    throw new ZoneSnapshotError('河道连接计算失败'); let line: number; try {
    line = s.own(s.g.GEOSGeom_createLineString(sequence));
}
catch (error) {
    s.g.GEOSCoordSeq_destroy(sequence);
    throw error;
} const length = s.length(line); if (length <= .01 || length > width + .001)
    return null; if (s.g.GEOSCovers(rivers, line) !== 1 && s.g.GEOSCovers(s.own(s.g.GEOSBuffer(rivers, .01, 16)), line) !== 1)
    return null; return line; }
function riverShapeValid(s: Scope, p: number) { const pieces = parts(s, p); if (!pieces.length)
    return false; const total = sum(pieces.map(part => s.area(part))); if (pieces.filter(part => s.area(part) >= total * .04).some(part => !shapeMetrics(s, part).acceptable))
    return false; const exterior = shell(s, p), hull = s.own(s.g.GEOSConvexHull(exterior)); if (s.area(exterior) / Math.max(s.area(hull), 1) < .65)
    return false; const rect = s.json(s.own(s.g.GEOSMinimumRotatedRectangle(hull))) as GridPolygon, sides = rect.coordinates[0].slice(0, 4).map((a, i) => Math.hypot(a[0] - rect.coordinates[0][i + 1][0], a[1] - rect.coordinates[0][i + 1][1])); return Math.max(...sides) / Math.max(Math.min(...sides), .001) <= 3; }
export function consolidateScopeParts(s: Scope, original: Set<number>[], geoms: number[], counts: number[], graph: FaceGraph, o: BalanceOptions, areaKm2: number, rivers?: number, maxRiverWidthM = 100) {
    if (!Number.isFinite(maxRiverWidthM) || maxRiverWidthM < 0)
        throw new ZoneSnapshotError('河宽上限必须为非负有限数值');
    const limit = zoneCountLimit(areaKm2, o.targetAreaKm2);
    let groups = original.map(part => new Set(part));
    const all = groups.flatMap(part => [...part]);
    if (all.length !== geoms.length || new Set(all).size !== geoms.length || geoms.some((_, i) => !all.includes(i)))
        throw new ZoneSnapshotError('片区基础面必须完整且唯一归属');
    const initial = groups.length;
    let containmentMerges = 0, adjacentMerges = 0;
    const riverMerges: {
        width_m: number;
        crossing_metric: unknown;
        merged_merchants: number;
    }[] = [];
    const geometry = (part: Set<number>) => union(s, sorted(part).map(i => geoms[i]));
    let shapes = groups.map(geometry), outlines = shapes.map(p => shell(s, p)), parent = groups.map((_, i) => i);
    const containmentIndex = index(s, outlines);
    try {
        for (const i of groups.map((_, i) => i).sort((a, b) => s.area(outlines[a]) - s.area(outlines[b]) || a - b)) {
            const candidates = containmentIndex.query(shapes[i]).filter(j => j !== i && s.area(outlines[j]) > s.area(outlines[i]) && s.g.GEOSCovers(outlines[j], shapes[i]) === 1);
            if (candidates.length)
                parent[i] = candidates.sort((a, b) => s.area(outlines[a]) - s.area(outlines[b]) || a - b)[0];
        }
    }
    finally {
        containmentIndex.close();
    }
    const root = (i: number) => { while (parent[i] !== i)
        i = parent[i]; return i; }, consolidated = new Map<number, Set<number>>();
    groups.forEach((part, i) => { const k = root(i); if (!consolidated.has(k))
        consolidated.set(k, new Set()); for (const member of part)
        consolidated.get(k)!.add(member); if (parent[i] !== i)
        containmentMerges++; });
    groups = [...consolidated.keys()].sort((a, b) => a - b).map(k => consolidated.get(k)!);
    const excluded = new Set(groups.filter(part => sum([...part].map(i => counts[i])) === 0).flatMap(part => [...part]));
    groups = groups.filter(part => sum([...part].map(i => counts[i])) > 0);
    if (!groups.length)
        throw new ZoneSnapshotError('有效范围内没有商户，不生成作业片区');
    shapes = groups.map(geometry);
    outlines = shapes.map(p => shell(s, p));
    const merge = (left: number, right: number) => { for (const i of groups[right])
        groups[left].add(i); shapes[left] = union(s, [shapes[left], shapes[right]]); outlines[left] = shell(s, shapes[left]); groups.splice(right, 1); shapes.splice(right, 1); outlines.splice(right, 1); };
    const shapeCost = (value: number) => { const pieces = parts(s, value), total = sum(pieces.map(p => s.area(p))); let score = 0; for (const p of pieces) {
        const q = shapeMetrics(s, p), weight = s.area(p) / Math.max(total, 1);
        score += weight * (8 * Math.max(0, .75 - q.solidity) ** 2 + Math.max(0, q.aspect - 2.2) ** 2 + 4 * Math.max(0, .28 - q.compactness) ** 2 + Number(narrowNeck(s, p)));
    } return score; };
    while (groups.length > limit) {
        const owners = new Map<number, number>();
        groups.forEach((part, k) => part.forEach(i => owners.set(i, k)));
        const pairs = new Map<string, [
            number,
            number
        ]>(), crossings = new Map<string, number>();
        for (const [i, a] of owners)
            for (const j of graph.get(i)!)
                if (owners.has(j) && owners.get(j) !== a) {
                    const b = owners.get(j)!, pair: [
                        number,
                        number
                    ] = [Math.min(a, b), Math.max(a, b)];
                    pairs.set(pair.join(':'), pair);
                }
        if (rivers && s.g.GEOSisEmpty(rivers) === 0 && maxRiverWidthM > 0) {
            const tree = index(s, shapes);
            try {
                for (let left = 0; left < shapes.length; left++) {
                    const around = s.own(s.g.GEOSBuffer(shapes[left], maxRiverWidthM, 16));
                    for (const right of tree.query(around)) {
                        if (right <= left)
                            continue;
                        const k = left + ':' + right;
                        if (pairs.has(k))
                            continue;
                        const line = crossing(s, shapes[left], shapes[right], rivers, maxRiverWidthM);
                        if (line && s.length(line) <= maxRiverWidthM) {
                            pairs.set(k, [left, right]);
                            crossings.set(k, line);
                        }
                    }
                }
            }
            finally {
                tree.close();
            }
        }
        const choices: number[][] = [];
        for (const [left, right] of [...pairs.values()].sort(compare)) {
            const joined = new Set([...groups[left], ...groups[right]]), area = sum([...joined].map(i => s.area(geoms[i]))), stores = sum([...joined].map(i => counts[i])), load = o.score(area, stores);
            if (load > o.loadMax)
                continue;
            const merged = union(s, [shapes[left], shapes[right]]), isRiver = crossings.has(left + ':' + right);
            if (isRiver && !riverShapeValid(s, merged))
                continue;
            const before = shapeCost(shapes[left]) + shapeCost(shapes[right]), after = shapeCost(merged), acceptable = parts(s, merged).filter(p => s.area(p) > s.area(merged) * .04).every(p => shapeMetrics(s, p).acceptable);
            if (acceptable || after <= before + .01)
                choices.push([Number(isRiver), after - before, load, left, right]);
        }
        if (!choices.length)
            throw new ZoneSnapshotError(`片区数量上限为${limit}，但仍有${groups.length}个有商户的独立片区；包含归并、空商户排除及不超过${maxRiverWidthM}米的已知河道检查后，无法在负载上限和形状不退化约束内继续合并，禁止跨外部水域强行拼接。`);
        const [isRiver, , , left, right] = choices.sort(compare)[0];
        if (isRiver) {
            const line = crossings.get(left + ':' + right)!;
            riverMerges.push({ width_m: rounded(s.length(line), 3), crossing_metric: (s.json(line) as {
                    coordinates: unknown;
                }).coordinates, merged_merchants: sum([...new Set([...groups[left], ...groups[right]])].map(i => counts[i])) });
        }
        merge(left, right);
        if (!isRiver)
            adjacentMerges++;
        let changed = true;
        while (changed) {
            changed = false;
            const tree = index(s, outlines);
            try {
                for (const inner of groups.map((_, i) => i).sort((a, b) => s.area(outlines[a]) - s.area(outlines[b]) || a - b)) {
                    const containers = tree.query(shapes[inner]).filter(outer => outer !== inner && s.area(outlines[outer]) > s.area(outlines[inner]) && s.g.GEOSCovers(outlines[outer], shapes[inner]) === 1);
                    if (containers.length) {
                        const outer = containers.sort((a, b) => s.area(outlines[a]) - s.area(outlines[b]) || a - b)[0];
                        merge(outer, inner);
                        containmentMerges++;
                        changed = true;
                        break;
                    }
                }
            }
            finally {
                tree.close();
            }
        }
    }
    return { groups, excluded, policy: { version: 3, zone_count_multiplier: 3, max_zone_count: limit, original_zone_count: initial, containment_merges: containmentMerges, adjacent_merges: adjacentMerges, max_river_width_m: maxRiverWidthM, river_merges: riverMerges, river_geometry_available: !!rivers && s.g.GEOSisEmpty(rivers) === 0, excluded_cell_count: excluded.size, excluded_area_km2: sum([...excluded].map(i => s.area(geoms[i]))) / 1e6, zone_count: groups.length, reference_area_km2: areaKm2, target_area_km2: o.targetAreaKm2 } };
}
export async function consolidateMetricZones(geometries: GridGeometry[], counts: number[], topology: number[][], members: number[][], config: BalanceConfig = {}, areaKm2: number, riverFaces: GridGeometry[] = [], maxRiverWidthM = 100) { return arena(s => { const geoms = geometries.map(g => s.own(s.g.GEOSGeom_setPrecision(s.read(g), .001, 0))), rivers = riverFaces.length ? union(s, riverFaces.map(g => s.read(g))) : undefined, result = consolidateScopeParts(s, members.map(part => new Set(part)), geoms, counts, new Map(topology.map((adjacent, i) => [i, new Set(adjacent)])), new BalanceOptions(config), areaKm2, rivers, maxRiverWidthM); return { ...result, groups: result.groups.map(part => sorted(part)), excluded: sorted(result.excluded) }; }); }
