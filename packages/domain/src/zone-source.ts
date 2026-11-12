/** Frozen scope source preparation. Streaming merchant rows; no database/network or raw_records. */
import { createHash } from 'node:crypto';
import { arena, union, gridPointToMetric, normalizedWkb, type GridGeometry, type JsonGeometry } from './grid-geometry';
import { coastLand, project, validProjected, polygonize, centroid, index, ZoneSnapshotError } from './zone-snapshot';
import { faceGraph, compare } from './zone-balance';
import type { ScopeCell } from './zone-scope';
export interface ScopeSourceInput {
    boundary: GridGeometry;
    roads: JsonGeometry[];
    excluded: GridGeometry[];
    coastlines?: JsonGeometry[];
    capturedAt: string;
    source: unknown;
    referenceSnapshot?: {
        snapshot_id: string;
        road_cells: ScopeCell[];
        ward_code: string;
        metric_crs: string;
    };
}
export type MerchantSourceRow = Record<string, string | undefined>;
function numeric(value: string | undefined) { if (value === undefined || !value.trim())
    return NaN; const text = value.trim().replace(/(?<=\d)_(?=\d)/g, ''); if (!/^[+-]?(?:(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|inf(?:inity)?|nan)$/i.test(text))
    return NaN; return Number(text); }
const codepointSort = (a: string, b: string) => compare([...a].map(c => c.codePointAt(0)!), [...b].map(c => c.codePointAt(0)!));
export async function prepareScopeSource(input: ScopeSourceInput, rows: Iterable<MerchantSourceRow> | AsyncIterable<MerchantSourceRow>) {
    return arena(async (s) => {
        const borderGeo = s.read(input.boundary), border = validProjected(s, input.boundary), masks = input.excluded.map(g => validProjected(s, g));
        let land = coastLand(s, border, input.coastlines ?? []);
        if (masks.length)
            land = s.own(s.g.GEOSDifference(land, union(s, masks, 0)));
        land = s.own(s.g.GEOSGeom_setPrecision(s.own(s.g.GEOSMakeValid(land)), 0, 0));
        const segments = [s.own(s.g.GEOSBoundary(land))];
        for (const road of input.roads) {
            const line = s.read(project(road));
            if (s.g.GEOSisEmpty(line) === 0 && s.g.GEOSIntersects(line, land) === 1)
                segments.push(line);
        }
        const faces = polygonize(s, union(s, segments)).filter(p => s.area(p) > 0 && s.g.GEOSCovers(land, s.own(s.g.GEOSPointOnSurface(p))) === 1);
        faces.sort((a, b) => { const x = centroid(s, a), y = centroid(s, b); return x[0] - y[0] || x[1] - y[1] || s.area(a) - s.area(b); });
        if (!faces.length)
            throw new ZoneSnapshotError('没有完整道路基础面，停止构建预览');
        const geometry = faces.map(p => s.json(p) as GridGeometry), topology = faceGraph(geometry), tree = index(s, faces), merchantIds = faces.map(() => [] as string[]), reviews: {
            store_id: string;
            name: string;
            lng: number;
            lat: number;
            reason: string;
            status: string;
        }[] = [], seen = new Set<string>(), audit = { inside_boundary: 0, inside_work: 0, excluded_unreviewed: 0, unlocated_in_work: 0, source_duplicates: 0, invalid_coordinates: 0, mapped_to_faces: 0 };
        const out = s.g.Module._malloc(8);
        let bbox: number[];
        try {
            bbox = [s.g.GEOSGeom_getXMin, s.g.GEOSGeom_getYMin, s.g.GEOSGeom_getXMax, s.g.GEOSGeom_getYMax].map(fn => { if (fn(borderGeo, out) !== 1)
                throw new ZoneSnapshotError('边界无效'); return s.g.Module.getValue(out, 'double'); });
        }
        finally {
            s.g.Module._free(out);
        }
        try {
            for await (const row of rows) {
                const lng = numeric(row.lon || row.lng), lat = numeric(row.lat);
                if (!Number.isFinite(lng) || !Number.isFinite(lat)) {
                    audit.invalid_coordinates++;
                    continue;
                }
                if (lng < bbox[0] || lng > bbox[2] || lat < bbox[1] || lat > bbox[3])
                    continue;
                if (!row.place_id) {
                    audit.invalid_coordinates++;
                    continue;
                }
                const geographic = s.read({ type: 'Point', coordinates: [lng, lat] });
                let inside: boolean;
                try {
                    inside = s.g.GEOSIntersects(borderGeo, geographic) === 1;
                }
                finally {
                    s.release(geographic);
                }
                if (!inside)
                    continue;
                if (seen.has(row.place_id)) {
                    audit.source_duplicates++;
                    continue;
                }
                seen.add(row.place_id);
                audit.inside_boundary++;
                const point = s.read({ type: 'Point', coordinates: gridPointToMetric([lng, lat]) });
                try {
                    const merchant = { store_id: row.place_id, name: row.name ?? '', lng, lat };
                    if (s.g.GEOSIntersects(land, point) !== 1) {
                        reviews.push({ ...merchant, reason: 'excluded_terrain', status: 'unreviewed' });
                        audit.excluded_unreviewed++;
                        continue;
                    }
                    audit.inside_work++;
                    let owner: number | undefined;
                    for (const i of tree.query(point))
                        if (s.g.GEOSIntersects(faces[i], point) === 1 && (owner === undefined || i < owner))
                            owner = i;
                    if (owner === undefined) {
                        reviews.push({ ...merchant, reason: 'no_road_face', status: 'unreviewed' });
                        audit.unlocated_in_work++;
                    }
                    else
                        merchantIds[owner].push(row.place_id);
                }
                finally {
                    s.release(point);
                }
            }
        }
        finally {
            tree.close();
        }
        const references = new Map<string, string>();
        if (input.referenceSnapshot) {
            const reference = input.referenceSnapshot;
            if (reference.ward_code !== 'tokyo23' || reference.metric_crs !== 'EPSG:6677')
                throw new ZoneSnapshotError('参考整体来源投影或范围不一致');
            const names = new Set<string>();
            for (const cell of reference.road_cells) {
                if (!cell.metric_geometry || !/^tokyo23-R-[a-f0-9]{12}$/.test(cell.id) || names.has(cell.id))
                    throw new ZoneSnapshotError('参考道路面ID无效或重复');
                names.add(cell.id);
                const p = s.own(s.g.GEOSGeom_setPrecision(s.read(cell.metric_geometry), .001, 0)), key = normalizedWkb(s, p);
                if (references.has(key))
                    throw new ZoneSnapshotError('参考道路面边界重复');
                references.set(key, cell.id);
            }
        }
        const digest = createHash('sha256'), cells = faces.map((p, i) => { const canonical = normalizedWkb(s, p), id = references.get(canonical) ?? 'tokyo23-R-' + createHash('sha256').update(Buffer.from(canonical, 'hex')).digest('hex').slice(0, 12), ids = merchantIds[i].sort(codepointSort); digest.update(id.slice('tokyo23-R-'.length) + ids.join('|')); return { id, metric_geometry: geometry[i], area_km2: s.area(p) / 1e6, store_count: ids.length, merchant_ids: ids }; });
        audit.mapped_to_faces = merchantIds.reduce((n, ids) => n + ids.length, 0);
        return { schema_version: 1, generation_version: 6, coverage_revision: 2, metric_crs: 'EPSG:6677', scope_ward_count: 23, ward_code: 'tokyo23', ward_name: '东京23区整体', snapshot_id: digest.digest('hex').slice(0, 12), id_reference_snapshot: input.referenceSnapshot?.snapshot_id ?? null, captured_at: input.capturedAt, source: input.source, boundary: project(s.json(border), true) as GridGeometry, work_area: project(s.json(land), true) as GridGeometry, area_km2: s.area(land) / 1e6, excluded_area_km2: (s.area(border) - s.area(land)) / 1e6, store_count: audit.inside_work, road_count: input.roads.length, road_cells: cells, topology: cells.map((_, i) => [...topology.get(i)!].sort((a, b) => a - b)), merchant_audit: audit, review_merchants: reviews, grids: [], issues: [], preview_only: true };
    });
}
