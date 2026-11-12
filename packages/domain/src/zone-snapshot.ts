/** Offline road-block snapshot generation. No provider, filesystem or database access. */
import { createHash } from 'node:crypto';
import { arena, union, normalizedWkb, gridPointToMetric, gridPointToGeographic, type Scope, type JsonGeometry, type GridGeometry, type GridPoint } from './grid-geometry';
export class ZoneSnapshotError extends Error {
}
export interface ZoneSnapshotInput {
    code: string;
    boundary: GridGeometry;
    roads: JsonGeometry[];
    excluded: GridGeometry[];
    merchants: GridPoint[];
    coastlines?: JsonGeometry[];
    capturedAt: string;
    source: unknown;
    referenceSnapshot?: {
        ward_code: string;
        snapshot_id?: string;
        grids: {
            id: string;
            geometry: GridGeometry;
        }[];
    };
}
const WARDS = ['千代田区', '中央区', '港区', '新宿区', '文京区', '台東区', '墨田区', '江東区', '品川区', '目黒区', '大田区', '世田谷区', '渋谷区', '中野区', '杉並区', '豊島区', '北区', '荒川区', '板橋区', '練馬区', '足立区', '葛飾区', '江戸川区'];
const MIN_GRID = 500000, MAX_GRID = 2000000;
const hash = (value: string | Uint8Array) => createHash('sha256').update(value).digest('hex');
export function project(value: JsonGeometry, inverse = false): JsonGeometry {
    const convert = (coordinates: any): any => typeof coordinates?.[0] === 'number' ? (inverse ? gridPointToGeographic(coordinates) : gridPointToMetric(coordinates)) : coordinates.map(convert);
    if (value.type === 'GeometryCollection')
        return { type: 'GeometryCollection', geometries: value.geometries.map(g => project(g, inverse)) } as JsonGeometry;
    if (!['Polygon', 'MultiPolygon', 'LineString', 'MultiLineString', 'Point'].includes(value.type))
        throw new ZoneSnapshotError('地理输入格式无效');
    const verify = (coordinates: any): void => {
        if (!Array.isArray(coordinates))
            throw new ZoneSnapshotError('地理输入格式无效');
        if (typeof coordinates[0] === 'number') {
            if (coordinates.length !== 2 || !coordinates.every(Number.isFinite) || (!inverse && (Math.abs(coordinates[0]) > 180 || Math.abs(coordinates[1]) > 90)))
                throw new ZoneSnapshotError('坐标无效');
        }
        else
            coordinates.forEach(verify);
    };
    verify(value.coordinates);
    return { ...value, coordinates: convert(value.coordinates) } as JsonGeometry;
}
export function parts(s: Scope, p: number): number[] {
    const type = s.g.GEOSGeomTypeId(p);
    if (s.g.GEOSisEmpty(p))
        return [];
    if (type === 3)
        return [p];
    if (type !== 6 && type !== 7)
        return [];
    return Array.from({ length: s.g.GEOSGetNumGeometries(p) }, (_, i) => parts(s, s.g.GEOSGetGeometryN(p, i))).flat();
}
export function centroid(s: Scope, p: number) {
    return (s.json(s.own(s.g.GEOSGetCentroid(p))) as {
        type: 'Point';
        coordinates: GridPoint;
    }).coordinates;
}
function boundary(s: Scope, p: number) { return s.own(s.g.GEOSBoundary(p)); }
export function validProjected(s: Scope, geometry: JsonGeometry) { return s.own(s.g.GEOSGeom_setPrecision(s.own(s.g.GEOSMakeValid(s.read(project(geometry)))), .001, 0)); }
export function polygonize(s: Scope, p: number) {
    const pointers = s.g.Module._malloc(4);
    try {
        s.g.Module.HEAPU32[pointers / 4] = p;
        return parts(s, s.own(s.g.GEOSPolygonize(pointers, 1)));
    }
    finally {
        s.g.Module._free(pointers);
    }
}
/** GEOS owns only the spatial tree; item values are numeric indices, never geometry pointers. */
export function index(s: Scope, geometries: number[]) {
    const tree = s.g.GEOSSTRtree_create(10);
    if (!tree)
        throw new ZoneSnapshotError('空间索引创建失败');
    geometries.forEach((geometry, i) => s.g.GEOSSTRtree_insert(tree, geometry, i + 1));
    let hits: number[] = [];
    const runtime = s.g.Module as typeof s.g.Module & {
        addFunction: (callback: (item: number) => void, signature: string) => number;
        removeFunction: (pointer: number) => void;
    };
    const callback = runtime.addFunction((item: number) => hits.push(item - 1), 'vii');
    return { query(p: number) { hits = []; s.g.GEOSSTRtree_query(tree, p, callback, 0); return hits; }, close() { s.g.GEOSSTRtree_destroy(tree); runtime.removeFunction(callback); } };
}
export function neighbours(s: Scope, geometries: number[]) {
    const graph = new Map(geometries.map((_, i) => [i, new Set<number>()])), tree = index(s, geometries), edges = geometries.map(p => boundary(s, p)), buffers = edges.map(p => s.own(s.g.GEOSBuffer(p, .002, 16)));
    try {
        geometries.forEach((p, i) => {
            const around = s.own(s.g.GEOSBuffer(p, .002, 16));
            for (const j of tree.query(around))
                if (j > i && s.length(s.own(s.g.GEOSIntersection(edges[i], buffers[j]))) > .2) {
                    graph.get(i)!.add(j);
                    graph.get(j)!.add(i);
                }
        });
        return graph;
    }
    finally {
        tree.close();
    }
}
export type Group = {
    geom: number;
    members: Set<number>;
    version: number;
    count: number;
};
type Candidate = [
    number,
    number,
    number,
    number,
    number
];
function compare(a: Candidate, b: Candidate) {
    for (let i = 0; i < a.length; i++)
        if (a[i] !== b[i])
            return a[i] - b[i];
    return 0;
}
class Heap {
    rows: Candidate[] = [];
    push(row: Candidate) {
        const a = this.rows;
        a.push(row);
        let i = a.length - 1;
        while (i) {
            const parent = (i - 1) >> 1;
            if (compare(a[parent], row) <= 0)
                break;
            a[i] = a[parent];
            i = parent;
        }
        a[i] = row;
    }
    pop() {
        const a = this.rows, first = a[0], last = a.pop()!;
        if (a.length) {
            let i = 0;
            while (i * 2 + 1 < a.length) {
                let child = i * 2 + 1;
                if (child + 1 < a.length && compare(a[child + 1], a[child]) < 0)
                    child++;
                if (compare(last, a[child]) <= 0)
                    break;
                a[i] = a[child];
                i = child;
            }
            a[i] = last;
        }
        return first;
    }
}
function outerPerimeter(s: Scope, p: number) { return parts(s, p).reduce((total, part) => total + s.length(s.g.GEOSGetExteriorRing(part)), 0); }
export function groupAtoms(s: Scope, atoms: number[], target: number, options: {
    counts?: number[];
    maxArea?: number;
} = {}) {
    const counts = options.counts ?? atoms.map(() => 0), maxArea = options.maxArea ?? MAX_GRID, targetStores = Math.max(counts.reduce((sum, n) => sum + n, 0) / Math.max(target, 1), 1), hasStores = counts.some(Boolean);
    const graph = neighbours(s, atoms), groups = new Map(atoms.map((p, i) => [i, { geom: p, members: new Set([i]), version: 0, count: counts[i] }])), targetArea = atoms.reduce((sum, p) => sum + s.area(p), 0) / Math.max(target, 1), queue = new Heap();
    function push(a: number, b: number) {
        const i = Math.min(a, b), j = Math.max(a, b), left = groups.get(i)!, right = groups.get(j)!, areaA = s.area(left.geom), areaB = s.area(right.geom);
        if (areaA + areaB > maxArea + 1)
            return;
        const edge = boundary(s, left.geom), other = s.own(s.g.GEOSBuffer(boundary(s, right.geom), .002, 16)), shared = s.length(s.own(s.g.GEOSIntersection(edge, other))), perimeter = outerPerimeter(s, left.geom) + outerPerimeter(s, right.geom) - 2 * shared;
        const compactness = perimeter ** 2 / (4 * Math.PI * (areaA + areaB)), contact = shared / Math.max(Math.sqrt(Math.min(areaA, areaB)), 1), storeCost = 2 * left.count * right.count / targetStores ** 2, score = 2 * areaA * areaB / Math.max(targetArea ** 2, 1) + .35 * storeCost + (hasStores ? .8 : .04) * compactness + (hasStores ? .25 / Math.max(contact, .01) : 0);
        queue.push([score, i, j, left.version, right.version]);
    }
    for (const [i, adjacent] of graph)
        for (const j of adjacent)
            if (j > i)
                push(i, j);
    while (groups.size > target && queue.rows.length) {
        const [, i, j, vi, vj] = queue.pop(), a = groups.get(i), b = groups.get(j);
        if (!a || !b || a.version !== vi || b.version !== vj)
            continue;
        const merged = union(s, [a.geom, b.geom]);
        if (s.g.GEOSGeomTypeId(merged) !== 3)
            continue;
        a.geom = merged;
        a.members = new Set([...a.members, ...b.members]);
        a.version++;
        a.count += b.count;
        const adjacent = new Set([...graph.get(i)!, ...graph.get(j)!].filter(k => k !== i && k !== j));
        groups.delete(j);
        for (const k of adjacent) {
            graph.get(k)!.delete(j);
            graph.get(k)!.delete(i);
            graph.get(k)!.add(i);
        }
        graph.set(i, adjacent);
        graph.delete(j);
        for (const k of adjacent)
            push(i, k);
    }
    return [...groups.values()];
}
export function coastLand(s: Scope, border: number, coasts: JsonGeometry[]) {
    border = s.own(s.g.GEOSGeom_setPrecision(border, 0, 0));
    const lines = coasts.map(line => s.read(project(line))).filter(p => s.g.GEOSIntersects(p, border) === 1);
    if (!lines.length)
        return border;
    const params = s.g.GEOSBufferParams_create();
    if (!params)
        throw new ZoneSnapshotError('海岸线缓冲创建失败');
    let left: number, right: number;
    try {
        s.g.GEOSBufferParams_setSingleSided(params, 1);
        s.g.GEOSBufferParams_setQuadrantSegments(params, 16);
        left = union(s, lines.map(p => s.own(s.g.GEOSBufferWithParams(p, params, 1))), 0);
        right = union(s, lines.map(p => s.own(s.g.GEOSBufferWithParams(p, params, -1))), 0);
    }
    finally {
        s.g.GEOSBufferParams_destroy(params);
    }
    const faces = polygonize(s, union(s, [boundary(s, border), ...lines], 0)), kept = faces.filter(p => s.g.GEOSCovers(border, s.own(s.g.GEOSPointOnSurface(p))) === 1 && s.area(s.own(s.g.GEOSIntersection(p, left))) > s.area(s.own(s.g.GEOSIntersection(p, right))));
    if (!kept.length)
        throw new ZoneSnapshotError('海岸线无法确认有效陆地范围，停止生成');
    return s.own(s.g.GEOSGeom_setPrecision(union(s, kept, 0), .1, 0));
}
function roundEven(value: number) { const floor = Math.floor(value), fraction = value - floor; return fraction === .5 ? (floor % 2 ? floor + 1 : floor) : Math.round(value); }
function stable(value: any): string {
    if (Array.isArray(value))
        return '[' + value.map(stable).join(',') + ']';
    if (value && typeof value === 'object')
        return '{' + Object.keys(value).sort().map(key => JSON.stringify(key) + ':' + stable(value[key])).join(',') + '}';
    return JSON.stringify(value);
}
export async function prepareZoneSnapshot(input: ZoneSnapshotInput) {
    const ward = input.code === 'tokyo23' ? '东京23区整体' : /^131(?:0[1-9]|1[0-9]|2[0-3])$/.test(input.code) ? WARDS[Number(input.code) - 13101] : undefined;
    if (!ward)
        throw new ZoneSnapshotError('请选择东京23区之一');
    return arena(s => {
        const border = validProjected(s, input.boundary), masks = input.excluded.map(g => validProjected(s, g));
        let land = coastLand(s, border, input.coastlines ?? []);
        if (masks.length)
            land = s.own(s.g.GEOSDifference(land, union(s, masks, 0)));
        land = s.own(s.g.GEOSGeom_setPrecision(s.own(s.g.GEOSMakeValid(land)), 0, 0));
        const segments = [boundary(s, land)];
        for (const road of input.roads) {
            const line = s.read(project(road));
            if (s.g.GEOSisEmpty(line) === 0 && s.g.GEOSIntersects(line, land) === 1)
                segments.push(line);
        }
        const atoms = polygonize(s, union(s, segments)).filter(p => s.area(p) > .01 && s.g.GEOSCovers(land, s.own(s.g.GEOSPointOnSurface(p))) === 1);
        atoms.sort((a, b) => { const x = centroid(s, a), y = centroid(s, b); return x[0] - y[0] || x[1] - y[1] || s.area(a) - s.area(b); });
        if (!atoms.length)
            throw new ZoneSnapshotError('行政边界内没有可用道路街块');
        const counts = atoms.map(() => 0), tree = index(s, atoms);
        let located = 0;
        try {
            for (const coordinates of input.merchants) {
                const point = s.read(project({ type: 'Point', coordinates }));
                const hits = tree.query(point).filter(i => s.g.GEOSIntersects(atoms[i], point) === 1);
                if (hits.length) {
                    counts[Math.min(...hits)]++;
                    located++;
                }
            }
        }
        finally {
            tree.close();
        }
        const graph = neighbours(s, atoms), unseen = new Set(graph.keys());
        let count = 0;
        while (unseen.size) {
            const pending = [Math.min(...unseen)], component = new Set<number>();
            while (pending.length) {
                const i = pending.pop()!;
                if (component.has(i))
                    continue;
                component.add(i);
                for (const j of graph.get(i)!)
                    if (!component.has(j))
                        pending.push(j);
            }
            for (const i of component)
                unseen.delete(i);
            count += Math.max(1, roundEven([...component].reduce((area, i) => area + s.area(atoms[i]), 0) / 1000000));
        }
        const groups = groupAtoms(s, atoms, count);
        for (let attempt = 0; attempt < atoms.length; attempt++) {
            const small = groups.map((g, i) => s.area(g.geom) < MIN_GRID - 1 ? i : -1).filter(i => i >= 0);
            if (!small.length)
                break;
            const adjacent = neighbours(s, groups.map(g => g.geom));
            let merged = false;
            for (const i of small) {
                const choices = [...adjacent.get(i)!].filter(j => s.area(groups[i].geom) + s.area(groups[j].geom) <= MAX_GRID + 1).sort((a, b) => s.area(groups[a].geom) - s.area(groups[b].geom) || a - b);
                if (!choices.length)
                    continue;
                const j = choices[0];
                groups[j].geom = union(s, [groups[j].geom, groups[i].geom], 0);
                for (const member of groups[i].members)
                    groups[j].members.add(member);
                groups.splice(i, 1);
                merged = true;
                break;
            }
            if (!merged)
                break;
        }
        groups.sort((a, b) => { const x = centroid(s, a.geom), y = centroid(s, b.geom); return x[0] - y[0] || x[1] - y[1]; });
        const referenceIds = new Map<string, string>(), referenceNames = new Set<string>();
        if (input.referenceSnapshot) {
            if (input.referenceSnapshot.ward_code !== input.code)
                throw new ZoneSnapshotError('参考快照行政区不一致');
            for (const grid of input.referenceSnapshot.grids) {
                if (typeof grid.id !== 'string' || !grid.id || grid.id.length > 160)
                    throw new ZoneSnapshotError('参考网格ID无效');
                if (referenceNames.has(grid.id))
                    throw new ZoneSnapshotError('参考快照存在重复网格ID');
                referenceNames.add(grid.id);
                const key = normalizedWkb(s, validProjected(s, grid.geometry));
                if (referenceIds.has(key))
                    throw new ZoneSnapshotError('参考快照存在重复网格边界');
                referenceIds.set(key, grid.id);
            }
        }
        const grids = groups.map(g => { const key = normalizedWkb(s, g.geom); return { id: referenceIds.get(key) ?? input.code + '-G-' + hash(Buffer.from(key, 'hex')).slice(0, 10), geometry: project(s.json(g.geom), true) as GridGeometry, area_km2: s.area(g.geom) / 1000000, store_count: [...g.members].reduce((sum, i) => sum + counts[i], 0) }; });
        const issues: ({
            code: string;
            count: number;
        } | {
            code: string;
            area_km2: number;
        })[] = [];
        const invalid = grids.filter(g => g.area_km2 * 1000000 < MIN_GRID - 1 || g.area_km2 * 1000000 > MAX_GRID + 1).length;
        if (invalid)
            issues.push({ code: 'grid_area', count: invalid });
        const missing = s.area(land) - groups.reduce((area, g) => area + s.area(g.geom), 0);
        if (Math.abs(missing) > Math.max(10, s.area(land) * .0001))
            issues.push({ code: 'coverage', area_km2: missing / 1000000 });
        return { schema_version: 1, generation_version: 4, ward_code: input.code, ward_name: ward, snapshot_id: hash(stable(grids)).slice(0, 12), id_reference_snapshot: input.referenceSnapshot?.snapshot_id ?? null, captured_at: input.capturedAt, source: input.source, coastline_applied: true, boundary: project(s.json(border), true) as GridGeometry, work_area: project(s.json(land), true) as GridGeometry, area_km2: s.area(land) / 1000000, excluded_area_km2: (s.area(border) - s.area(land)) / 1000000, store_count: located, grids, issues, road_count: input.roads.length };
    });
}
