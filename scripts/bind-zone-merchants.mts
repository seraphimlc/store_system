/** Explicit local PostgreSQL binding. No CSV fallback; all database access is read-only. */
import '../apps/server/node_modules/reflect-metadata/Reflect.js';
import { readFile, stat, mkdir } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';
import { Database } from '../apps/server/src/database';
import { bindDatabaseMerchants } from '../apps/server/src/zone-merchant-source';
import { MERCHANT_SOURCE_TABLE } from '../packages/domain/src/zone-merchant-binding';
import { publishRailFile } from '../apps/server/src/rail-source';
const options: Record<string, string | boolean> = {}, args = process.argv.slice(2);
for (let i = 0; i < args.length; i++) {
    if (args[i] === '--write')
        options.write = true;
    else if (['--input', '--output', '--table'].includes(args[i]) && args[i + 1] && !args[i + 1].startsWith('--'))
        options[args[i].slice(2)] = args[++i];
    else
        throw Error('Use --input <natural-source.json> [--table schema.table] [--write --output <new-bound-source.json>]');
}
if (typeof options.input !== 'string')
    throw Error('Explicit metric source required');
if (!!options.write !== (typeof options.output === 'string'))
    throw Error('--write and --output must be supplied together');
const input = resolve(options.input), output = typeof options.output === 'string' ? resolve(options.output) : null;
if (input === output)
    throw Error('Output must differ from input');
const info = await stat(input);
if (!info.isFile() || info.size > 128 * 1024 * 1024)
    throw Error('Input must be a regular JSON file up to 128 MiB');
const doc = JSON.parse(await readFile(input, 'utf8'));
const url = new URL(process.env.NEXT_DATABASE_URL ?? '');
if (!['postgres:', 'postgresql:'].includes(url.protocol) || !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname) || !['store_settle'].includes(url.pathname.slice(1)))
    throw Error('Binding requires the explicit local source/refactor PostgreSQL database');
url.searchParams.set('options', '-c default_transaction_read_only=on');
const db = new Database({ databaseUrl: url.href, sessionSecret: 'offline-merchant-readonly-not-a-session-secret', cookieSecure: false, port: 3000, host: '127.0.0.1' });
try {
    const result = await bindDatabaseMerchants(db, doc, String(options.table ?? MERCHANT_SOURCE_TABLE));
    if (output) {
        await mkdir(dirname(output), { recursive: true });
        await publishRailFile(output, JSON.stringify(result) + '\n');
    }
    console.log(JSON.stringify({ mode: output ? 'write-new-file' : 'dry-run', readOnly: true, network: false, python: false, databaseWritten: false, sourceDatabase: result.source.database, totalSourceRows: result.source.total_count, faces: result.road_cells.length, audit: result.merchant_audit, reviewMerchants: result.review_merchants.length, snapshotId: result.snapshot_id, output }));
}
finally {
    await db.onModuleDestroy();
}
