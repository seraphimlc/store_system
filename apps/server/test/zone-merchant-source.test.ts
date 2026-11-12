import 'reflect-metadata';
import test from 'node:test';
import assert from 'node:assert/strict';
import { Database } from '../src/database';
import { bindDatabaseMerchants, merchantTableName } from '../src/zone-merchant-source';
import { gridToGeographic, gridPointToGeographic, type GridPolygon } from '../../../packages/domain/src/grid-geometry';
function fixture() { const metric: GridPolygon = { type: 'Polygon', coordinates: [[[10000, -40000], [11000, -40000], [11000, -39000], [10000, -39000], [10000, -40000]]] }; return { metric_crs: 'EPSG:6677', captured_at: 'geometry-date', source: { geometry: 'synthetic' }, boundary: gridToGeographic(metric), work_area: gridToGeographic(metric), road_cells: [{ id: 'cell-one', metric_geometry: metric, store_count: 0, merchant_ids: [] }] }; }
test('merchant table validation forbids SQL fragments, quoted path tricks and raw_records', () => {
    assert.equal(merchantTableName('public.kanto_merchants_v20261029'), '"public"."kanto_merchants_v20261029"');
    for (const name of ['table;DROP TABLE users', 'public.raw_records', 'raw_records', 'other.RAW_RECORDS', 'a.b.c', '"public".x', ''])
        assert.throws(() => merchantTableName(name), /表名必须/);
});
const url = process.env.NEXT_TEST_DATABASE_URL;
test('PostgreSQL merchant binding streams inside one read-only repeatable-read transaction without touching source or ops', { skip: !url }, async () => {
    assert.equal(process.env.NEXT_TEST_ISOLATED, '1');
    assert.match(new URL(url!).pathname, /\/store_test_[0-9]{13}_[a-f0-9]{12}_refactor_p1$/);
    const db = new Database({ databaseUrl: url!, sessionSecret: 'merchant-read-only-test-secret-at-least40', cookieSecure: false, port: 0, host: '127.0.0.1' }), table = 'public.zone_binding_fixture', point = gridPointToGeographic([10500, -39500]), statements: string[] = [];
    let readonly = false, isolation = '';
    try {
        await db.query('CREATE TABLE public.zone_binding_fixture(place_id text,name text,lat double precision,lon double precision)');
        await db.query("INSERT INTO public.zone_binding_fixture SELECT 'store-'||n,'synthetic', $1,$2 FROM generate_series(1,6001) AS n", [point[1], point[0]]);
        await db.query("INSERT INTO public.zone_binding_fixture VALUES('invalid',NULL,NULL,NULL),('outside','synthetic',0,0)");
        const before = (await db.query('SELECT count(*)::int AS n FROM public.zone_binding_fixture')).rows[0].n, original = db.transaction.bind(db);
        const observed = { transaction: async (operation: any) => original(async (client) => {
                const query = client.query.bind(client);
                const proxy = new Proxy(client, { get(target, key) {
                        if (key !== 'query')
                            return Reflect.get(target, key);
                        return async (sql: string, values?: unknown[]) => {
                            statements.push(sql);
                            if (sql.startsWith('DECLARE')) {
                                readonly = (await query('SHOW transaction_read_only')).rows[0].transaction_read_only === 'on';
                                isolation = (await query('SHOW transaction_isolation')).rows[0].transaction_isolation;
                            }
                            return query(sql, values);
                        };
                    } });
                return operation(proxy);
            }) } as Database;
        const result = await bindDatabaseMerchants(observed, fixture(), table, 'fixed-read-date');
        assert.equal(readonly, true);
        assert.equal(isolation, 'repeatable read');
        assert.equal(result.source.total_count, 6003);
        assert.equal(result.store_count, 6001);
        assert.equal(result.merchant_audit.invalid_coordinates, 1);
        assert.equal(result.road_cells[0].merchant_ids.length, 6001);
        assert.equal(result.review_merchants[0].store_id, 'invalid');
        assert.equal(statements.filter(sql => sql.startsWith('FETCH')).length, 3);
        assert.ok(statements.every(sql => !/^\s*(?:INSERT|UPDATE|DELETE|ALTER|CREATE|DROP)/i.test(sql)));
        assert.equal((await db.query('SELECT count(*)::int AS n FROM public.zone_binding_fixture')).rows[0].n, before);
        await assert.rejects(bindDatabaseMerchants(db, fixture(), 'public.missing_binding_fixture'), error => (error as Error).message === '数据库商户源读取失败，请检查数据库连接和指定商户表的只读权限。');
        assert.equal((await bindDatabaseMerchants(db, fixture(), table)).store_count, 6001);
    }
    finally {
        await db.query('DROP TABLE IF EXISTS public.zone_binding_fixture');
        await db.onModuleDestroy();
    }
});
test('merchant binding CLI rejects remote connections and CSV fallback flags before opening a source', async () => {
    const { mkdtemp, writeFile, rm } = await import('node:fs/promises'), { join } = await import('node:path'), { tmpdir } = await import('node:os'), { spawnSync } = await import('node:child_process');
    const directory = await mkdtemp(join(tmpdir(), 'merchant-binding-cli-')), input = join(directory, 'source.json');
    try {
        await writeFile(input, JSON.stringify(fixture()));
        const run = (extra: string[]) => spawnSync(process.execPath, ['--import', 'tsx', 'scripts/bind-zone-merchants.mts', '--input', input, ...extra], { encoding: 'utf8', env: { ...process.env, NEXT_DATABASE_URL: 'postgresql://fixture:unused@remote/store_settle' } });
        const remote = run([]);
        assert.notEqual(remote.status, 0);
        assert.match(remote.stderr, /explicit local source/);
        const fallback = run(['--merchants', 'old.csv']);
        assert.notEqual(fallback.status, 0);
        assert.match(fallback.stderr, /Use --input/);
        assert.notEqual(run(['--write', '--output', input]).status, 0);
    }
    finally {
        await rm(directory, { recursive: true, force: true });
    }
});
