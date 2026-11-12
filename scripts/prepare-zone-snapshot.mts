/** Prepare a new offline zone snapshot. Never downloads data, calls Python or connects to a database. */
import { readFile, stat, mkdir } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';
import { prepareZoneSnapshot, type ZoneSnapshotInput } from '../packages/domain/src/zone-snapshot';
import { publishRailFile } from '../apps/server/src/rail-source';
const options: Record<string, string | boolean> = {}, args = process.argv.slice(2);
for (let i = 0; i < args.length; i++) {
    if (args[i] === '--write')
        options.write = true;
    else if (['--input', '--reference', '--output'].includes(args[i]) && args[i + 1] && !args[i + 1].startsWith('--'))
        options[args[i].slice(2)] = args[++i];
    else
        throw Error('Use --input <geometry.json> [--reference <legacy-snapshot.json>] [--write --output <new-file>]');
}
if (typeof options.input !== 'string')
    throw Error('Explicit --input file required');
if (!!options.write !== (typeof options.output === 'string'))
    throw Error('--write and --output must be supplied together');
async function json(file: string) { const info = await stat(file); if (!info.isFile() || info.size > 64 * 1024 * 1024)
    throw Error('Input must be a regular JSON file of at most 64 MiB'); return JSON.parse(await readFile(file, 'utf8')); }
const inputPath = resolve(options.input), referencePath = typeof options.reference === 'string' ? resolve(options.reference) : null, output = typeof options.output === 'string' ? resolve(options.output) : null;
if (output && (output === inputPath || output === referencePath))
    throw Error('Output must differ from both input and reference');
const raw = await json(inputPath), input: ZoneSnapshotInput = { ...raw, capturedAt: raw.capturedAt ?? raw.captured_at };
if (!input || typeof input.code !== 'string' || !input.boundary || !Array.isArray(input.roads) || !Array.isArray(input.excluded) || !Array.isArray(input.merchants) || typeof input.capturedAt !== 'string' || !Number.isFinite(Date.parse(input.capturedAt)))
    throw Error('Input requires code, boundary, roads, excluded, merchants and capturedAt');
if (referencePath)
    input.referenceSnapshot = await json(referencePath);
const snapshot = await prepareZoneSnapshot(input);
if (output) {
    await mkdir(dirname(output), { recursive: true });
    await publishRailFile(output, JSON.stringify(snapshot) + '\n');
}
console.log(JSON.stringify({ mode: output ? 'write-new-file' : 'dry-run', databaseTouched: false, network: false, python: false, ward: snapshot.ward_code, generationVersion: snapshot.generation_version, snapshotId: snapshot.snapshot_id, referenceSnapshotId: snapshot.id_reference_snapshot, grids: snapshot.grids.length, stores: snapshot.store_count, areaKm2: snapshot.area_km2, issues: snapshot.issues, output }));
