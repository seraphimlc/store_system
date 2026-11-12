/** Build a complete audited road-face source from prepared geometry and a private CSV. */
import { readFile, stat, mkdir } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';
import { prepareScopeSource, type ScopeSourceInput } from '../packages/domain/src/zone-source';
import { merchantSourceCsv } from '../apps/server/src/merchant-source-csv';
import { publishRailFile } from '../apps/server/src/rail-source';
const options: Record<string, string | boolean> = {}, args = process.argv.slice(2);
for (let i = 0; i < args.length; i++) {
    if (args[i] === '--write')
        options.write = true;
    else if (['--input', '--merchants', '--reference', '--output'].includes(args[i]) && args[i + 1] && !args[i + 1].startsWith('--'))
        options[args[i].slice(2)] = args[++i];
    else
        throw Error('Use --input <prepared-geometry.json> --merchants <private.csv> [--reference <old-source.json>] [--write --output <new-source.json>]');
}
if (typeof options.input !== 'string' || typeof options.merchants !== 'string')
    throw Error('Explicit geometry and merchant CSV required');
if (!!options.write !== (typeof options.output === 'string'))
    throw Error('--write and --output must be supplied together');
const geometry = resolve(options.input), csv = resolve(options.merchants), reference = typeof options.reference === 'string' ? resolve(options.reference) : null, output = typeof options.output === 'string' ? resolve(options.output) : null;
if (output && [geometry, csv, reference].includes(output))
    throw Error('Output must differ from every input');
async function json(path: string) { const info = await stat(path); if (!info.isFile() || info.size > 64 * 1024 * 1024)
    throw Error('Geometry/reference must be a regular JSON file up to 64 MiB'); return JSON.parse(await readFile(path, 'utf8')); }
const info = await stat(csv);
if (!info.isFile() || info.size > 1024 * 1024 * 1024)
    throw Error('Merchant source must be a regular CSV file up to 1 GiB');
const raw = await json(geometry);if(raw.code&&raw.code!=='tokyo23')throw Error('Scope source requires a complete tokyo23 geometry');const input: ScopeSourceInput = { ...raw, capturedAt: raw.capturedAt ?? raw.captured_at };
if (!input.boundary || !Array.isArray(input.roads) || !Array.isArray(input.excluded) || !input.capturedAt)
    throw Error('Input requires boundary, roads, excluded and capturedAt');
if (reference)
    input.referenceSnapshot = await json(reference);
const result = await prepareScopeSource(input, merchantSourceCsv(csv));
if (output) {
    await mkdir(dirname(output), { recursive: true });
    await publishRailFile(output, JSON.stringify(result) + '\n');
}
console.log(JSON.stringify({ mode: output ? 'write-new-file' : 'dry-run', databaseTouched: false, network: false, python: false, generationVersion: result.generation_version, snapshotId: result.snapshot_id, referenceSnapshotId: result.id_reference_snapshot, faces: result.road_cells.length, areaKm2: result.area_km2, audit: result.merchant_audit, reviewMerchants: result.review_merchants.length, output }));
