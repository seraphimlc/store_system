/** Read rows into existing metric road faces. Binding never changes geometry or exclusion policy. */
import { createHash } from 'node:crypto';
import { arena, gridPointToMetric, type GridGeometry } from './grid-geometry';
import { index, validProjected, ZoneSnapshotError } from './zone-snapshot';
import { compare } from './zone-balance';
export const MERCHANT_SOURCE_TABLE = 'public.kanto_merchants_v20261029';
export interface BindingCell {
    id: string;
    metric_geometry: GridGeometry;
    store_count: number;
    merchant_ids: string[];
    [key: string]: unknown;
}
export interface MerchantBindingDocument {
    road_cells: BindingCell[];
    metric_crs: string;
    boundary: GridGeometry;
    work_area: GridGeometry;
    captured_at: string;
    source: Record<string, unknown>;
    [key: string]: unknown;
}
export interface MerchantDatabaseRow {
    place_id: unknown;
    name?: unknown;
    lat: unknown;
    lon: unknown;
}
function number(value: unknown) { if (typeof value === 'number' || typeof value === 'boolean')
    return Number(value); if (typeof value !== 'string' || !value.trim())
    return NaN; const text = value.trim().replace(/(?<=\d)_(?=\d)/g, ''); return /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(text) ? Number(text) : NaN; }
export async function bindMerchantRows<T extends MerchantBindingDocument>(doc: T, rows: Iterable<MerchantDatabaseRow> | AsyncIterable<MerchantDatabaseRow>, sourceInfo: Record<string, unknown>) {
    if (!doc.road_cells?.length || doc.metric_crs !== 'EPSG:6677')
        throw new ZoneSnapshotError('缺少完整米制道路基础面，不能绑定数据库商户。');
    return arena(async (s) => {
        const cells = doc.road_cells, geoms = cells.map(cell => s.own(s.g.GEOSGeom_setPrecision(s.read(cell.metric_geometry), .001, 0))), tree = index(s, geoms), border = s.read(doc.boundary), work = validProjected(s, doc.work_area), bound = cells.map(() => [] as string[]), seen = new Set<string>(), reviews: {
            store_id: string;
            name: unknown;
            lat: number | null;
            lng: number | null;
            reason: string;
            status: string;
        }[] = [], audit = { inside_boundary: 0, inside_work: 0, excluded_unreviewed: 0, unlocated_in_work: 0, source_duplicates: 0, invalid_coordinates: 0, mapped_to_faces: 0 };
        const out = s.g.Module._malloc(8);
        let bbox: number[];
        try {
            bbox = [s.g.GEOSGeom_getXMin, s.g.GEOSGeom_getYMin, s.g.GEOSGeom_getXMax, s.g.GEOSGeom_getYMax].map(fn => { if (fn(border, out) !== 1)
                throw new ZoneSnapshotError('商户绑定边界无效'); return s.g.Module.getValue(out, 'double'); });
        }
        finally {
            s.g.Module._free(out);
        }
        type Merchant = {
            store_id: string;
            name: unknown;
            lat: number;
            lng: number;
        };
        let batch: Merchant[] = [];
        function consume() {
            const unique: Merchant[] = [];
            for (const merchant of batch) {
                const geographic = s.read({ type: 'Point', coordinates: [merchant.lng, merchant.lat] });
                let inside: boolean;
                try {
                    inside = s.g.GEOSIntersects(border, geographic) === 1;
                }
                finally {
                    s.release(geographic);
                }
                if (!inside)
                    continue;
                if (seen.has(merchant.store_id)) {
                    audit.source_duplicates++;
                    continue;
                }
                seen.add(merchant.store_id);
                unique.push(merchant);
            }
            audit.inside_boundary += unique.length;
            const locations: {
                merchant: Merchant;
                coordinates: [
                    number,
                    number
                ];
            }[] = [];
            for (const merchant of unique) {
                const coordinates = gridPointToMetric([merchant.lng, merchant.lat]), point = s.read({ type: 'Point', coordinates });
                try {
                    if (s.g.GEOSIntersects(work, point) === 1) {
                        audit.inside_work++;
                        locations.push({ merchant, coordinates });
                    }
                    else {
                        audit.excluded_unreviewed++;
                        reviews.push({ ...merchant, reason: 'excluded_terrain', status: 'unreviewed' });
                    }
                }
                finally {
                    s.release(point);
                }
            }
            for (const { merchant, coordinates } of locations) {
                const point = s.read({ type: 'Point', coordinates });
                try {
                    let owner: number | undefined;
                    for (const i of tree.query(point))
                        if (s.g.GEOSIntersects(geoms[i], point) === 1 && (owner === undefined || i < owner))
                            owner = i;
                    if (owner === undefined) {
                        audit.unlocated_in_work++;
                        reviews.push({ ...merchant, reason: 'no_road_face', status: 'unreviewed' });
                    }
                    else
                        bound[owner].push(merchant.store_id);
                }
                finally {
                    s.release(point);
                }
            }
            batch = [];
        }
        try {
            for await (const row of rows) {
                const lat = number(row.lat), lng = number(row.lon), identity = String(row.place_id || '').trim(), name = row.name || '';
                if (!identity || !Number.isFinite(lat) || !Number.isFinite(lng) || lat < -90 || lat > 90 || lng < -180 || lng > 180) {
                    audit.invalid_coordinates++;
                    reviews.push({ store_id: String(row.place_id || ''), name, lat: null, lng: null, reason: 'invalid_coordinates', status: 'unreviewed' });
                    continue;
                }
                if (lng < bbox[0] || lng > bbox[2] || lat < bbox[1] || lat > bbox[3])
                    continue;
                batch.push({ store_id: identity, name, lat, lng });
                if (batch.length >= 20000)
                    consume();
            }
            consume();
        }
        finally {
            tree.close();
        }
        const digest = createHash('sha256'), updated = cells.map((cell, i) => { const ids = bound[i].sort((a, b) => compare([...a].map(c => c.codePointAt(0)!), [...b].map(c => c.codePointAt(0)!))); digest.update(cell.id + '|' + ids.join('|')); return { ...cell, store_count: ids.length, merchant_ids: ids }; });
        audit.mapped_to_faces = bound.reduce((n, ids) => n + ids.length, 0);
        return { ...doc, road_cells: updated, store_count: audit.inside_work, merchant_audit: audit, review_merchants: reviews, source: { ...doc.source, ...sourceInfo, merchants: 'PostgreSQL / ' + (sourceInfo.table ?? MERCHANT_SOURCE_TABLE), limitations: '库内已知商户，非完整门店普查；拟排除地形内商户仍需复核。' }, snapshot_id: digest.digest('hex').slice(0, 12), geometry_captured_at: doc.captured_at, captured_at: sourceInfo.read_at ?? doc.captured_at, coverage_revision: 2 };
    });
}
