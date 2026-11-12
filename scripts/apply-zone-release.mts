/** Explicit local release application; default validates files without opening a database. */
import '../apps/server/node_modules/reflect-metadata/Reflect.js';
import { readFile, stat } from 'node:fs/promises';
import { resolve } from 'node:path';
import { Database } from '../apps/server/src/database';
import { formalZoneRelease, validateZoneMembership, validateZoneCandidateHeader, zoneReleaseFingerprint, type ZoneCandidate } from '../apps/server/src/zone-release';
import type { ScopeSource } from '../packages/domain/src/zone-scope';
const options: Record<string, string | boolean> = {}, args = process.argv.slice(2);
for (let i = 0; i < args.length; i++) {
    if (['--apply', '--replace-unused'].includes(args[i]))
        options[args[i].slice(2)] = true;
    else if (['--source', '--candidate', '--admin-user'].includes(args[i]) && args[i + 1] && !args[i + 1].startsWith('--'))
        options[args[i].slice(2)] = args[++i];
    else
        throw Error('Use --source <source.json> --candidate <generated.json> [--apply --admin-user <username> [--replace-unused]]');
}
if (typeof options.source !== 'string' || typeof options.candidate !== 'string')
    throw Error('Explicit source and candidate files are required');
if (options['replace-unused'] && !options.apply)
    throw Error('--replace-unused requires --apply');
if (options.apply && typeof options['admin-user'] !== 'string')
    throw Error('--apply requires an existing --admin-user');
async function json(path: string) { const file = resolve(path), info = await stat(file); if (!info.isFile() || info.size > 64 * 1024 * 1024)
    throw Error('Input must be a regular JSON file at most 64 MiB'); return JSON.parse(await readFile(file, 'utf8')); }
const doc: ScopeSource = await json(options.source), candidate: ZoneCandidate = await json(options.candidate);
await validateZoneCandidateHeader(candidate, doc);
await validateZoneMembership(candidate, doc);
const fingerprint = zoneReleaseFingerprint(candidate, doc);
if (!options.apply)
    console.log(JSON.stringify({ applied: false, databaseTouched: false, fingerprint, zones: candidate.features.length, grids: candidate.grids.length, merchants: candidate.audit.assigned_merchants, confirmationReady: candidate.summary.confirmation_ready }));
else {
    const url = new URL(process.env.NEXT_DATABASE_URL ?? '');
    if (!['postgres:', 'postgresql:'].includes(url.protocol) || !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname) || !url.pathname.endsWith('_refactor_p1'))
        throw Error('Release application is currently limited to an explicit local refactor database');
    const db = new Database({ databaseUrl: url.href, sessionSecret: 'offline-release-cli-not-a-browser-session-secret', cookieSecure: false, port: 3000, host: '127.0.0.1' });
    try {
        const result = await db.transaction(async (client) => { const { rows: [actor] } = await client.query('SELECT id FROM users WHERE username=$1', [options['admin-user']]); if (!actor)
            throw Error('Admin account not found'); return formalZoneRelease(client, actor.id, candidate, doc, !!options['replace-unused']); });
        console.log(JSON.stringify({ applied: true, fingerprint, ...result }));
    }
    finally {
        await db.onModuleDestroy();
    }
}
