/** Whole-face region/task subdivision, with legacy scalar proxies and real-geometry refinement. */
import { union, shapeMetrics, type Scope, type GridPoint, type GridGeometry } from './grid-geometry';
import { centroid } from './zone-snapshot';
import { components, connectedFaces, geodesicCandidates, grow, Heap, compare, sum, narrowNeck, type FaceGraph } from './zone-balance';
import { rounded } from './zone-preview';
type Cut = {
    score: number;
    ids: number[];
    left: Set<number>;
    right: Set<number>;
};
export interface ScopePart {
    members: Set<number>;
    geom: number;
    count: number;
}
export interface ScopeTask extends ScopePart {
    regionIndex: number;
}
const sorted = (part: Iterable<number>) => [...part].sort((a, b) => a - b), key = (part: Set<number>) => sorted(part).join(','), total = (part: Set<number>, values: number[]) => sum([...part].map(i => values[i]));
const rank = (a: Cut, b: Cut) => a.score - b.score || compare(a.ids, b.ids), dist = (a: GridPoint, b: GridPoint) => Math.hypot(a[0] - b[0], a[1] - b[1]);
function treeCandidates(graph: FaceGraph, part: Set<number>, areas: number[], centers: GridPoint[], nl: number, nr: number, ratio: number): Cut[] {
    const size = total(part, areas), root = sorted(part).sort((a, b) => centers[a][0] - centers[b][0] || centers[a][1] - centers[b][1])[0], parent = new Map<number, number | null>([[root, null]]), order: number[] = [], todo = [root];
    for (let pos = 0; pos < todo.length; pos++) {
        const i = todo[pos];
        order.push(i);
        for (const j of sorted(graph.get(i)!))
            if (part.has(j) && !parent.has(j)) {
                parent.set(j, i);
                todo.push(j);
            }
    }
    const children = new Map([...part].map(i => [i, [] as number[]])), sizes = new Map([...part].map(i => [i, 1])), weights = new Map([...part].map(i => [i, areas[i]]));
    for (const i of [...order].reverse()) {
        const p = parent.get(i)!;
        if (p !== null) {
            children.get(p)!.push(i);
            sizes.set(p, sizes.get(p)! + sizes.get(i)!);
            weights.set(p, weights.get(p)! + weights.get(i)!);
        }
    }
    const choices: number[][] = [];
    for (const i of order.slice(1))
        for (const reverse of [0, 1]) {
            const nleft = reverse ? part.size - sizes.get(i)! : sizes.get(i)!, nright = part.size - nleft;
            if (nleft >= nl && nright >= nr) {
                const amount = reverse ? size - weights.get(i)! : weights.get(i)!;
                choices.push([Math.abs(amount / size - ratio), i, reverse]);
            }
        }
    return choices.sort(compare).slice(0, 6).map(([score, root, reverse]) => { const subtree = new Set<number>(), stack = [root]; while (stack.length) {
        const i = stack.pop()!;
        subtree.add(i);
        stack.push(...children.get(i)!);
    } const left = reverse ? new Set([...part].filter(i => !subtree.has(i))) : subtree, right = new Set([...part].filter(i => !left.has(i))); return { score, ids: sorted(left), left, right }; });
}
function compactGroups(graph: FaceGraph, part: Set<number>, centers: GridPoint[], areas: number[], number: number) {
    number = Math.min(number, part.size);
    const indices = sorted(part);
    if (number === indices.length)
        return indices.map(i => new Set([i]));
    const area = total(part, areas), center = [sum(indices.map(i => areas[i] * centers[i][0])) / area, sum(indices.map(i => areas[i] * centers[i][1])) / area] as GridPoint, first = indices.slice().sort((a, b) => dist(centers[a], center) - dist(centers[b], center) || a - b)[0];
    let seeds = [first];
    const distances = new Map(indices.map(i => [i, dist(centers[i], centers[first])]));
    while (seeds.length < number) {
        const selected = new Set(seeds), seed = indices.filter(i => !selected.has(i)).sort((a, b) => distances.get(b)! - distances.get(a)! || a - b)[0];
        seeds.push(seed);
        for (const i of indices)
            distances.set(i, Math.min(distances.get(i)!, dist(centers[i], centers[seed])));
    }
    let groups: Set<number>[] = [];
    for (let step = 0; step < 8; step++) {
        const owned = new Map<number, number>(), queue = new Heap();
        seeds.forEach((i, owner) => queue.push([0, owner, i]));
        while (queue.rows.length) {
            const [distance, owner, i] = queue.pop();
            if (owned.has(i))
                continue;
            owned.set(i, owner);
            for (const j of graph.get(i)!)
                if (part.has(j) && !owned.has(j))
                    queue.push([distance + Math.max(dist(centers[i], centers[j]), .001), owner, j]);
        }
        groups = seeds.map(() => new Set<number>());
        for (const [i, owner] of owned)
            groups[owner].add(i);
        const revised = groups.map(group => { const weight = total(group, areas), center = [sum([...group].map(i => areas[i] * centers[i][0])) / weight, sum([...group].map(i => areas[i] * centers[i][1])) / weight] as GridPoint; return sorted(group).sort((a, b) => dist(centers[a], center) - dist(centers[b], center) || a - b)[0]; });
        if (revised.every((seed, i) => seed === seeds[i]))
            break;
        seeds = revised;
    }
    return groups;
}
export function partitionScopeTasks(s: Scope, geoms: number[], counts: number[], graph: FaceGraph, targetStores: number, maxArea: number, regionParts?: Set<number>[]) {
    const areas = geoms.map(p => s.area(p)), centers = geoms.map(p => centroid(s, p)), [ox, oy] = centers[0], moments = geoms.map((p, i) => { const out = s.g.Module._malloc(8); let bounds: number[]; try {
        bounds = [s.g.GEOSGeom_getXMin, s.g.GEOSGeom_getYMin, s.g.GEOSGeom_getXMax, s.g.GEOSGeom_getYMax].map(fn => { if (fn(p, out) !== 1)
            throw Error('Invalid face bounds'); return s.g.Module.getValue(out, 'double'); });
    }
    finally {
        s.g.Module._free(out);
    } const width = bounds[2] - bounds[0], height = bounds[3] - bounds[1], x = centers[i][0] - ox, y = centers[i][1] - oy, a = areas[i]; return [a * x, a * y, a * (x * x + width ** 2 / 12), a * x * y, a * (y * y + height ** 2 / 12)]; }), cache = new Map<string, number>();
    function geometry(part: Set<number>) { const k = key(part); if (!cache.has(k))
        cache.set(k, union(s, sorted(part).map(i => geoms[i]))); return cache.get(k)!; }
    // Fixed cell-index aggregation prevents insertion-order rounding from changing near-tied cuts.
    function stats(part: Set<number>) { let weight = 0, sx = 0, sy = 0, sxx = 0, sxy = 0, syy = 0; for (const i of sorted(part)) {
        const [a, b, c, d, e] = moments[i];
        weight += areas[i];
        sx += a;
        sy += b;
        sxx += c;
        sxy += d;
        syy += e;
    } weight = Math.max(weight, 1e-12); const vx = Math.max(0, sxx / weight - (sx / weight) ** 2), vy = Math.max(0, syy / weight - (sy / weight) ** 2), cov = sxy / weight - sx * sy / weight ** 2, delta = Math.hypot(vx - vy, 2 * cov); return [Math.sqrt(Math.max(vx + vy + delta, 1e-9) / Math.max(vx + vy - delta, 1e-9)), .5 * Math.atan2(2 * cov, vx - vy)]; }
    function concavity(part: Set<number>, area: number) { const hull = s.area(s.own(s.g.GEOSConvexHull(s.read({ type: 'MultiPoint', coordinates: sorted(part).map(i => centers[i]) })))); return Math.max(0, 1 - area / Math.max(hull, area, 1)) ** 2; }
    function defect(p: number) { const quality = shapeMetrics(s, p); if (!Number.isFinite(quality.aspect))
        return Infinity; return Math.max(0, quality.aspect / (3 + 1e-5) - 1) + Math.max(0, (.65 - 1e-5) / Math.max(quality.solidity, 1e-9) - 1) + Number(narrowNeck(s, p)); }
    function cutScore(left: Set<number>, right: Set<number>, size: number, stores: number, ratio: number): Cut { const amount = total(left, areas), separation = Math.abs(amount / size - ratio), shops = Math.abs(total(left, counts) / stores - ratio), crossing = sum([...left].map(i => [...graph.get(i)!].filter(j => right.has(j)).length)), la = stats(left)[0], ra = stats(right)[0], elongation = .18 * Math.log(Math.max(la, ra)) + .04 * Math.log(la * ra), indent = concavity(left, amount) + concavity(right, size - amount); return { score: separation + elongation + 2.5 * indent + .025 * shops + .00015 * crossing / Math.sqrt(Math.max(left.size + right.size, 1)), ids: sorted(left), left: new Set(left), right }; }
    function split(part: Set<number>, number: number, minimum: number): Set<number>[] {
        const size = total(part, areas);
        number = Math.min(part.size, Math.max(1, number));
        if (part.size === 1)
            return [part];
        if (number === part.size)
            return sorted(part).map(i => new Set([i]));
        const refining = number === 1;
        let parentDefect = 0;
        if (refining) {
            if (size < 2 * minimum)
                return [part];
            parentDefect = defect(geometry(part));
            if (parentDefect === 0)
                return [part];
            number = 2;
        }
        const nl = Math.floor(number / 2), nr = number - nl, ratio = nl / number, stores = Math.max(total(part, counts), 1), candidates: Cut[] = [], seen = new Set<string>(), offsets = refining ? [-.25, -.17, -.1, -.06, -.03, 0, .03, .06, .1, .17, .25] : [-.06, -.03, 0, .03, .06], angles = [stats(part)[1], ...Array.from({ length: 8 }, (_, direction) => direction * Math.PI / 8)];
        for (const angle of angles) {
            const dx = Math.cos(angle), dy = Math.sin(angle), order = sorted(part).sort((a, b) => (centers[a][0] * dx + centers[a][1] * dy) - (centers[b][0] * dx + centers[b][1] * dy) || a - b), cumulative: number[] = [];
            let running = 0;
            for (const i of order) {
                running += areas[i];
                cumulative.push(running);
            }
            for (const offset of offsets) {
                const value = size * (ratio + offset);
                let low = 0, high = cumulative.length;
                while (low < high) {
                    const mid = (low + high) >> 1;
                    if (cumulative[mid] < value)
                        low = mid + 1;
                    else
                        high = mid;
                }
                const pos = Math.min(order.length - nr, Math.max(nl, low + 1)), left = new Set(order.slice(0, pos)), k = key(left);
                if (seen.has(k) || left.size < nl || part.size - left.size < nr)
                    continue;
                seen.add(k);
                const right = new Set([...part].filter(i => !left.has(i)));
                if (!connectedFaces(graph, left) || !connectedFaces(graph, right))
                    continue;
                const amount = total(left, areas);
                if (refining && Math.min(amount, size - amount) < minimum)
                    continue;
                candidates.push(cutScore(left, right, size, stores, ratio));
            }
        }
        if (refining)
            for (const [left, right] of geodesicCandidates(graph, part, centers)) {
                const k = key(left);
                if (seen.has(k) || Math.min(total(left, areas), total(right, areas)) < minimum)
                    continue;
                seen.add(k);
                candidates.push(cutScore(left, right, size, stores, ratio));
            }
        if (!candidates.length)
            candidates.push(...treeCandidates(graph, part, areas, centers, nl, nr, ratio));
        if (!candidates.length)
            return [part];
        let candidate: Cut;
        if (refining) {
            const improved: {
                defect: number;
                candidate: Cut;
            }[] = [];
            for (const row of candidates.sort(rank).slice(0, 8)) {
                if (Math.min(total(row.left, areas), total(row.right, areas)) < minimum)
                    continue;
                const ld = defect(geometry(row.left)), rd = defect(geometry(row.right)), worst = Math.max(ld, rd), weight = total(row.left, areas) / size, value = weight * ld + (1 - weight) * rd;
                if (Number.isFinite(worst) && worst <= parentDefect + 1e-5 && value < parentDefect * .9)
                    improved.push({ defect: value, candidate: row });
            }
            if (!improved.length)
                return [part];
            candidate = improved.sort((a, b) => a.defect - b.defect || rank(a.candidate, b.candidate))[0].candidate;
        }
        else
            candidate = candidates.sort(rank)[0];
        return [...split(candidate.left, nl, minimum), ...split(candidate.right, nr, minimum)];
    }
    if (regionParts === undefined) {
        regionParts = [];
        for (const component of components(graph)) {
            const size = total(component, areas), shops = total(component, counts), number = Math.max(1, Math.ceil(size / maxArea), Math.ceil(shops / targetStores)), useCenters = number > 1 && number <= 128, initial = useCenters ? compactGroups(graph, component, centers, areas, number) : [component];
            for (const candidate of initial) {
                const remaining = useCenters ? Math.max(1, Math.ceil(total(candidate, areas) / maxArea)) : number;
                regionParts.push(...split(candidate, remaining, 500000));
            }
        }
    }
    const regions: ScopePart[] = [], tasks: ScopeTask[] = [];
    for (const part of regionParts) {
        const regionIndex = regions.length;
        regions.push({ members: part, geom: geometry(part), count: total(part, counts) });
        for (const component of components(graph, part)) {
            const number = Math.max(1, rounded(total(component, areas) / 1e6, 0));
            for (const grid of split(component, number, 500000))
                tasks.push({ regionIndex, members: grid, geom: geometry(grid), count: total(grid, counts) });
        }
    }
    return { regions, tasks };
}
