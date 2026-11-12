/** Translate existing local Overpass caches. No automatic network fallback or database. */
import { readFile, stat, mkdir } from 'node:fs/promises';
import { resolve, join, dirname } from 'node:path';
import { createHash } from 'node:crypto';
import { prepareOsmScopeGeometry, osmExclusionFaces, osmWardRelations, type OsmZoneDocument } from '../packages/domain/src/osm-zone-source';
import { publishRailFile } from '../apps/server/src/rail-source';
const options: Record<string, string | boolean> = {}, args = process.argv.slice(2);
for (let i = 0; i < args.length; i++) {
    if (['--write', '--natural-input'].includes(args[i]))
        options[args[i].slice(2)] = true;
    else if (['--directory', '--output', '--captured-at'].includes(args[i]) && args[i + 1] && !args[i + 1].startsWith('--'))
        options[args[i].slice(2)] = args[++i];
    else
        throw Error('Use --directory <local-osm-cache> [--captured-at <ISO-date>] [--natural-input] [--write --output <new-geometry.json>]');
}
if (typeof options.directory !== 'string')
    throw Error('Explicit local OSM directory required');
if (!!options.write !== (typeof options.output === 'string'))
    throw Error('--write and --output must be supplied together');
const root = resolve(options.directory), output = typeof options.output === 'string' ? resolve(options.output) : null, hash = createHash('sha256'), files: string[] = [];
async function load(name: string): Promise<OsmZoneDocument> {
    const file = join(root, name);
    if (output === file)
        throw Error('Output must differ from OSM inputs');
    const info = await stat(file);
    if (!info.isFile() || info.size > 64 * 1024 * 1024)
        throw Error('OSM cache must be a regular JSON file up to 64 MiB');
    const bytes = await readFile(file);
    hash.update(name).update('\0').update(bytes);
    files.push(name);
    const doc = JSON.parse(bytes.toString('utf8'));
    if (!Array.isArray(doc.elements) || doc.remark)
        throw Error('OSM cache is incomplete');
    return doc;
}
let railwayCollected = true;
const boundaries = await load('boundaries.source.json'), coastlines = await load('coastlines.source.json'), relations = osmWardRelations(boundaries.elements), wards: Record<string, OsmZoneDocument> = {};
if (relations.size !== 23)
    throw Error('23区行政边界不完整');
for (const [code, relation] of [...relations].sort(([a], [b]) => a.localeCompare(b))) {
    if (!Number.isSafeInteger(relation.id) || relation.id <= 0)
        throw Error('Invalid OSM relation ID');
    const stem = code + '-' + relation.id;
    let filename = stem + '.roads-v2.source.json';
    try {
        await stat(join(root, filename));
    }
    catch (error) {
        if ((error as NodeJS.ErrnoException).code !== 'ENOENT')
            throw error;
        filename = stem + '.roads-v1.source.json';
        railwayCollected = false;
    }
    wards[code] = await load(filename);
}
const capturedAt = String(options['captured-at'] ?? new Date().toISOString());
if (!Number.isFinite(Date.parse(capturedAt)))
    throw Error('Invalid capture date');
const result = await prepareOsmScopeGeometry({ boundaries, coastlines, wards, capturedAt, source: { geometry: 'OpenStreetMap / Overpass (ODbL)', cache_files: files } });
if (options['natural-input']) {
    try {
        const rail = await load('railways.source.json');
        const elementsById = new Map(result.elements.map(element => [element.type + ':' + element.id, element]));
        for (const element of rail.elements)
            elementsById.set(element.type + ':' + element.id, element);
        result.elements = [...elementsById.values()];
        result.excluded = await osmExclusionFaces(result.elements);
        railwayCollected = true;
    }
    catch (error) {
        if ((error as NodeJS.ErrnoException).code !== 'ENOENT')
            throw error;
    }
}
const fingerprint = hash.digest('hex');
Object.assign(result.source, { cache_fingerprint: fingerprint, ...options['natural-input'] ? { railway_collected: railwayCollected, limitations: railwayCollected ? '仅地面主干道、铁路与已标注陆地边界。' : '旧缓存未采集铁路；铁路验收待补。' } : {} });
const { elements, ...withoutElements } = result;
const payload = options['natural-input'] ? result : withoutElements;
if (output) {
    await mkdir(dirname(output), { recursive: true });
    await publishRailFile(output, JSON.stringify(payload) + '\n');
}
console.log(JSON.stringify({ mode: output ? 'write-new-file' : 'dry-run', network: false, databaseTouched: false, python: false, fingerprint, naturalInput: !!options['natural-input'], files: files.length, wards: result.wards.length, roads: result.roads.length, exclusions: result.excluded.length, coastlines: result.coastlines.length, output }));
