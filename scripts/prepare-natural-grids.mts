/** Offline natural preparation/candidate only. Merchant binding and normalization are separate. */
import { readFile, stat, mkdir } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';
import { prepareNaturalGrids, naturalGridCandidate, type NaturalGridInput } from '../packages/domain/src/natural-grids';
import { publishRailFile } from '../apps/server/src/rail-source';
const options: Record<string, string | boolean> = {}, args = process.argv.slice(2);
for (let i = 0; i < args.length; i++) {
    if (['--write', '--candidate'].includes(args[i]))
        options[args[i].slice(2)] = true;
    else if (['--input', '--reference', '--output', '--region'].includes(args[i]) && args[i + 1] && !args[i + 1].startsWith('--'))
        options[args[i].slice(2)] = args[++i];
    else
        throw Error('Use --input <natural-geometry.json> [--region tokyo23|tokyo26|kanagawa|saitama|chiba] [--reference <old-natural-source.json>] [--candidate] [--write --output <new-file>]');
}
if (typeof options.input !== 'string')
    throw Error('Explicit natural geometry input required');
if (!!options.write !== (typeof options.output === 'string'))
    throw Error('--write and --output must be supplied together');
async function json(file: string) { const info = await stat(file); if (!info.isFile() || info.size > 128 * 1024 * 1024)
    throw Error('Natural source must be a regular JSON file up to 128 MiB'); return JSON.parse(await readFile(file, 'utf8')); }
const inputPath = resolve(options.input), reference = typeof options.reference === 'string' ? resolve(options.reference) : null, output = typeof options.output === 'string' ? resolve(options.output) : null;
if (output && [inputPath, reference].includes(output))
    throw Error('Output must differ from inputs');
const raw = await json(inputPath), input: NaturalGridInput = { ...raw, capturedAt: raw.capturedAt ?? raw.captured_at, regionCode: options.region === undefined ? (raw.regionCode ?? raw.region_code ?? raw.code ?? 'tokyo23') : String(options.region) };
if (!input.boundary || !Array.isArray(input.elements) || !Array.isArray(input.excluded) || !Array.isArray(input.coastlines) || !input.capturedAt)
    throw Error('Input requires boundary, tagged elements, excluded, coastlines and capturedAt');
if (reference)
    input.referenceSnapshot = await json(reference);
const doc = await prepareNaturalGrids(input), payload = options.candidate ? await naturalGridCandidate(doc) : doc;
if (output) {
    await mkdir(dirname(output), { recursive: true });
    await publishRailFile(output, JSON.stringify(payload) + '\n');
}
console.log(JSON.stringify({ mode: output ? 'write-new-file' : 'dry-run', network: false, databaseTouched: false, python: false, applied: false, region: doc.region_code, faces: doc.road_cells.length, areaKm2: doc.area_km2, geometricPass: doc.natural_audit.geometric_pass, merchantBindingReady: false, snapshotId: doc.snapshot_id, generationVersion: doc.generation_version, output }));
