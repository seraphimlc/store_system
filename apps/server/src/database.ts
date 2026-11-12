import { Inject, Injectable, OnModuleDestroy } from '@nestjs/common';
import { Pool, PoolClient, QueryResultRow, types } from 'pg';
// Legacy timestamp-without-time-zone columns store UTC, regardless of the host TZ.
export function utcTimestamp(value:string){return new Date(value.replace(' ','T')+'Z');}
types.setTypeParser(types.builtins.TIMESTAMP,utcTimestamp);
import { CONFIG, AppConfig } from './config';

@Injectable()
export class Database implements OnModuleDestroy {
  readonly pool: Pool;
  constructor(@Inject(CONFIG) config: AppConfig) {
    this.pool = new Pool({ connectionString: config.databaseUrl, max: 8, idleTimeoutMillis: 30000,
      connectionTimeoutMillis: 3000, statement_timeout: 15000, application_name: 'store-next-api' });
    this.pool.on('error', () => console.error(JSON.stringify({ event: 'database_idle_connection_error' })));
  }
  query<T extends QueryResultRow>(sql: string, values: unknown[] = []) { return this.pool.query<T>(sql, values); }
  async transaction<T>(fn: (client: PoolClient) => Promise<T>): Promise<T> {
    const client = await this.pool.connect();
    try { await client.query('BEGIN'); const value = await fn(client); await client.query('COMMIT'); return value; }
    catch (error) { await client.query('ROLLBACK'); throw error; }
    finally { client.release(); }
  }
  async onModuleDestroy() { await this.pool.end(); }
}
