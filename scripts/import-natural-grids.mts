/** File validation first; explicit local apply only. Natural size normalization remains a prerequisite for replacement/append. */
import '../apps/server/node_modules/reflect-metadata/Reflect.js';
import { readFile, stat } from 'node:fs/promises';
import { resolve } from 'node:path';
import { Database } from '../apps/server/src/database';
import { validateNaturalCandidate, importNaturalCandidate, type NaturalCandidate } from '../apps/server/src/natural-grid-import';
const options: Record<string, string | boolean> = {}, args = process.argv.slice(2);
for (let i = 0; i < args.length; i++) {
    if (['--apply', '--replace', '--append-region'].includes(args[i]))
        options[args[i].slice(2)] = true;
    else if (['--input', '--admin-user'].includes(args[i]) && args[i + 1] && !args[i + 1].startsWith('--'))
        options[args[i].slice(2)] = args[++i];
    else
        throw Error('Use --input <natural-candidate.json> [--apply --admin-user <username> [--replace | --append-region]]');
}
if (typeof options.input !== 'string')
    throw Error('Explicit natural candidate required');
if (options.replace && options['append-region'])
    throw Error('Replace and append-region are mutually exclusive');
if ((options.replace || options['append-region']) && !options.apply)
    throw Error('Replace/append requires --apply');
if (options.apply && typeof options['admin-user'] !== 'string')
    throw Error('--apply requires an existing admin account');
const input = resolve(options.input), info = await stat(input);
if (!info.isFile() || info.size > 128 * 1024 * 1024)
    throw Error('Candidate must be a regular JSON file up to 128 MiB');
const result: NaturalCandidate = JSON.parse(await readFile(input, 'utf8'));
const ids = await validateNaturalCandidate(result);
if (!options.apply)
    console.log(JSON.stringify({ applied: false, databaseTouched: false, region: result.source_document.region_code, grids: result.features.length, merchants: ids.length, qualityPass: !!(result.audit as any).quality_pass }));
else {
    const url = new URL(process.env.NEXT_DATABASE_URL ?? '');
    if (!['postgres:', 'postgresql:'].includes(url.protocol) || !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname) || !url.pathname.endsWith('_refactor_p1'))
        throw Error('Natural import currently requires an explicit local refactor PostgreSQL target');
    const db = new Database({ databaseUrl: url.href, sessionSecret: 'natural-import-cli-not-a-browser-session-secret', cookieSecure: false, port: 3000, host: '127.0.0.1' });
    try {
        const imported = await db.transaction(async (client) => { const { rows: [actor] } = await client.query('SELECT id FROM users WHERE username=$1', [options['admin-user']]); if (!actor)
            throw Error('Admin account not found'); return importNaturalCandidate(client, actor.id, result, { replace: !!options.replace, appendRegion: !!options['append-region'] }); });
        console.log(JSON.stringify({ applied: true, ...imported }));
    }
    finally {
        await db.onModuleDestroy();
    }
}
