/** Nonlinear workload partition kernel; coverage/publish eligibility is a separate stage. */
import { arena, union, shapeMetrics, type Scope, type GridGeometry, type GridPolygon, type GridPoint } from './grid-geometry';
import { centroid, ZoneSnapshotError } from './zone-snapshot';
export interface BalanceConfig {
    targetAreaKm2?: number;
    targetStores?: number;
    areaWeight?: number;
    loadMin?: number;
    loadMax?: number;
}
export class BalanceOptions {
    readonly targetAreaKm2: number;
    readonly targetStores: number;
    readonly areaWeight: number;
    readonly loadMin: number;
    readonly loadMax: number;
    constructor(config: BalanceConfig = {}) { this.targetAreaKm2 = config.targetAreaKm2 === undefined ? 25 : config.targetAreaKm2; this.targetStores = config.targetStores === undefined ? 8000 : config.targetStores; this.areaWeight = config.areaWeight === undefined ? .15 : config.areaWeight; this.loadMin = config.loadMin === undefined ? .75 : config.loadMin; this.loadMax = config.loadMax === undefined ? 1.35 : config.loadMax; this.validate(); Object.freeze(this); }
    validate() {
        const values = [this.targetAreaKm2, this.targetStores, this.areaWeight, this.loadMin, this.loadMax];
        if (values.some(v => typeof v !== 'number' || !Number.isFinite(v)) || this.targetAreaKm2 < 2 || this.targetAreaKm2 > 500 || this.targetStores < 1 || this.targetStores > 100000 || this.areaWeight < .05 || this.areaWeight > .95 || this.loadMin < .1 || this.loadMin >= this.loadMax || this.loadMax > 4)
            throw new ZoneSnapshotError('综合负载参数超出范围：权重须在5%～95%，负载下限必须小于上限。');
        return this;
    }
    score(area: number, stores: number) { return this.areaWeight * (area / (this.targetAreaKm2 * 1e6)) ** 2 + (1 - this.areaWeight) * (stores / this.targetStores) ** 2; }
    marginal(area: number, stores: number, addedArea: number, addedStores: number) { return this.score(area + addedArea, stores + addedStores) - this.score(area, stores); }
    seedCount(area: number, stores: number, faces: number) {
        const total = this.score(area, stores);
        if (total <= this.loadMax)
            return 1;
        const preferred = Math.min(1, (this.loadMin + 1) / 2);
        return Math.min(faces, Math.max(1, Math.ceil(Math.sqrt(total / preferred))));
    }
}
export type FaceGraph = Map<number, Set<number>>;
const sorted = (part: Iterable<number>) => [...part].sort((a, b) => a - b);
// Neumaier compensated sum matches the legacy Python 3.12 floating aggregation.
export function sum(values: Iterable<number>) {
    let high = 0, low = 0;
    for (const value of values) {
        const next = high + value;
        low += Math.abs(high) >= Math.abs(value) ? (high - next) + value : (value - next) + high;
        high = next;
    }
    return low && Number.isFinite(low) ? high + low : high;
}
const total = (part: Set<number>, values: number[]) => sum([...part].map(i => values[i]));
const key = (part: Set<number>) => sorted(part).join(',');
export function compare(a: number[], b: number[]) {
    for (let i = 0; i < Math.min(a.length, b.length); i++)
        if (a[i] !== b[i])
            return a[i] - b[i];
    return a.length - b.length;
}
export class Heap {
    rows: number[][] = [];
    push(row: number[]) {
        const a = this.rows;
        a.push(row);
        let i = a.length - 1;
        while (i) {
            const p = (i - 1) >> 1;
            if (compare(a[p], row) <= 0)
                break;
            a[i] = a[p];
            i = p;
        }
        a[i] = row;
    }
    pop() {
        const a = this.rows, first = a[0], last = a.pop()!;
        if (a.length) {
            let i = 0;
            while (i * 2 + 1 < a.length) {
                let c = i * 2 + 1;
                if (c + 1 < a.length && compare(a[c + 1], a[c]) < 0)
                    c++;
                if (compare(last, a[c]) <= 0)
                    break;
                a[i] = a[c];
                i = c;
            }
            a[i] = last;
        }
        return first;
    }
}
const distance = (a: GridPoint, b: GridPoint) => Math.hypot(a[0] - b[0], a[1] - b[1]);
export function connectedFaces(graph: FaceGraph, part: Set<number>, removed?: number) {
    const remaining = removed === undefined ? part : new Set([...part].filter(i => i !== removed));
    if (!remaining.size)
        return false;
    const seen = new Set<number>(), todo = [Math.min(...remaining)];
    while (todo.length) {
        const i = todo.pop()!;
        if (seen.has(i))
            continue;
        seen.add(i);
        for (const j of graph.get(i)!)
            if (remaining.has(j) && !seen.has(j))
                todo.push(j);
    }
    return seen.size === remaining.size;
}
function removalConnected(graph: FaceGraph, part: Set<number>, removed: number) {
    if (part.size <= 1)
        return false;
    const neighbours = new Set([...graph.get(removed)!].filter(i => part.has(i) && i !== removed));
    if (neighbours.size <= 1)
        return true;
    const allowed = new Set(neighbours);
    for (let round = 0; round < 2; round++)
        for (const i of [...allowed])
            for (const j of graph.get(i)!)
                if (part.has(j) && j !== removed)
                    allowed.add(j);
    const seen = new Set<number>(), todo = [Math.min(...neighbours)];
    while (todo.length) {
        const i = todo.pop()!;
        if (seen.has(i))
            continue;
        seen.add(i);
        for (const j of graph.get(i)!)
            if (allowed.has(j) && !seen.has(j))
                todo.push(j);
    }
    return [...neighbours].every(i => seen.has(i)) || connectedFaces(graph, part, removed);
}
export function components(graph: FaceGraph, initial?: Set<number>) {
    const allowed = initial ?? new Set(graph.keys()), unseen = new Set(allowed), parts: Set<number>[] = [];
    while (unseen.size) {
        const part = new Set<number>(), todo = [Math.min(...unseen)];
        while (todo.length) {
            const i = todo.pop()!;
            if (part.has(i))
                continue;
            part.add(i);
            for (const j of graph.get(i)!)
                if (allowed.has(j) && !part.has(j))
                    todo.push(j);
        }
        for (const i of part)
            unseen.delete(i);
        parts.push(part);
    }
    return parts;
}
/** Exact shared coordinate segments, not proximity or corner contact. */
export function faceGraph(geometries: GridGeometry[]): FaceGraph {
    const owners = new Map<string, number>(), graph = new Map(geometries.map((_, i) => [i, new Set<number>()]));
    geometries.forEach((g, i) => {
        if (g.type !== 'Polygon')
            throw new ZoneSnapshotError('道路基础面必须为连续多边形');
        for (const ring of g.coordinates)
            for (let k = 1; k < ring.length; k++) {
                const a = ring[k - 1].map(x => Math.round(x * 1000) / 1000), b = ring[k].map(x => Math.round(x * 1000) / 1000);
                if (a[0] === b[0] && a[1] === b[1])
                    continue;
                const edge = JSON.stringify(compare(a, b) < 0 ? [a, b] : [b, a]), other = owners.get(edge);
                if (other !== undefined && other !== i) {
                    graph.get(i)!.add(other);
                    graph.get(other)!.add(i);
                }
                else if (other === undefined)
                    owners.set(edge, i);
            }
    });
    return graph;
}
export function grow(graph: FaceGraph, part: Set<number>, centers: GridPoint[], seeds: number[], multipliers = seeds.map(() => 1)) {
    const owners = new Map(seeds.map((i, k) => [i, k])), groups = seeds.map(i => new Set([i])), queue = new Heap();
    seeds.forEach((seed, owner) => {
        for (const j of graph.get(seed)!)
            if (part.has(j) && !owners.has(j)) {
                const d = Math.max(distance(centers[seed], centers[j]), .001);
                queue.push([d * multipliers[owner], d, owner, j]);
            }
    });
    while (queue.rows.length) {
        const [, d, owner, i] = queue.pop();
        if (owners.has(i))
            continue;
        owners.set(i, owner);
        groups[owner].add(i);
        for (const j of graph.get(i)!)
            if (part.has(j) && !owners.has(j)) {
                const next = d + Math.max(distance(centers[i], centers[j]), .001);
                queue.push([next * multipliers[owner], next, owner, j]);
            }
    }
    return groups;
}
function massCenter(part: Set<number>, centers: GridPoint[], areas: number[], counts: number[], options: BalanceOptions) { const indices = [...part], mass = indices.map(i => options.areaWeight * areas[i] / (options.targetAreaKm2 * 1e6) + (1 - options.areaWeight) * counts[i] / options.targetStores), amount = Math.max(sum(mass), 1e-12); return [sum(indices.map((i, k) => mass[k] * centers[i][0])) / amount, sum(indices.map((i, k) => mass[k] * centers[i][1])) / amount] as GridPoint; }
function nearest(part: Set<number>, center: GridPoint, centers: GridPoint[]) { return sorted(part).sort((a, b) => distance(centers[a], center) - distance(centers[b], center) || a - b)[0]; }
function seedsFor(part: Set<number>, centers: GridPoint[], areas: number[], counts: number[], number: number, options: BalanceOptions) {
    const indices = sorted(part), mass = new Map(indices.map(i => [i, options.areaWeight * areas[i] / (options.targetAreaKm2 * 1e6) + (1 - options.areaWeight) * counts[i] / options.targetStores])), amount = sum(mass.values());
    if (amount <= 0)
        return [indices[0]];
    const center = [sum(indices.map(i => mass.get(i)! * centers[i][0])) / amount, sum(indices.map(i => mass.get(i)! * centers[i][1])) / amount] as GridPoint, seeds = [nearest(part, center, centers)], distances = new Map(indices.map(i => [i, distance(centers[i], centers[seeds[0]])]));
    while (seeds.length < number) {
        const candidates = indices.filter(i => !seeds.includes(i)).sort((a, b) => distances.get(b)! ** 2 * Math.sqrt(Math.max(mass.get(b)!, 1e-12)) - distances.get(a)! ** 2 * Math.sqrt(Math.max(mass.get(a)!, 1e-12)) || a - b), seed = candidates[0];
        seeds.push(seed);
        for (const i of indices)
            distances.set(i, Math.min(distances.get(i)!, distance(centers[i], centers[seed])));
    }
    return seeds;
}
const penalty = (load: number, o: BalanceOptions) => 8 * Math.max(0, load - o.loadMax) ** 2 + .5 * Math.max(0, o.loadMin - load) ** 2;
function joined(s: Scope, part: Set<number>, geoms: number[]) { return union(s, sorted(part).map(i => geoms[i])); }
export function narrowNeck(s: Scope, p: number) {
    if (s.g.GEOSGeomTypeId(p) !== 3)
        return true;
    const shell = s.read({ type: 'Polygon', coordinates: [(s.json(p) as GridPolygon).coordinates[0]] }), area = s.area(shell), eroded = s.own(s.g.GEOSBuffer(shell, -Math.min(150, Math.sqrt(area) * .06), 16));
    const pieces = (value: number): number[] => s.g.GEOSisEmpty(value) ? [] : s.g.GEOSGeomTypeId(value) === 3 ? [value] : Array.from({ length: s.g.GEOSGetNumGeometries(value) }, (_, i) => pieces(s.g.GEOSGetGeometryN(value, i))).flat();
    return pieces(eroded).filter(value => s.area(value) > area * .04).length > 1;
}
function rebalance(s: Scope, original: Set<number>[], graph: FaceGraph, areas: number[], counts: number[], o: BalanceOptions, geoms: number[]) {
    const groups = original.map(g => new Set(g)), owners = new Map<number, number>();
    groups.forEach((g, k) => g.forEach(i => owners.set(i, k)));
    const amounts = groups.map(g => total(g, areas)), merchants = groups.map(g => total(g, counts)), perimeters = groups.map(g => s.length(joined(s, g, geoms))), edges = new Map<string, number>();
    const contact = (i: number, j: number) => {
        const k = [Math.min(i, j), Math.max(i, j)].join(':');
        if (!edges.has(k))
            edges.set(k, s.length(s.own(s.g.GEOSIntersection(s.own(s.g.GEOSBoundary(geoms[i])), s.own(s.g.GEOSBoundary(geoms[j]))))));
        return edges.get(k)!;
    };
    for (let round = 0; round < 12; round++) {
        const loads = amounts.map((a, k) => o.score(a, merchants[k]));
        if (loads.every(load => load >= o.loadMin && load <= o.loadMax))
            break;
        let changed = 0;
        for (const i of sorted(owners.keys())) {
            const source = owners.get(i)!;
            if (groups[source].size <= 1)
                continue;
            const adjacent = [...new Set([...graph.get(i)!].filter(j => owners.has(j) && owners.get(j) !== source).map(j => owners.get(j)!))].sort((a, b) => a - b), current = o.score(amounts[source], merchants[source]), choices: number[][] = [];
            for (const target of adjacent) {
                const other = o.score(amounts[target], merchants[target]), sa = amounts[source] - areas[i], sn = merchants[source] - counts[i], ta = amounts[target] + areas[i], tn = merchants[target] + counts[i], afterSource = o.score(sa, sn), afterTarget = o.score(ta, tn);
                if (Math.max(afterSource - o.loadMax, 0) + Math.max(afterTarget - o.loadMax, 0) > Math.max(current - o.loadMax, 0) + Math.max(other - o.loadMax, 0) + 1e-8)
                    continue;
                const before = penalty(current, o) + penalty(other, o);
                let after = penalty(afterSource, o) + penalty(afterTarget, o);
                if (before - after <= 1e-5)
                    continue;
                const sourceEdge = sum([...graph.get(i)!].filter(j => owners.get(j) === source).map(j => contact(i, j))), targetEdge = sum([...graph.get(i)!].filter(j => owners.get(j) === target).map(j => contact(i, j))), nextSource = perimeters[source] - s.length(geoms[i]) + 2 * sourceEdge, nextTarget = perimeters[target] + s.length(geoms[i]) - 2 * targetEdge, shapeBefore = perimeters[source] ** 2 / Math.max(amounts[source], 1) + perimeters[target] ** 2 / Math.max(amounts[target], 1), shapeAfter = nextSource ** 2 / Math.max(sa, 1) + nextTarget ** 2 / Math.max(ta, 1);
                after += .04 * Math.max(0, shapeAfter - shapeBefore);
                if (before - after > 1e-5)
                    choices.push([after - before, target, nextSource, nextTarget]);
            }
            if (choices.length && removalConnected(graph, groups[source], i)) {
                const [, target, nextSource, nextTarget] = choices.sort(compare)[0];
                perimeters[source] = nextSource;
                perimeters[target] = nextTarget;
                groups[source].delete(i);
                groups[target].add(i);
                owners.set(i, target);
                amounts[source] -= areas[i];
                merchants[source] -= counts[i];
                amounts[target] += areas[i];
                merchants[target] += counts[i];
                if (++changed >= 1500)
                    break;
            }
        }
        if (!changed)
            break;
    }
    return groups;
}
function mergeResiduals(s: Scope, original: Set<number>[], graph: FaceGraph, areas: number[], counts: number[], o: BalanceOptions, geoms: number[]) {
    const groups = original.map(g => new Set(g));
    for (;;) {
        const owners = new Map<number, number>();
        groups.forEach((g, k) => g.forEach(i => owners.set(i, k)));
        const stats = groups.map(g => [total(g, areas), total(g, counts)]);
        let changed = false;
        for (const source of groups.map((_, i) => i).sort((a, b) => o.score(stats[a][0], stats[a][1]) - o.score(stats[b][0], stats[b][1]))) {
            if (!groups[source].size || o.score(stats[source][0], stats[source][1]) >= o.loadMin)
                continue;
            const adjacent = new Set([...groups[source]].flatMap(i => [...graph.get(i)!].filter(j => owners.has(j) && owners.get(j) !== source).map(j => owners.get(j)!))), feasible: number[][] = [];
            for (const target of adjacent) {
                const load = o.score(stats[source][0] + stats[target][0], stats[source][1] + stats[target][1]);
                if (load <= o.loadMax && shapeMetrics(s, joined(s, new Set([...groups[source], ...groups[target]]), geoms)).acceptable)
                    feasible.push([Math.abs(load - 1), o.score(stats[target][0], stats[target][1]), target]);
            }
            if (feasible.length) {
                const target = feasible.sort(compare)[0][2];
                for (const i of groups[source])
                    groups[target].add(i);
                groups[source].clear();
                changed = true;
                break;
            }
        }
        if (!changed)
            return groups.filter(g => g.size);
    }
}
function repairPairs(s: Scope, original: Set<number>[], graph: FaceGraph, centers: GridPoint[], areas: number[], counts: number[], o: BalanceOptions, geoms: number[]) {
    const groups = original.map(g => new Set(g)), cache = new Map<string, number>(), geom = (part: Set<number>) => {
        const k = key(part);
        if (!cache.has(k))
            cache.set(k, joined(s, part, geoms));
        return cache.get(k)!;
    }, shapeCost = (part: Set<number>) => {
        const p = geom(part), q = shapeMetrics(s, p);
        if (!Number.isFinite(q.aspect))
            return 100;
        return 2 * Number(!q.acceptable) + 12 * Math.max(0, .75 - q.solidity) ** 2 + .8 * Math.max(0, q.aspect - 2.2) ** 2 + 6 * Math.max(0, .28 - q.compactness) ** 2 + .8 * Number(narrowNeck(s, p));
    }, cost = (part: Set<number>) => penalty(o.score(total(part, areas), total(part, counts)), o) + shapeCost(part);
    for (let pass = 0; pass < 3; pass++) {
        const owners = new Map<number, number>();
        groups.forEach((g, k) => g.forEach(i => owners.set(i, k)));
        const values = groups.map(cost), troublesome = groups.map((_, i) => i).filter(k => o.score(total(groups[k], areas), total(groups[k], counts)) < o.loadMin || o.score(total(groups[k], areas), total(groups[k], counts)) > o.loadMax || shapeCost(groups[k]) > 0), used = new Set<number>();
        let changed = 0;
        for (const source of troublesome.sort((a, b) => values[b] - values[a] || a - b)) {
            if (used.has(source))
                continue;
            const adjacent = new Set([...groups[source]].flatMap(i => [...graph.get(i)!].filter(j => owners.has(j) && owners.get(j) !== source).map(j => owners.get(j)!)));
            let best: {
                delta: number;
                target: number;
                trial: Set<number>[];
            } | undefined;
            for (const target of [...adjacent].sort((a, b) => values[b] - values[a] || a - b).slice(0, 3)) {
                if (used.has(target))
                    continue;
                const part = new Set([...groups[source], ...groups[target]]), seeds = [groups[source], groups[target]].map(g => nearest(g, massCenter(g, centers, areas, counts, o), centers));
                let low = -1.6, high = 1.6;
                const candidates: {
                    proxy: number;
                    step: number;
                    trial: Set<number>[];
                }[] = [];
                for (let step = 0; step < 9; step++) {
                    const middle = (low + high) / 2, trial = grow(graph, part, centers, seeds, [Math.exp(middle), 1]), loads = trial.map(g => o.score(total(g, areas), total(g, counts)));
                    candidates.push({ proxy: sum(loads.map(load => penalty(load, o))), step, trial });
                    if (loads[0] > loads[1])
                        low = middle;
                    else
                        high = middle;
                }
                const before = values[source] + values[target];
                for (const { trial } of candidates.sort((a, b) => a.proxy - b.proxy || a.step - b.step).slice(0, 4)) {
                    const after = sum(trial.map(cost)), oldBad = [groups[source], groups[target]].filter(g => !shapeMetrics(s, geom(g)).acceptable).length, newBad = trial.filter(g => !shapeMetrics(s, geom(g)).acceptable).length, excess = (parts: Set<number>[]) => sum(parts.map(g => Math.max(0, o.score(total(g, areas), total(g, counts)) - o.loadMax)));
                    if (excess(trial) > excess([groups[source], groups[target]]) + 1e-8 || newBad > oldBad)
                        continue;
                    if (after < before - .01 && (!best || after - before < best.delta))
                        best = { delta: after - before, target, trial };
                }
            }
            if (best) {
                groups[source] = best.trial[0];
                groups[best.target] = best.trial[1];
                used.add(source);
                used.add(best.target);
                if (++changed >= 12)
                    break;
            }
        }
        if (!changed)
            break;
    }
    return groups;
}
export function geodesicCandidates(graph: FaceGraph, part: Set<number>, centers: GridPoint[]) {
    const seen = new Set<string>(), out: [
        Set<number>,
        Set<number>
    ][] = [];
    for (let direction = 0; direction < 8; direction++) {
        const angle = direction * Math.PI / 8, dx = Math.cos(angle), dy = Math.sin(angle), ordered = sorted(part).sort((a, b) => (centers[a][0] * dx + centers[a][1] * dy) - (centers[b][0] * dx + centers[b][1] * dy) || a - b), a = ordered[0], b = ordered.at(-1)!, signature = [Math.min(a, b), Math.max(a, b)].join(':');
        if (a === b || seen.has(signature))
            continue;
        seen.add(signature);
        const owned = new Map<number, number>(), queue = new Heap();
        queue.push([0, 0, a]);
        queue.push([0, 1, b]);
        while (queue.rows.length) {
            const [d, owner, i] = queue.pop();
            if (owned.has(i))
                continue;
            owned.set(i, owner);
            for (const j of graph.get(i)!)
                if (part.has(j) && !owned.has(j))
                    queue.push([d + Math.max(distance(centers[i], centers[j]), .001), owner, j]);
        }
        const left = new Set([...owned].filter(([, owner]) => owner === 0).map(([i]) => i)), right = new Set([...part].filter(i => !left.has(i)));
        if (left.size && right.size)
            out.push([left, right]);
    }
    return out;
}
export function partitionBalancedFaces(s: Scope, geoms: number[], counts: number[], graph: FaceGraph, o: BalanceOptions) {
    o.validate();
    const areas = geoms.map(p => s.area(p)), centers = geoms.map(p => centroid(s, p)), output: Set<number>[] = [], pending = components(graph);
    while (pending.length) {
        const part = pending.shift()!, a = total(part, areas), n = total(part, counts);
        let number = o.seedCount(a, n, part.size);
        if (number === 1) {
            const p = joined(s, part, geoms), quality = shapeMetrics(s, p);
            if (n > 0 && part.size > 1 && a >= 2e6 && (quality.aspect > 3 || narrowNeck(s, p)))
                number = 2;
            else {
                output.push(part);
                continue;
            }
        }
        if (number === part.size) {
            output.push(...sorted(part).map(i => new Set([i])));
            continue;
        }
        if (number > 128) {
            const choices = geodesicCandidates(graph, part, centers).map(([left, right]) => ({ left, right, imbalance: Math.abs(Math.sqrt(o.score(total(left, areas), total(left, counts))) - Math.sqrt(o.score(a - total(left, areas), n - total(left, counts)))) }));
            if (choices.length) {
                const choice = choices.sort((a, b) => a.imbalance - b.imbalance || compare(sorted(a.left), sorted(b.left)))[0];
                pending.push(choice.left, choice.right);
            }
            else
                output.push(part);
            continue;
        }
        let seeds = seedsFor(part, centers, areas, counts, number, o), multipliers = seeds.map(() => 1);
        let finalists: {
            cost: number;
            step: number;
            groups: Set<number>[];
        }[] = [];
        for (let step = 0; step < 40; step++) {
            const groups = grow(graph, part, centers, seeds, multipliers), loads = groups.map(g => o.score(total(g, areas), total(g, counts)));
            let concavity = 0;
            for (const group of groups) {
                const area = total(group, areas), points = sorted(group).map(i => centers[i]), hull = s.area(s.own(s.g.GEOSConvexHull(s.read({ type: 'MultiPoint', coordinates: points }))));
                concavity += 6 * Math.max(0, .75 - area / Math.max(hull, area, 1)) ** 2;
                const xs = [...group].map(i => centers[i][0]), ys = [...group].map(i => centers[i][1]), mx = sum(xs) / xs.length, my = sum(ys) / ys.length, vx = sum(xs.map(x => (x - mx) ** 2)) / xs.length, vy = sum(ys.map(y => (y - my) ** 2)) / ys.length, cv = sum(xs.map((x, k) => (x - mx) * (ys[k] - my))) / xs.length, delta = Math.hypot(vx - vy, 2 * cv), aspect = Math.sqrt(Math.max(vx + vy + delta, 1) / Math.max(vx + vy - delta, 1));
                concavity += .15 * Math.max(0, Math.min(aspect, 10) - 2.2) ** 2;
            }
            const cost = sum(loads.map(load => penalty(load, o))) + 2 * concavity;
            if (finalists.length < 3 || cost < finalists.at(-1)!.cost) {
                finalists.push({ cost, step, groups: groups.map(g => new Set(g)) });
                finalists.sort((a, b) => a.cost - b.cost || a.step - b.step);
                finalists = finalists.slice(0, 3);
            }
            if (loads.every(load => load >= o.loadMin && load <= o.loadMax) && concavity === 0)
                break;
            const average = sum(loads.map(load => Math.sqrt(Math.max(load, .0001)))) / number, eta = .18 / (1 + step / 30);
            multipliers = multipliers.map((v, k) => Math.max(.15, Math.min(6, v * (Math.sqrt(Math.max(loads[k], .001)) / average) ** eta)));
            if (step % 3 === 2)
                seeds = groups.map(g => nearest(g, massCenter(g, centers, areas, counts, o), centers));
        }
        const evaluated = finalists.map(row => ({ ...row, score: row.cost + 2 * row.groups.filter(g => !shapeMetrics(s, joined(s, g, geoms)).acceptable).length })).sort((a, b) => a.score - b.score || a.step - b.step);
        let groups = rebalance(s, evaluated[0].groups, graph, areas, counts, o, geoms);
        groups = repairPairs(s, groups, graph, centers, areas, counts, o, geoms);
        output.push(...mergeResiduals(s, groups, graph, areas, counts, o, geoms));
    }
    return output;
}
export async function balanceScopeFaces(metricGeometries: GridGeometry[], counts: number[], config: BalanceConfig = {}, topology?: number[][]) {
    const o = new BalanceOptions(config);
    if (metricGeometries.length !== counts.length || counts.some(n => !Number.isFinite(n) || n < 0))
        throw new ZoneSnapshotError('道路面商户统计无效');
    return arena(s => {
        const geoms = metricGeometries.map(g => {
            const p = s.own(s.g.GEOSGeom_setPrecision(s.read(g), .001, 0));
            if (s.g.GEOSisValid(p) !== 1 || s.g.GEOSisEmpty(p) !== 0 || s.area(p) <= 0)
                throw new ZoneSnapshotError('道路基础面无效');
            return p;
        });
        const graph = topology ? new Map(topology.map((adjacent, i) => [i, new Set(adjacent)])) : faceGraph(geoms.map(p => s.json(p) as GridGeometry));
        if (graph.size !== counts.length || [...graph].some(([i, edges]) => [...edges].some(j => !Number.isInteger(j) || j < 0 || j >= counts.length || j === i || !graph.get(j)?.has(i))))
            throw new ZoneSnapshotError('道路面拓扑无效');
        const parts = partitionBalancedFaces(s, geoms, counts, graph, o);
        return parts.map(part => { const p = joined(s, part, geoms), area = s.area(p), stores = total(part, counts); return { members: sorted(part), geometry: s.json(p) as GridGeometry, areaM2: area, stores, workload: o.score(area, stores) }; });
    });
}
