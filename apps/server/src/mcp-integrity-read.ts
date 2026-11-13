import { Injectable } from '@nestjs/common';
import type { Database } from './database';
import type { McpActor } from './mcp';
import { McpReadError } from './mcp-read';
export const mcpIntegrityReadTools = [{ name: 'visit_verify', title: '数据自洽检查', description: '仅管理员：执行 A1–A9 九项找平/发放/核销/来源/递延/调整列互证。仅报告，不修复。A6 为计划变更提示 info，其余差异为 fail；每项最多5条样本。', inputSchema: { type: 'object' as const, properties: {}, additionalProperties: false }, annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false } }];
type Row = Record<string, any>;
@Injectable()
export class McpIntegrityReadService {
  async run(client: Pick<Database, 'query'>, actor: McpActor, tool: string, args: Record<string, unknown>): Promise<Row> {
    if (actor.role !== 'admin') throw new McpReadError('FORBIDDEN_TOOL', '该工具仅管理员可用');
    if (tool !== 'visit_verify' || Object.keys(args).length) throw new McpReadError('BAD_PARAM', '自洽检查无需参数');
    const periods = (await client.query('SELECT id,month,person_code,diff_amount,adjust_amount,prev_adjust_amount,half1_amount,half2_amount FROM payroll_period_rows ORDER BY id')).rows;
    const adjusts = (await client.query('SELECT id,source_month,person_code,adjust_amount,settled_amount,remaining,status FROM payroll_adjusts ORDER BY id')).rows;
    const payments = (await client.query('SELECT id,month,person_code,seq,amount,adjust_applied,adjust_source_row_id,adjust_source_month,adjust_source_task_id FROM payroll_payments ORDER BY id')).rows;
    const links = (await client.query('SELECT adjust_id,payment_id,amount FROM payroll_settlement_links')).rows;
    const tasks = new Set((await client.query('SELECT id FROM recon_tasks')).rows.map(row => row.id));
    const months = (await client.query("SELECT DISTINCT to_char(japan_date,'YYYY-MM') AS month FROM formal_records WHERE japan_date IS NOT NULL ORDER BY month")).rows.map(row => row.month);
    const byPersonMonth = new Map(periods.map(row => [`${row.month}:${row.person_code}`, row])), periodIds = new Set(periods.map(row => row.id));
    const byPayment = new Map<number, number>(), byAdjust = new Map<number, number>();
    for (const row of links) { byPayment.set(row.payment_id, (byPayment.get(row.payment_id) || 0) + row.amount); byAdjust.set(row.adjust_id, (byAdjust.get(row.adjust_id) || 0) + row.amount); }
    const checks: Row[] = [];
    const add = (id: string, name: string, bad: string[], status = 'fail') => checks.push({ id, name, status: bad.length ? status : 'pass', count: bad.length, samples: bad.slice(0, 5) });
    const a1: string[] = [], a2: string[] = [], a3: string[] = [], a4: string[] = [], a5: string[] = [], a6: string[] = [], a7: string[] = [], a8: string[] = [], a9: string[] = [];
    for (const a of adjusts) {
      const period = byPersonMonth.get(`${a.source_month}:${a.person_code}`);
      if (!period || period.diff_amount !== a.adjust_amount) a1.push(`${a.source_month} ${a.person_code}: 找平表 ${a.adjust_amount} vs 找平行 ${period ? period.diff_amount : 'None'}`);
      if (a.settled_amount + a.remaining !== a.adjust_amount) a2.push(`#${a.id} ${a.source_month} ${a.person_code}: ${a.settled_amount}+${a.remaining} != ${a.adjust_amount}`);
      if ((a.remaining === 0) !== (a.status === 'settled')) a3.push(`#${a.id} status=${a.status} remaining=${a.remaining}`);
      const amount = byAdjust.get(a.id) || 0;
      if (amount !== a.settled_amount) a5.push(`找平#${a.id}: 关联合计 ${amount} vs 已找平 ${a.settled_amount}`);
    }
    for (const p of payments) {
      const amount = byPayment.get(p.id) || 0;
      if (amount !== p.adjust_applied) a4.push(`台账#${p.id} ${p.month} seq${p.seq}: 关联合计 ${amount} vs adjust_applied ${p.adjust_applied}`);
      const period = byPersonMonth.get(`${p.month}:${p.person_code}`);
      if (!period) a6.push(`台账#${p.id} 无对应找平行 ${p.month} ${p.person_code}`);
      else { const expected = p.seq === 1 ? period.half1_amount : period.half2_amount; if (p.amount !== expected) a6.push(`台账#${p.id} ${p.month} seq${p.seq}: 实发 ${p.amount} vs 当前应发 ${expected}`); }
      if (p.adjust_source_row_id && !periodIds.has(p.adjust_source_row_id)) a7.push(`台账#${p.id} source_row_id=${p.adjust_source_row_id} 不存在`);
      if (p.adjust_source_month && !byPersonMonth.has(`${p.adjust_source_month}:${p.person_code}`)) a7.push(`台账#${p.id} source_month=${p.adjust_source_month} 无该人找平行`);
      if (p.adjust_source_task_id && !tasks.has(p.adjust_source_task_id)) a7.push(`台账#${p.id} source_task_id=${p.adjust_source_task_id} 不存在`);
    }
    for (const row of periods) {
      if (row.diff_amount && row.prev_adjust_amount && (row.diff_amount > 0) !== (row.prev_adjust_amount > 0)) a8.push(`${row.month} ${row.person_code}: diff ${row.diff_amount} 与结转 ${row.prev_adjust_amount} 符号不一致`);
      if (row.diff_amount !== row.adjust_amount) a9.push(`${row.month} ${row.person_code}: diff ${row.diff_amount} vs 调整列 ${row.adjust_amount}`);
    }
    add('A1', '找平表金额 == 该月差异金额', a1); add('A2', '已找平 + 剩余 == 原始金额', a2); add('A3', '状态与剩余一致（0=settled）', a3);
    add('A4', '关联（按发放）合计 == 台账抵扣额', a4); add('A5', '关联（按找平）合计 == 已找平额', a5); add('A6', '台账快照 vs 当前应发（计划变更提示）', a6, 'info');
    add('A7', '抵扣来源可反查', a7); add('A8', '递延链符号一致', a8); add('A9', '应发表调整列 == 差异列（防双源）', a9);
    const failed = checks.filter(check => check.status === 'fail').length;
    return { ok: failed === 0, checks, summary: { passed: checks.filter(check => check.status === 'pass').length, failed, info: checks.filter(check => check.status === 'info').length,
      months_with_data: months, scale: { findiff_rows: periods.length, adjust_rows: adjusts.length, payment_rows: payments.length, link_rows: links.length } } };
  }
}
