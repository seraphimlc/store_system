/** PostgreSQL cursor source. The same read-only repeatable-read transaction covers count and binding. */
import type { PoolClient } from 'pg';
import { Database } from './database';
import { bindMerchantRows, MERCHANT_SOURCE_TABLE, type MerchantBindingDocument, type MerchantDatabaseRow } from '../../../packages/domain/src/zone-merchant-binding';
import { arena } from '../../../packages/domain/src/grid-geometry';
import { ZoneSnapshotError } from '../../../packages/domain/src/zone-snapshot';
export function merchantTableName(value: string) { if (!/^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?$/.test(value) || value.split('.').at(-1)!.toLowerCase() === 'raw_records')
    throw new ZoneSnapshotError('商户表名必须为合法的表名或schema.table'); return value.split('.').map(part => '"' + part + '"').join('.'); }
export async function* merchantDatabaseRows(client: PoolClient, bounds: number[], table = MERCHANT_SOURCE_TABLE) {
    const name = merchantTableName(table);
    await client.query(`DECLARE zone_merchants_cursor NO SCROLL CURSOR FOR SELECT place_id,name,lat,lon FROM ${name} WHERE (lon BETWEEN $1 AND $3 AND lat BETWEEN $2 AND $4) OR lat IS NULL OR lon IS NULL OR place_id IS NULL OR TRIM(place_id)='' OR NOT(lat BETWEEN -90 AND 90) OR NOT(lon BETWEEN -180 AND 180)`, bounds);
    try {
        for (;;) {
            const { rows } = await client.query<MerchantDatabaseRow>('FETCH FORWARD 5000 FROM zone_merchants_cursor');
            if (!rows.length)
                break;
            for (const row of rows)
                yield row;
        }
    }
    finally {
        await client.query('CLOSE zone_merchants_cursor').catch(() => { });
    }
}
export async function bindDatabaseMerchants<T extends MerchantBindingDocument>(db: Database, doc: T, table = MERCHANT_SOURCE_TABLE, readAt = new Date().toISOString()) {
    const name = merchantTableName(table), bounds = await arena(s => { const border = s.read(doc.boundary), out = s.g.Module._malloc(8); try {
        return [s.g.GEOSGeom_getXMin, s.g.GEOSGeom_getYMin, s.g.GEOSGeom_getXMax, s.g.GEOSGeom_getYMax].map(fn => { if (fn(border, out) !== 1)
            throw new ZoneSnapshotError('商户绑定边界无效'); return s.g.Module.getValue(out, 'double'); });
    }
    finally {
        s.g.Module._free(out);
    } });
    try {
        return await db.transaction(async (client) => { await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'); await client.query("SET LOCAL statement_timeout='60s'"); await client.query("SET LOCAL lock_timeout='2s'"); const { rows: [source] } = await client.query(`SELECT current_database() AS database,count(*)::text AS total_count FROM ${name}`); return bindMerchantRows(doc, merchantDatabaseRows(client, bounds, table), { backend: 'postgresql', database: source.database, table, total_count: Number(source.total_count), read_at: readAt }); });
    }
    catch (error) {
        if (error instanceof ZoneSnapshotError)
            throw error;
        throw new ZoneSnapshotError('数据库商户源读取失败，请检查数据库连接和指定商户表的只读权限。');
    }
}
