/** Natural road blocks, before merchant binding and size normalization. Never draws synthetic cuts. */
import { createHash } from 'node:crypto';
import { arena, union, normalizedWkb, shapeMetrics, type Scope, type GridGeometry, type GridPolygon, type JsonGeometry, type OsmWaterElement } from './grid-geometry';
import { parts, index, coastLand, project, validProjected, centroid, ZoneSnapshotError } from './zone-snapshot';
import { compare, sum } from './zone-balance';
const MAIN = new Set(['motorway', 'trunk', 'primary', 'secondary']), RAIL = new Set(['rail', 'light_rail', 'tram']), REGIONS = new Set(['tokyo23', 'tokyo26', 'kanagawa', 'saitama', 'chiba']);
export function naturalBoundaryKind(tags: Record<string, string>, lengthM?: number): 'road' | 'road_projection' | 'rail' | null {
    const raw = tags.layer === undefined ? '0' : tags.layer;
    let layer: number;
    if (typeof raw === 'number')
        layer = raw;
    else if (typeof raw === 'string' && /^[+-]?(?:(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|inf(?:inity)?|nan)$/i.test(raw.trim())) {
        layer = /^[+]?inf(?:inity)?$/i.test(raw.trim()) ? Infinity : /^-inf(?:inity)?$/i.test(raw.trim()) ? -Infinity : Number(raw);
    }
    else
        return null;
    const highway = tags.highway ?? '', suffix = highway.endsWith('_link') ? highway.slice(0, -5) : highway;
    let road = MAIN.has(highway) || MAIN.has(suffix);
    if (['tertiary', 'tertiary_link', 'unclassified'].includes(highway))
        road = !!tags.ref && !['private', 'no'].includes(tags.access);
    const active = (name: string) => !['no', 'false', '0', ''].includes(tags[name] ?? 'no');
    if (road) {
        const underground = active('tunnel') || layer < 0 || tags.location === 'underground';
        if (tags.location === 'underwater' || (underground && (lengthM === undefined || lengthM > 150)))
            return null;
        return layer !== 0 || active('bridge') || underground ? 'road_projection' : 'road';
    }
    if (layer !== 0 || active('bridge') || active('tunnel') || ['underground', 'underwater'].includes(tags.location))
        return null;
    return RAIL.has(tags.railway) ? 'rail' : null;
}
export function canonical(value: any): string { if (value === null)
    return 'null'; if (Array.isArray(value))
    return '[' + value.map(canonical).join(',') + ']'; if (typeof value === 'object')
    return '{' + Object.keys(value).sort().map(k => JSON.stringify(k) + ':' + canonical(value[k])).join(',') + '}'; if (typeof value === 'number' && !Number.isFinite(value))
    throw new ZoneSnapshotError('来源含非有限数值'); return JSON.stringify(value); }
export const sha = (value: any) => createHash('sha256').update(canonical(value)).digest('hex');
function outline(s: Scope, p: number) { const faces = parts(s, p).map(part => s.read({ type: 'Polygon', coordinates: [(s.json(part) as GridPolygon).coordinates[0]] })); return faces.length ? union(s, faces) : p; }
function bounds(s: Scope, p: number) { const out = s.g.Module._malloc(8); try {
    return [s.g.GEOSGeom_getXMin, s.g.GEOSGeom_getYMin, s.g.GEOSGeom_getXMax, s.g.GEOSGeom_getYMax].map(fn => { if (fn(p, out) !== 1)
        throw new ZoneSnapshotError('边界无效'); return s.g.Module.getValue(out, 'double'); });
}
finally {
    s.g.Module._free(out);
} }
function clipper(s: Scope, land: number) {
    const prepared = s.g.GEOSPrepare(land);
    if (!prepared)
        throw new ZoneSnapshotError('陆地准备失败');
    const [xmin, ymin, xmax, ymax] = bounds(s, land), pieces: number[] = [];
    for (let x = Math.floor(xmin / 10000); x <= Math.floor(xmax / 10000); x++)
        for (let y = Math.floor(ymin / 10000); y <= Math.floor(ymax / 10000); y++) {
            const clipped = s.own(s.g.GEOSClipByRect(land, x * 10000, y * 10000, (x + 1) * 10000, (y + 1) * 10000));
            if (s.g.GEOSisEmpty(clipped) === 0)
                pieces.push(...parts(s, s.own(s.g.GEOSGeom_setPrecision(s.own(s.g.GEOSMakeValid(clipped)), .001, 0))));
        }
    const tree = index(s, pieces);
    return { clip(line: number) { if (s.g.GEOSPreparedCovers(prepared, line) === 1)
            return line; const candidates = tree.query(line).filter(i => s.g.GEOSIntersects(pieces[i], line) === 1); return candidates.length ? union(s, candidates.map(i => s.own(s.g.GEOSIntersection(line, pieces[i])))) : s.read({ type: 'LineString', coordinates: [] }); }, close() { tree.close(); s.g.GEOSPreparedGeom_destroy(prepared); } };
}
export function matches(s: Scope, expected: number, actual: number) { const difference = s.own(s.g.GEOSSymDifference(expected, actual)), area = s.area(difference); if (area <= .01)
    return true; if (area > s.length(expected) * .002)
    return false; return s.area(s.own(s.g.GEOSDifference(difference, s.own(s.g.GEOSBuffer(s.own(s.g.GEOSBoundary(expected)), .002, 16))))) <= .01; }
export interface NaturalGridInput {
    boundary: GridGeometry;
    elements: OsmWaterElement[];
    excluded: GridGeometry[];
    coastlines: JsonGeometry[];
    capturedAt: string;
    source: Record<string, unknown>;
    regionCode?: string;
    dividers?: OsmWaterElement[];
    referenceSnapshot?: {
        region_code: string;
        road_cells: {
            id: string;
            metric_geometry: GridGeometry;
        }[];
        snapshot_id: string;
    };
}
export async function prepareNaturalGrids(input: NaturalGridInput) {
    const region = input.regionCode ?? 'tokyo23';
    if (!REGIONS.has(region))
        throw new ZoneSnapshotError('地区不存在');
    return arena(s => {
        const border = validProjected(s, input.boundary), masks = input.excluded.map(g => validProjected(s, g));
        let land = coastLand(s, border, input.coastlines);
        if (masks.length)
            land = s.own(s.g.GEOSDifference(land, union(s, masks)));
        land = s.own(s.g.GEOSGeom_setPrecision(outline(s, land), .001, 0));
        const clip = clipper(s, land), segments = [s.own(s.g.GEOSBoundary(land))], retained: {
            osm_id: number;
            kind: string;
            tags: Record<string, string>;
        }[] = [], skipped = { minor_or_other: 0, grade_separated_or_unknown: 0 }, unique = new Map(input.elements.map(e => [e.type + ':' + e.id, e])), ordered = [...unique.values()].sort((a, b) => a.type < b.type ? -1 : a.type > b.type ? 1 : a.id - b.id), extra: {
            osm_id: number;
            tags: Record<string, string>;
        }[] = [];
        const rawLine = (element: OsmWaterElement) => s.read(project({ type: 'LineString', coordinates: (element.geometry ?? []).map(p => [p.lon, p.lat]) }));
        try {
            for (const element of ordered) {
                if (element.type !== 'way' || (element.geometry?.length ?? 0) < 2)
                    continue;
                const tags = element.tags ?? {}, raw = rawLine(element), kind = naturalBoundaryKind(tags, s.length(raw));
                if (kind === null) {
                    skipped[MAIN.has(tags.highway) || RAIL.has(tags.railway) ? 'grade_separated_or_unknown' : 'minor_or_other']++;
                    continue;
                }
                const line = clip.clip(raw);
                if (s.g.GEOSisEmpty(line) === 1 || s.length(line) <= .001)
                    continue;
                segments.push(line);
                retained.push({ osm_id: element.id, kind, tags });
            }
            if (!retained.length)
                throw new ZoneSnapshotError('缺少有标签的地面主干道/铁路，不能生成自然网格');
            for (const element of input.dividers ?? []) {
                const tags = element.tags ?? {};
                if (tags.waterway !== 'river' && !['administrative', 'facility'].includes(tags.boundary))
                    throw new ZoneSnapshotError('补充分割线缺少真实来源标签');
                if ((element.geometry?.length ?? 0) < 2)
                    continue;
                const line = clip.clip(rawLine(element));
                if (s.g.GEOSisEmpty(line) === 0) {
                    segments.push(line);
                    extra.push({ osm_id: element.id, tags });
                }
            }
        }
        finally {
            clip.close();
        }
        const pointers = s.g.Module._malloc(12);
        let collection: number, cuts: number, dangles: number, invalid: number;
        try {
            collection = s.own(s.g.GEOSPolygonize_full(union(s, segments), pointers, pointers + 4, pointers + 8));
            cuts = s.own(s.g.Module.getValue(pointers, 'i32'));
            dangles = s.own(s.g.Module.getValue(pointers + 4, 'i32'));
            invalid = s.own(s.g.Module.getValue(pointers + 8, 'i32'));
        }
        finally {
            s.g.Module._free(pointers);
        }
        const faces = parts(s, collection).filter(face => s.g.GEOSCovers(land, s.own(s.g.GEOSPointOnSurface(face))) === 1).flatMap(face => parts(s, s.own(s.g.GEOSIntersection(s.own(s.g.GEOSGeom_setPrecision(face, .001, 0)), land))));
        faces.sort((a, b) => { const x = centroid(s, a), y = centroid(s, b); return x[0] - y[0] || x[1] - y[1] || s.area(a) - s.area(b); });
        const combined = union(s, faces), missing = s.own(s.g.GEOSDifference(land, combined)), outside = s.own(s.g.GEOSDifference(combined, land)), overlap = Math.max(0, sum(faces.map(p => s.area(p))) - s.area(combined));
        const references = new Map<string, string>();
        if (input.referenceSnapshot) {
            if (input.referenceSnapshot.region_code !== region)
                throw new ZoneSnapshotError('参考自然网格地区不一致');
            const ids = new Set<string>();
            for (const cell of input.referenceSnapshot.road_cells) {
                if (ids.has(cell.id) || !cell.id.startsWith(region + '-N-'))
                    throw new ZoneSnapshotError('参考自然网格ID无效或重复');
                ids.add(cell.id);
                const k = normalizedWkb(s, s.own(s.g.GEOSGeom_setPrecision(s.read(cell.metric_geometry), .001, 0)));
                if (references.has(k))
                    throw new ZoneSnapshotError('参考自然网格边界重复');
                references.set(k, cell.id);
            }
        }
        const cells = faces.map(face => { const key = normalizedWkb(s, face); return { id: references.get(key) ?? region + '-N-' + createHash('sha256').update(Buffer.from(key, 'hex')).digest('hex').slice(0, 16), metric_geometry: s.json(face) as GridGeometry, area_km2: s.area(face) / 1e6, store_count: 0, merchant_ids: [] as string[] }; });
        const audit = { crs: 'EPSG:6677', precision_m: .001, main_road_classes: [...MAIN].sort(), junction_links: true, tertiary_evidence: 'only a route ref may cut; unsigned tertiary and residential never cut', grade_policy: 'road projection on land; short underpass<=150m; ground rail only; no transit graph', retained_boundaries: retained, skipped, additional_boundaries: extra, cuts: s.json(cuts), dangles: s.json(dangles), invalid_rings: s.json(invalid), uncovered: s.json(missing), outside: s.json(outside), overlap_m2: overlap, uncovered_m2: s.area(missing), outside_m2: s.area(outside), precision_boundary_tolerance_m: .002, geometric_pass: faces.length > 0 && matches(s, land, combined) && overlap <= .01 && s.g.GEOSisEmpty(invalid) === 1, input_sha256: sha({ boundary: input.boundary, elements: ordered, exclusions: input.excluded, coastlines: input.coastlines, dividers: input.dividers ?? [] }) };
        if (!audit.geometric_pass) {
            const error = new ZoneSnapshotError(`自然网格校验失败：未覆盖${s.area(missing).toFixed(3)}m²，外溢${s.area(outside).toFixed(3)}m²，重叠${overlap.toFixed(3)}m²，非法环${s.g.GEOSGetNumGeometries(invalid)}个`);
            Object.assign(error, { audit });
            throw error;
        }
        return { schema_version: 1, coverage_revision: 2, generation_version: 'natural-ts-1', hash_format: 'canonical-json-js-v1', id_reference_snapshot: input.referenceSnapshot?.snapshot_id ?? null, cover_policy: 'road-block', metric_crs: 'EPSG:6677', snapshot_id: sha(cells).slice(0, 12), captured_at: input.capturedAt, source: { ...input.source, region_code: region }, region_code: region, scope_ward_count: region === 'tokyo23' ? 23 : null, boundary: input.boundary, work_area: project(s.json(land), true) as GridGeometry, road_cells: cells, store_count: 0, area_km2: s.area(land) / 1e6, grids: [], issues: [], natural_audit: audit, preview_only: true };
    });
}
export type NaturalGridSource = Awaited<ReturnType<typeof prepareNaturalGrids>> & {
    merchant_audit?: Record<string, number>;
};
export async function naturalGridCandidate(doc: NaturalGridSource) { return arena(s => { const features = doc.road_cells.map(cell => ({ type: 'Feature', id: cell.id, geometry: project(cell.metric_geometry, true), properties: { id: cell.id, level: 'grid', zone_id: null, area_km2: cell.area_km2, store_count: cell.store_count, cell_ids: [cell.id], shape_valid: shapeMetrics(s, outline(s, s.read(cell.metric_geometry))).acceptable } })), merchants = doc.merchant_audit ?? {}, audit = { ...doc.natural_audit, merchant_pass: merchants.unlocated_in_work === 0 && merchants.source_duplicates === 0 && merchants.mapped_to_faces === merchants.inside_work, merchant_audit: merchants, geojson_sha256: sha(features) }; return { type: 'FeatureCollection', features, audit, source_document: doc, status: 'candidate', applied: false }; }); }
