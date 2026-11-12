/** Raw offline OSM geometry extraction. Relation rings are polygonized in geographic coordinates. */
import { arena, union, type Scope, type GridGeometry, type GridPoint, type JsonGeometry, type OsmWaterElement } from './grid-geometry';
import { polygonize, ZoneSnapshotError } from './zone-snapshot';
export interface OsmZoneDocument {
    elements: OsmWaterElement[];
    remark?: string;
    osm3s?: {
        timestamp_osm_base?: string;
    };
}
export const TOKYO_WARDS = ['千代田区', '中央区', '港区', '新宿区', '文京区', '台東区', '墨田区', '江東区', '品川区', '目黒区', '大田区', '世田谷区', '渋谷区', '中野区', '杉並区', '豊島区', '北区', '荒川区', '板橋区', '練馬区', '足立区', '葛飾区', '江戸川区'];
const wardCodes = TOKYO_WARDS.map((_, i) => String(13101 + i));
export function osmWardRelations(elements: OsmWaterElement[]) { const result = new Map<string, OsmWaterElement>(); for (const element of elements) {
    const code = (element.tags?.ref ?? '').slice(0, 5);
    if (element.type === 'relation' && element.tags?.admin_level === '7' && wardCodes.includes(code))
        result.set(code, element);
} return result; }
function point(value: {
    lon: number;
    lat: number;
}): GridPoint { if (!Number.isFinite(value.lon) || !Number.isFinite(value.lat) || Math.abs(value.lon) > 180 || Math.abs(value.lat) > 90)
    throw new ZoneSnapshotError('OSM坐标无效'); return [value.lon, value.lat]; }
function relation(s: Scope, element: OsmWaterElement) {
    const outer: number[] = [], inner: number[] = [];
    for (const member of element.members ?? []) {
        if (member.type !== 'way' || (member.geometry?.length ?? 0) < 2)
            continue;
        (member.role === 'inner' ? inner : outer).push(s.read({ type: 'LineString', coordinates: member.geometry!.map(point) }));
    }
    const polygons = (lines: number[]) => lines.length ? union(s, polygonize(s, union(s, lines, 0)), 0) : s.read({ type: 'Polygon', coordinates: [] });
    return s.own(s.g.GEOSDifference(polygons(outer), polygons(inner)));
}
export async function osmRelationPolygon(element: OsmWaterElement) { return arena(s => s.json(relation(s, element)) as GridGeometry); }
function waterFaces(s: Scope, elements: OsmWaterElement[]) { const output: GridGeometry[] = []; for (const element of elements) {
    const tags = element.tags ?? {};
    if (tags.natural !== 'water' && tags.landuse !== 'reservoir' && tags.waterway !== 'riverbank')
        continue;
    let geometry: number;
    if (element.type === 'relation')
        geometry = relation(s, element);
    else {
        const coordinates = (element.geometry ?? []).map(point);
        if (coordinates.length < 4 || coordinates[0][0] !== coordinates.at(-1)![0] || coordinates[0][1] !== coordinates.at(-1)![1])
            continue;
        geometry = s.read({ type: 'Polygon', coordinates: [coordinates] });
    }
    if (s.g.GEOSisEmpty(geometry) === 0)
        output.push(s.json(geometry) as GridGeometry);
} return output; }
export async function osmExclusionFaces(elements: OsmWaterElement[]) { return arena(s => waterFaces(s, elements)); }
export interface OsmScopeGeometryInput {
    boundaries: OsmZoneDocument;
    coastlines: OsmZoneDocument;
    wards: Record<string, OsmZoneDocument>;
    capturedAt: string;
    source?: unknown;
}
function complete(doc: OsmZoneDocument | undefined) { if (!doc || !Array.isArray(doc.elements) || doc.remark)
    throw new ZoneSnapshotError('OSM来源文件无效或返回不完整结果'); }
export async function prepareOsmScopeGeometry(input: OsmScopeGeometryInput) {
    complete(input.boundaries);
    complete(input.coastlines);
    const relations = osmWardRelations(input.boundaries.elements);
    if (wardCodes.some(code => !relations.has(code)))
        throw new ZoneSnapshotError('23区行政边界不完整');
    return arena(s => {
        const borders: number[] = [], roads: JsonGeometry[] = [], elements = new Map<string, OsmWaterElement>(), timestamps = new Set<string>();
        const wards: {
            code: string;
            name: string;
            relation: number;
            roadCount: number;
        }[] = [];
        for (const [i, code] of wardCodes.entries()) {
            const element = relations.get(code)!;
            if (!Number.isSafeInteger(element.id) || element.id <= 0)
                throw new ZoneSnapshotError('OSM关系标识无效');
            const border = relation(s, element);
            if (s.g.GEOSisEmpty(border) !== 0 || s.g.GEOSisValid(border) !== 1)
                throw new ZoneSnapshotError(TOKYO_WARDS[i] + '行政边界无效');
            borders.push(border);
            const raw = input.wards[code];
            complete(raw);
            let count = 0;
            for (const item of raw.elements) {
                elements.set(item.type + ':' + item.id, item);
                if (item.type === 'way' && item.tags?.highway && (item.geometry?.length ?? 0) > 1) {
                    roads.push({ type: 'LineString', coordinates: item.geometry!.map(point) });
                    count++;
                }
            }
            if (!count)
                throw new ZoneSnapshotError(TOKYO_WARDS[i] + '道路缺失，停止，不生成伪网格');
            timestamps.add(raw.osm3s?.timestamp_osm_base ?? '');
            wards.push({ code, name: TOKYO_WARDS[i], relation: element.id, roadCount: count });
        }
        const coastlines = input.coastlines.elements.filter(item => item.type === 'way' && (item.geometry?.length ?? 0) > 1).map(item => ({ type: 'LineString' as const, coordinates: item.geometry!.map(point) }));
        return { code: 'tokyo23', scope_ward_count: 23, elements:[...elements.values()], boundary: s.json(union(s, borders, 0)) as GridGeometry, roads, excluded: waterFaces(s, [...elements.values()]), coastlines, capturedAt: input.capturedAt, source: input.source ?? { geometry: 'OpenStreetMap / Overpass (ODbL)', osm_timestamps: [...timestamps].sort() }, wards };
    });
}
