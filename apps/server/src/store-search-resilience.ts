import type {Database} from './database';import {BusinessError} from './errors';
/** Telemetry is optional: bound database lock/execution time without changing normal search timeouts. */
export async function writeStoreSearchTelemetry(db:Database,sql:string,values:unknown[]){
 return db.transaction(async client=>{await client.query("SET LOCAL lock_timeout='100ms'; SET LOCAL statement_timeout='250ms'");return client.query(sql,values);});
}
export function searchFailureKind(error:unknown){const code=(error as {code?:unknown})?.code;return code==='55P03'?'lock_timeout':code==='57014'?'statement_timeout':'storage_error';}
export function suggestionFailure(error:unknown){
 const code=(error as {code?:unknown})?.code;
 if(code==='55P03'||code==='57014')return new BusinessError('SEARCH_SUGGESTIONS_BUSY','相似名称查询繁忙，请稍后重试；普通搜索仍可使用',503);
 return error;
}
