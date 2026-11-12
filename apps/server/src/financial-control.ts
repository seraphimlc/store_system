import type {PoolClient} from 'pg';import {BusinessError} from './errors';
/** Caller holds settlement-write or financial-write while validating and changing derived settlement data. */
export async function assertMonthsOpen(client:PoolClient,months:string[]){
 const values=[...new Set(months.filter(month=>/^\d{4}-(0[1-9]|1[0-2])$/.test(month)))];if(!values.length)return;
 const closed=(await client.query<{month:string}>('SELECT month FROM sealed_months WHERE month=ANY($1::text[]) ORDER BY month',[values])).rows;
 if(closed.length)throw new BusinessError('MONTH_SEALED','月份已封账，不能改写结算数据：'+closed.map(row=>row.month).join('、'),409);
}
export async function requireAdmin(client:PoolClient,id:number){if(!(await client.query("SELECT id FROM users WHERE id=$1 AND role='admin' AND status IN ('active','leave') AND is_active=true FOR SHARE",[id])).rowCount)throw new BusinessError('FORBIDDEN','管理员权限已变化，请重新登录',403);}
