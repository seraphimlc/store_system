/** Offline ward/scope preview over a frozen snapshot. No database, provider or Python. */
import { readFile, stat, mkdir } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';
import { previewWardZones } from '../packages/domain/src/zone-preview';
import { previewScopeZones } from '../packages/domain/src/zone-scope';
import { riverEvidence } from '../apps/server/src/river-evidence';
import { publishRailFile } from '../apps/server/src/rail-source';
const args = process.argv.slice(2), options: Record<string, string | boolean> = {};
for (let i = 0; i < args.length; i++) {
    if (['--write', '--include-memberships', '--legacy-area', '--generation-rules'].includes(args[i]))
        options[args[i].slice(2)] = true;
    else if (['--input', '--stores', '--max-area-km2', '--output', '--area-weight', '--load-min', '--load-max', '--river-directory', '--max-river-width-m'].includes(args[i]) && args[i + 1] && !args[i + 1].startsWith('--'))
        options[args[i].slice(2)] = args[++i];
    else
        throw Error('Use --input <snapshot.json> --stores <target> --max-area-km2 <area> [--write --output <new-file>] [--include-memberships] [--generation-rules --river-directory <dir> --max-river-width-m <m>] [--legacy-area | --area-weight <0.05..0.95> --load-min <n> --load-max <n>]');
}
if (typeof options.input !== 'string' || typeof options.stores !== 'string' || typeof options['max-area-km2'] !== 'string')
    throw Error('Explicit snapshot, store target and area cap are required');
if (!!options.write !== (typeof options.output === 'string'))
    throw Error('--write and --output must be supplied together');
const input = resolve(options.input), output = typeof options.output === 'string' ? resolve(options.output) : null;
if (input === output)
    throw Error('Output must differ from input snapshot');
const info = await stat(input);
if (!info.isFile() || info.size > 64 * 1024 * 1024)
    throw Error('Snapshot must be a regular JSON file of at most 64 MiB');
const snapshot = JSON.parse(await readFile(input, 'utf8'));
const scope = snapshot.ward_code === 'tokyo23';
if (!scope && ['legacy-area', 'include-memberships', 'area-weight', 'load-min', 'load-max', 'generation-rules', 'river-directory', 'max-river-width-m'].some(key => options[key] !== undefined))
    throw Error('Scope options require a tokyo23 source');
if (options['legacy-area'] && ['area-weight', 'load-min', 'load-max'].some(key => options[key] !== undefined))
    throw Error('Load options cannot be combined with --legacy-area');
if (options['legacy-area'] && options['generation-rules'])
    throw Error('Generation rules require nonlinear balance');
if (!options['generation-rules'] && ['river-directory', 'max-river-width-m'].some(key => options[key] !== undefined))
    throw Error('River evidence options require --generation-rules');
if (options['river-directory'])
    process.env.NEXT_ZONE_DATA_DIRECTORY = resolve(String(options['river-directory']));
const evidence = options['generation-rules'] ? await riverEvidence() : undefined;
const balance = options['legacy-area'] ? null : { ...(options['generation-rules'] ? { areaWeight: .15 } : {}), ...options['area-weight'] !== undefined ? { areaWeight: Number(options['area-weight']) } : {}, ...options['load-min'] !== undefined ? { loadMin: Number(options['load-min']) } : {}, ...options['load-max'] !== undefined ? { loadMax: Number(options['load-max']) } : {} };
const result = scope ? await previewScopeZones(snapshot, Number(options.stores), Number(options['max-area-km2']), { balance, includeMemberships: !!options['include-memberships'] || !!options['generation-rules'], generationRules: !!options['generation-rules'], riverFaces: evidence?.faces, maxRiverWidthM: options['max-river-width-m'] === undefined ? 100 : Number(options['max-river-width-m']) }) : await previewWardZones(snapshot, Number(options.stores), Number(options['max-area-km2']));
if (output) {
    await mkdir(dirname(output), { recursive: true });
    await publishRailFile(output, JSON.stringify(result) + '\n');
}
console.log(JSON.stringify({ mode: output ? 'write-new-file' : 'dry-run', previewOnly: result.preview_only, databaseTouched: false, network: false, python: false, ward: snapshot.ward_code, ...evidence ? { riverEvidence: { fingerprint: evidence.fingerprint, files: evidence.files.length, faces: evidence.faces.length } } : {}, summary: result.summary, issues: result.issues, output }));
