/** Complete preview audit over scope road faces. No persistence; formal consolidation remains explicit. */
import { consolidateScopeParts } from './zone-rules';
import { createHash } from 'node:crypto';
import { arena, union, shapeMetrics, type Scope, type GridGeometry } from './grid-geometry';
import { project, validProjected, neighbours, parts, ZoneSnapshotError } from './zone-snapshot';
import { BalanceOptions, partitionBalancedFaces, sum, compare, type BalanceConfig, type FaceGraph } from './zone-balance';
import { partitionScopeTasks } from './zone-scope-partition';
export interface ScopeCell {
    id: string;
    geometry?: GridGeometry;
    metric_geometry?: GridGeometry;
    store_count: number;
    merchant_ids?: string[];
}
export interface ScopeSource {
    ward_code: string;
    ward_name: string;
    scope_ward_count?: number;
    snapshot_id: string;
    captured_at: string;
    source: unknown;
    store_count: number;
    area_km2: number;
    excluded_area_km2: number;
    road_count: number;
    boundary: GridGeometry;
    work_area: GridGeometry;
    metric_crs?: string;
    coverage_revision?: number;
    road_cells?: ScopeCell[];
    grids?: ScopeCell[];
    topology?: number[][];
    merchant_audit?: Record<string, number>;
    review_merchants?: unknown[];
}
export interface ScopePreviewOptions {
    balance?: BalanceConfig | null;
    includeMemberships?: boolean;
    generationRules?: boolean;
    riverFaces?: GridGeometry[];
    maxRiverWidthM?: number;
}
const sorted = (part: Iterable<number>) => [...part].sort((a, b) => a - b), get = (row: Record<string, number>, name: string, fallback: number) => Object.hasOwn(row, name) ? row[name] : fallback;
const min = (values: number[], fallback = 0) => values.length ? Math.min(...values) : fallback, max = (values: number[], fallback = 0) => values.length ? Math.max(...values) : fallback;
function identity(cells: ScopeCell[], members: Set<number>) { return [...members].map(i => cells[i].id).sort((a, b) => compare([...a].map(c => c.codePointAt(0)!), [...b].map(c => c.codePointAt(0)!))).join('|'); }
export async function previewScopeZones(doc: ScopeSource, targetStores: number, maxAreaKm2: number, options: ScopePreviewOptions = {}) {
    if (typeof targetStores !== 'number' || !Number.isFinite(targetStores) || targetStores < 1 || targetStores > 100000 || !Number.isFinite(maxAreaKm2) || maxAreaKm2 < 2 || maxAreaKm2 > 500)
        throw new ZoneSnapshotError('试算参数超出范围');
    if (doc.ward_code !== 'tokyo23' || (doc.scope_ward_count !== undefined && doc.scope_ward_count !== 23))
        throw new ZoneSnapshotError('东京23区整体快照不完整，不能按单区结果拼接代替');
    if (options.generationRules && options.balance === null)
        throw new ZoneSnapshotError('正式生成规则必须使用综合负载参数');
    const cells = doc.road_cells?.length ? doc.road_cells : doc.grids ?? [];
    if (!cells.length)
        throw new ZoneSnapshotError('缺少完整道路基础面，无法进行覆盖优先预览');
    if (cells.some(c => c.metric_geometry !== undefined) && doc.metric_crs !== 'EPSG:6677')
        throw new ZoneSnapshotError('道路基础面的投影声明不正确，不能预览');
    const balance = options.balance === null ? null : new BalanceOptions({ targetAreaKm2: maxAreaKm2, targetStores, areaWeight: .25, ...options.balance });
    return arena(s => {
        const geoms = cells.map(c => c.metric_geometry !== undefined ? s.own(s.g.GEOSGeom_setPrecision(s.read(c.metric_geometry), .001, 0)) : validProjected(s, c.geometry!)), counts = cells.map(c => c.store_count);
        if (counts.some(n => !Number.isFinite(n) || n < 0) || geoms.some(p => s.area(p) <= 0 || s.g.GEOSisValid(p) !== 1))
            throw new ZoneSnapshotError('道路基础面或商户计数无效');
        const graph: FaceGraph = doc.topology !== undefined && doc.topology.length === cells.length ? new Map(doc.topology.map((adjacent, i) => [i, new Set(adjacent)])) : neighbours(s, geoms);
        if ([...graph].some(([i, edges]) => [...edges].some(j => !Number.isInteger(j) || j < 0 || j >= cells.length || j === i || !graph.get(j)?.has(i))))
            throw new ZoneSnapshotError('道路面拓扑无效');
        let regionParts = balance ? partitionBalancedFaces(s, geoms, counts, graph, balance) : undefined;
        let excluded = new Set<number>(), policy: ReturnType<typeof consolidateScopeParts>['policy'] | undefined;
        if (options.generationRules && balance) {
            const rivers = options.riverFaces?.length ? union(s, options.riverFaces.map(g => s.own(s.g.GEOSGeom_setPrecision(s.read(g), .001, 0)))) : undefined;
            const consolidated = consolidateScopeParts(s, regionParts!, geoms, counts, graph, balance, s.area(validProjected(s, doc.work_area)) / 1e6, rivers, options.maxRiverWidthM ?? 100);
            regionParts = consolidated.groups;
            excluded = consolidated.excluded;
            policy = consolidated.policy;
            if ([...excluded].some(i => cells[i].merchant_ids?.length))
                throw new ZoneSnapshotError('无商户排除范围仍有商户绑定，拒绝生成');
        }
        const { regions, tasks } = partitionScopeTasks(s, geoms, counts, graph, targetStores, maxAreaKm2 * 1e6, regionParts), owners = new Map<number, number>();
        regions.forEach((g, k) => g.members.forEach(i => owners.set(i, k)));
        const groups = regions.map((g, k) => { const workload = balance ? balance.score(s.area(g.geom), g.count) : null, hasNeighbour = [...g.members].some(i => [...graph.get(i)!].some(j => owners.has(j) && owners.get(j) !== k)); return { ...g, workload, loadValid: !balance || (workload! >= balance.loadMin && workload! <= balance.loadMax), residual: balance ? workload! < balance.loadMin && !hasNeighbour : s.area(g.geom) < 500000 - 1, shapeValid: shapeMetrics(s, g.geom).acceptable }; });
        const cellOwners = new Map<number, string>(), ids: string[] = [], features = groups.map((g, k) => {
            const id = 'tokyo23-Z-' + createHash('sha256').update(identity(cells, g.members)).digest('hex').slice(0, 10);
            ids.push(id);
            for (const i of g.members)
                cellOwners.set(i, id);
            return { type: 'Feature', id, geometry: project(s.json(g.geom), true), properties: { id, level: 'zone', ward_code: 'tokyo23', unit_kind: g.residual ? (balance ? 'residual_land' : 'independent_land') : 'region_candidate', needs_review: g.residual || !g.loadValid || !g.shapeValid, workload: g.workload, load_valid: g.loadValid, shape_valid: g.shapeValid, review_reason: g.residual ? 'no_shared_edge_neighbour' : !g.loadValid ? 'workload_outside_band' : !g.shapeValid ? 'shape_conflict' : '', grid_count: tasks.filter(t => t.regionIndex === k).length, store_count: g.count, area_km2: s.area(g.geom) / 1e6 } };
        });
        let gridConflicts = 0;
        const gridFeatures = tasks.map(task => {
            const id = 'tokyo23-G-' + createHash('sha256').update(identity(cells, task.members)).digest('hex').slice(0, 10), area = s.area(task.geom), valid = area >= 500000 - 1 && area <= 2000000 + 1;
            if (!valid)
                gridConflicts++;
            return { type: 'Feature', id, geometry: project(s.json(task.geom), true), properties: { id, level: 'grid', zone_id: ids[task.regionIndex], store_count: task.count, area_km2: area / 1e6, area_valid: valid, ...options.includeMemberships ? { cell_ids: sorted(task.members).map(i => cells[i].id) } : {} } };
        });
        const originalWork = validProjected(s, doc.work_area), work = excluded.size ? s.own(s.g.GEOSDifference(originalWork, union(s, sorted(excluded).map(i => geoms[i])))) : originalWork, active = cells.map((_, i) => i).filter(i => !excluded.has(i)), combined = union(s, groups.map(g => g.geom)), uncovered = s.own(s.g.GEOSDifference(work, combined)), outside = s.own(s.g.GEOSDifference(combined, work)), overlap = Math.max(0, sum(groups.map(g => s.area(g.geom))) - s.area(combined), sum(active.map(i => s.area(geoms[i]))) - s.area(combined)), source = doc.merchant_audit ?? {}, boundIds = cells.flatMap(c => c.merchant_ids ?? []), uniqueIds = new Set(boundIds), sourceReady = [1, 2].includes(doc.coverage_revision!) && Object.keys(source).length > 0, expected = get(source, 'inside_work', doc.store_count), mapped = sourceReady ? uniqueIds.size : sum(counts), duplicates = sourceReady ? boundIds.length - uniqueIds.size : 0, reviews = doc.review_merchants ?? [], excludedUnreviewed = get(source, 'excluded_unreviewed', reviews.length), assignedOnce = cellOwners.size === active.length && active.every(i => cellOwners.has(i)) && sum(groups.map(g => g.members.size)) === active.length, taskMembers = tasks.flatMap(t => [...t.members]), taskSet = new Set(taskMembers), tasksOnce = taskMembers.length === active.length && taskSet.size === active.length && active.every(i => taskSet.has(i)), mismatch = sourceReady ? sum(cells.map(c => Math.abs(c.store_count - (c.merchant_ids?.length ?? 0)))) : 0;
        const audit = { tolerance_m2: 1, work_area_km2: s.area(work) / 1e6, uncovered_m2: s.area(uncovered), overlap_m2: overlap, outside_m2: s.area(outside), coverage_percent: Math.max(0, 100 * (1 - s.area(uncovered) / Math.max(s.area(work), 1))), source_merchant_count: get(source, 'inside_boundary', doc.store_count), expected_merchants: expected, assigned_merchants: mapped, unassigned_merchants: Math.max(0, expected - mapped), duplicate_merchants: duplicates, excluded_unreviewed: excludedUnreviewed, unlocated_source_merchants: get(source, 'unlocated_in_work', 0), source_duplicates: get(source, 'source_duplicates', 0), duplicate_cell_ids: cells.length - new Set(cells.map(c => c.id)).size, duplicate_region_ids: features.length - new Set(features.map(f => f.id)).size, duplicate_grid_ids: gridFeatures.length - new Set(gridFeatures.map(f => f.id)).size, all_cells_assigned_once: assignedOnce, all_task_cells_assigned_once: tasksOnce, binding_count_mismatch: mismatch, invalid_source_coordinates: get(source, 'invalid_coordinates', 0), source_audit_ready: sourceReady, ...policy ? { generation_rules: policy, excluded_empty_cell_ids: sorted(excluded).map(i => cells[i].id) } : {}, geometric_pass: false, merchant_pass: false };
        audit.geometric_pass = audit.uncovered_m2 <= 1 && audit.overlap_m2 <= 1 && audit.outside_m2 <= 1 && assignedOnce && tasksOnce && audit.duplicate_cell_ids === 0 && audit.duplicate_region_ids === 0 && audit.duplicate_grid_ids === 0;
        audit.merchant_pass = sourceReady && mapped === expected && duplicates === 0 && audit.unlocated_source_merchants === 0 && audit.source_duplicates === 0 && audit.invalid_source_coordinates === 0 && mismatch === 0;
        const issues: {
            code: string;
            count?: number;
            ratio?: number;
        }[] = [];
        if (!audit.geometric_pass)
            issues.push({ code: 'coverage_failed' });
        if (!audit.merchant_pass)
            issues.push({ code: 'merchant_coverage_failed' });
        if (!sourceReady)
            issues.push({ code: 'source_audit_missing' });
        if (excludedUnreviewed)
            issues.push({ code: 'excluded_merchant_review', count: excludedUnreviewed });
        if (gridConflicts)
            issues.push({ code: 'grid_area_conflict', count: gridConflicts });
        const sizes = groups.map(g => s.area(g.geom) / 1e6), ratio = max(sizes) / Math.max(min(sizes), 1e-12);
        if (!balance && ratio > 2 + 1e-5)
            issues.push({ code: 'zone_ratio', ratio });
        if (!balance && max(sizes) > maxAreaKm2 + 1e-6)
            issues.push({ code: 'zone_area' });
        const normal = groups.filter(g => !g.residual), workloadConflicts = normal.filter(g => !g.loadValid).length, residualCount = groups.filter(g => g.residual).length, shapeCount = (balance ? normal : groups).filter(g => !g.shapeValid).length;
        if (balance && workloadConflicts)
            issues.push({ code: 'workload_conflict', count: workloadConflicts });
        if (balance && residualCount)
            issues.push({ code: 'residual_land_review', count: residualCount });
        if (shapeCount)
            issues.push({ code: 'zone_shape', count: shapeCount });
        return { type: 'FeatureCollection', features, grids: gridFeatures, boundary: doc.boundary, work_area: project(s.json(work), true), meta: { ward_code: doc.ward_code, ward_name: doc.ward_name, snapshot_id: doc.snapshot_id, captured_at: doc.captured_at, source: doc.source, store_count: doc.store_count, area_km2: doc.area_km2, excluded_area_km2: doc.excluded_area_km2, road_count: doc.road_count }, audit, review_merchants: reviews, uncovered_features: parts(s, uncovered).filter(p => s.area(p) > 1).map(p => ({ type: 'Feature', geometry: project(s.json(p), true), properties: {} })), pending_grids: [], summary: { zone_count: groups.length, region_candidate_count: normal.length, independent_unit_count: residualCount, grid_count: tasks.length, area_ratio: ratio, min_area_km2: min(sizes), max_area_km2: max(sizes), target_stores: targetStores, max_area_km2_limit: balance ? null : maxAreaKm2, target_area_km2: maxAreaKm2, balance: balance ? { target_area: balance.targetAreaKm2, target_stores: balance.targetStores, area_weight: balance.areaWeight, load_min: balance.loadMin, load_max: balance.loadMax } : null, workload_conflicts: workloadConflicts, shape_conflicts: shapeCount, residual_area_km2: sum(groups.filter(g => g.residual).map(g => s.area(g.geom))) / 1e6, residual_merchants: sum(groups.filter(g => g.residual).map(g => g.count)), merchant_count_min: min(normal.map(g => g.count)), merchant_count_max: max(normal.map(g => g.count)), workload_min: min(normal.filter(g => g.workload !== null).map(g => g.workload!)), workload_max: max(normal.filter(g => g.workload !== null).map(g => g.workload!)), pending_grid_count: 0, pending_store_count: 0, pending_area_km2: 0, grid_area_conflicts: gridConflicts, eligible: issues.length === 0, confirmation_ready: issues.length === 0 }, issues, preview_only: true };
    });
}
