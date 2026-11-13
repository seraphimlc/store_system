import { Injectable } from '@nestjs/common';
import type { Database } from './database';
import type { McpActor } from './mcp';

export class McpReadError extends Error {
  constructor(readonly code: string, message: string, readonly hint = '', readonly details?: Record<string, unknown>) { super(message); }
}
const monthPattern = /^[0-9]{4}-(0[1-9]|1[0-2])$/;
const monthSchema = { type: ['string', 'null'], pattern: '^[0-9]{4}-(0[1-9]|1[0-2])$' };
const readAnnotations = { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false };
export const mcpReadTools = [
  { name: 'visit_overview', title: '公司总览', description: '仅管理员：summary 为某月正式表及已物化工资汇总；months 为已有月份；ranking 为点数排行。summary/ranking 需要 YYYY-MM，limit 默认 10、最多 100。只读，金额为日元。',
    inputSchema: { type: 'object' as const, properties: { month: monthSchema, view: { type: 'string', enum: ['summary', 'months', 'ranking'], default: 'summary' }, limit: { type: 'integer', minimum: 1, default: 10 } }, additionalProperties: false }, annotations: readAnnotations },
  { name: 'visit_person', title: '员工绩效查询', description: '仅管理员：按 person 工号精确或 name 姓名包含查询员工月绩效与逐日数据；月份省略取全系统最新数据月。工资只读物化值，未物化为 null。',
    inputSchema: { type: 'object' as const, properties: { person: { type: ['string', 'null'] }, name: { type: ['string', 'null'] }, month: monthSchema }, additionalProperties: false }, annotations: readAnnotations },
  { name: 'visit_my_perf', title: '我的绩效', description: '读取本人已物化的月绩效或逐日明细；month 为 YYYY-MM，省略取本人最新数据月。view=month/daily；未物化工资为 null，不自行计算。',
    inputSchema: { type: 'object' as const, properties: { month: monthSchema, view: { type: 'string', enum: ['month', 'daily'], default: 'month' } }, additionalProperties: false }, annotations: readAnnotations },
  { name: 'visit_my_pay', title: '我的找平与发放', description: '读取本人找平、已发工资和双向核销轨迹；month 可省略，指定 YYYY-MM 时按旧口径过滤。金额为日元；只读，不登记发放。',
    inputSchema: { type: 'object' as const, properties: { month: monthSchema }, additionalProperties: false }, annotations: readAnnotations },
];
type QueryClient = Pick<Database, 'query'>;
type Daily = { date: string; records: number; p1: number; p2: number; points: number };
type Perf = { records: number; p1: number; p2: number; points: number; salary: number; per_point: number; settle_points: number; settle_amount: number; diff_points: number; diff_amount: number; rate37: number; pass37: boolean };
type Adjustment = { id: number; source_month: string; person_code: string; adjust_amount: number; settled_amount: number; remaining: number; status: string; settled_at: Date | null; source_task_id: number | null; source_row_id: number | null };
type Payment = { id: number; month: string; seq: number; points: number; amount: number; bonus: number; adjust_applied: number; adjust_leftover: number | null; paid_at: Date | null };
type Link = { adjust_id: number; payment_id: number; month: string; amount: number };
type AdjustLink = { payment_id: number; month: string; seq: number | null; payment_amount: number | null; amount: number };
type PayLink = { adjust_id: number; source_month: string; adjust_amount: number | null; amount: number };
function iso(date: Date | null) { return date ? date.toISOString().slice(0, 19) : null; }
function monthArgument(value: unknown): string | null {
  if (value == null) return null;
  if (typeof value !== 'string' || !monthPattern.test(value.trim())) throw new McpReadError('BAD_MONTH', '月份格式非法，应为 YYYY-MM', '月份必须是 YYYY-MM，例如 2026-09');
  return value.trim();
}
function monthBounds(month: string) {
  const [year, number] = month.split('-').map(Number);
  if (year < 1 || year === 9999 && number === 12) throw new McpReadError('BAD_MONTH', '月份超出支持的日期范围');
  return [month + '-01', `${String(number === 12 ? year + 1 : year).padStart(4, '0')}-${String(number === 12 ? 1 : number + 1).padStart(2, '0')}-01`];
}

@Injectable()
export class McpReadService {
  async run(client: QueryClient, actor: McpActor, tool: string, input: Record<string, unknown>): Promise<Record<string, unknown>> {
    if (tool === 'visit_overview' || tool === 'visit_person') {
      if (actor.role !== 'admin') throw new McpReadError('FORBIDDEN_TOOL', '该工具仅管理员可用');
      const allowed = tool === 'visit_overview' ? ['month', 'view', 'limit'] : ['person', 'name', 'month'];
      if (Object.keys(input).some(key => !allowed.includes(key))) throw new McpReadError('BAD_PARAM', '包含不支持的参数');
      return tool === 'visit_overview' ? this.overview(client, input) : this.person(client, input);
    }
    if (!['visit_my_perf', 'visit_my_pay'].includes(tool)) throw new McpReadError('NOT_FOUND', '工具尚未恢复');
    const args = { ...input };
    // Check explicit foreign identities before validating month or querying any business table.
    for (const key of ['person', 'person_code', 'person_id']) {
      if (key in args && args[key] !== actor.personCode) throw new McpReadError('FORBIDDEN_TOOL', '不能查询其他人员的数据', '本工具仅返回当前凭据绑定人员');
      delete args[key];
    }
    const allowed = tool === 'visit_my_perf' ? ['month', 'view'] : ['month'];
    if (Object.keys(args).some(key => !allowed.includes(key))) throw new McpReadError('BAD_PARAM', '包含不支持的参数');
    if (tool === 'visit_my_perf' && args.view !== undefined && !['month', 'daily'].includes(args.view as string)) throw new McpReadError('BAD_PARAM', 'view 必须是 month 或 daily', '可选 view：month、daily');
    if (!actor.personCode) throw new McpReadError('BAD_PARAM', '账号未绑定人员', '当前账号未绑定人员编号，无法查本人数据；请用管理员账号或绑定人员');
    const month = monthArgument(args.month);
    return tool === 'visit_my_perf' ? this.performance(client, actor, month, args.view === 'daily') : this.pay(client, actor, month);
  }

  private async overview(client: QueryClient, args: Record<string, unknown>): Promise<Record<string, unknown>> {
    const view = args.view ?? 'summary';
    if (!['summary', 'months', 'ranking'].includes(view as string)) throw new McpReadError('BAD_PARAM', 'view 必须是 summary、months 或 ranking', '可选 view：summary、months、ranking');
    if (view === 'months') {
      const formal = (await client.query<{ month: string; count: number }>("SELECT to_char(japan_date,'YYYY-MM') AS month,count(*)::int AS count FROM formal_records WHERE japan_date IS NOT NULL GROUP BY 1")).rows;
      const perf = (await client.query<{ month: string; count: number }>("SELECT month,count(*)::int AS count FROM month_perf_records WHERE month<>'' GROUP BY month")).rows;
      const recon = (await client.query<{ params: { month?: string; replaced_by?: unknown } }>("SELECT params FROM recon_tasks WHERE kind='monthly_v3'")).rows;
      const counts = new Map<string, { month: string; formal_records: number; month_perf_records: number; recon_tasks: number }>();
      const row = (month: string) => { if (!counts.has(month)) counts.set(month, { month, formal_records: 0, month_perf_records: 0, recon_tasks: 0 }); return counts.get(month)!; };
      for (const item of formal) row(item.month).formal_records = item.count;
      for (const item of perf) row(item.month).month_perf_records = item.count;
      for (const item of recon) if (item.params.month && !this.replaced(item.params.replaced_by)) row(item.params.month).recon_tasks++;
      const months = [...counts.values()].sort((a, b) => a.month.localeCompare(b.month));
      return { months, total: months.length, ...(!months.length ? { hint: '系统尚无任何月数据（合法结果，不是错误）' } : {}) };
    }
    const month = monthArgument(args.month);
    if (!month) throw new McpReadError('BAD_MONTH', '该视图需要月份', "view='summary'/'ranking' 需要 month（YYYY-MM）");
    const [start, end] = monthBounds(month);
    if (view === 'ranking') {
      const raw = args.limit ?? 10;
      if (!(typeof raw === 'number' && Number.isInteger(raw) || typeof raw === 'string' && /^[+-]?[0-9]+$/.test(raw.trim())) || Number(raw) < 1) throw new McpReadError('BAD_PARAM', 'limit 必须是正整数', 'limit 传 1~100 的整数');
      const limit = Math.min(Number(raw), 100);
      const rows = (await client.query(`SELECT m.person_code,coalesce(p.display_name,m.person_code) AS name,m.points,m.p1,m.p2,m.salary
        FROM month_perf_records m LEFT JOIN persons p ON p.code=m.person_code WHERE m.month=$1 ORDER BY m.points DESC,m.id LIMIT $2`, [month, limit])).rows;
      return { month, limit, count: rows.length, currency: 'JPY', rows, ...(!rows.length ? { hint: '该月无绩效数据（合法结果，不是错误）' } : {}) };
    }
    const formal = (await client.query<{ formal_rows: number; points_total: number; p1_count: number; p2_count: number; persons: number }>(`SELECT count(*)::int AS formal_rows,coalesce(sum(points),0)::int AS points_total,
      count(*) FILTER(WHERE points=1)::int AS p1_count,count(*) FILTER(WHERE points=2)::int AS p2_count,count(DISTINCT person_code)::int AS persons
      FROM formal_records WHERE japan_date >= $1::date AND japan_date < $2::date`, [start, end])).rows[0];
    const salary = (await client.query<{ total: string }>('SELECT coalesce(sum(salary),0)::text AS total FROM month_perf_records WHERE month=$1', [month])).rows[0];
    const recon = (await client.query<{ id: number; params: { month?: string; replaced_by?: unknown } }>("SELECT id,params FROM recon_tasks WHERE kind='monthly_v3' ORDER BY id DESC")).rows.find(item => item.params.month === month && !this.replaced(item.params.replaced_by));
    const hasRecon = recon ? (await client.query<{ present: boolean }>('SELECT EXISTS(SELECT 1 FROM recon_data_rows WHERE task_id=$1) AS present', [recon.id])).rows[0].present : false;
    return { month, ...formal, total_salary: Number(salary.total), has_recon: hasRecon, currency: 'JPY', hint: "summary=某月正式表行数/总点数/人数/工资总额/是否已对账；更细的看板指标与排行用 view='ranking' 或 visit_person 逐人查看" };
  }

  private replaced(value: unknown): boolean {
    if (value == null || value === false || value === 0 || value === '') return false;
    if (Array.isArray(value)) return value.length > 0;
    if (typeof value === 'object') return Object.keys(value).length > 0;
    return true;
  }

  private async person(client: QueryClient, args: Record<string, unknown>): Promise<Record<string, unknown>> {
    for (const key of ['person', 'name', 'month']) if (args[key] != null && typeof args[key] !== 'string') throw new McpReadError('BAD_PARAM', '人员及月份参数必须为文本');
    const key = (args.person as string | undefined)?.trim() || (args.name as string | undefined)?.trim();
    if (!key) throw new McpReadError('BAD_PARAM', 'person 不能为空', '传工号（精确）或姓名（包含），如 P001 或 张三');
    let month = (args.month as string | undefined)?.trim() || null;
    if (!month) {
      const latest = (await client.query<{ month: string | null }>(`SELECT greatest((SELECT max(month) FROM month_perf_records),
        (SELECT to_char(max(japan_date),'YYYY-MM') FROM formal_records)) AS month`)).rows[0];
      month = latest.month;
    }
    if (!month) return { month: null, person_code: null, name: null, summary: null, daily: [], currency: 'JPY', hint: '系统尚无任何月份数据（合法结果，不是错误）' };
    monthArgument(month);
    const [start, end] = monthBounds(month);
    let person = (await client.query<{ code: string; name: string }>(`(SELECT code,display_name AS name FROM persons WHERE code=$1)
      UNION ALL (SELECT code,display_name AS name FROM persons WHERE code<>$1 AND strpos(display_name,$1)>0) LIMIT 1`, [key])).rows[0];
    if (!person) person = (await client.query<{ code: string; name: string }>(`SELECT m.person_code AS code,coalesce(p.display_name,m.person_code) AS name FROM month_perf_records m
      LEFT JOIN persons p ON p.code=m.person_code WHERE m.month=$1 AND (m.person_code=$2 OR strpos(coalesce(p.display_name,m.person_code),$2)>0)
      ORDER BY (m.person_code=$2) DESC,m.points DESC,m.id LIMIT 1`, [month, key])).rows[0];
    if (!person) throw new McpReadError('NOT_FOUND', '未找到人员', "请传工号（精确）或姓名（包含）；可先用 visit_staff(view='list') 查人员编号");
    const materialized = (await client.query<Perf>(`SELECT records,p1,p2,points,salary,per_point,rate37,settle_points,settle_amount,diff_points,diff_amount
      FROM month_perf_records WHERE month=$1 AND person_code=$2 ORDER BY id LIMIT 1`, [month, person.code])).rows[0];
    const daily = (await client.query<Daily>(`SELECT ref_date::text AS date,records,p1,p2,points FROM person_daily_stats
      WHERE person_code=$1 AND ref_date >= $2::date AND ref_date < $3::date ORDER BY ref_date`, [person.code, start, end])).rows;
    const summary = materialized ?? { ...daily.reduce((sum, row) => ({ records: sum.records + row.records, p1: sum.p1 + row.p1, p2: sum.p2 + row.p2, points: sum.points + row.points }), { records: 0, p1: 0, p2: 0, points: 0 }), salary: null, per_point: null, rate37: null };
    return { month, person_code: person.code, name: person.name, summary, daily, currency: 'JPY', hint: "看全公司某月用 visit_overview(view='summary')；看我自己的用 visit_my_perf（员工）" };
  }

  private async performance(client: QueryClient, actor: McpActor, requested: string | null, dailyView: boolean): Promise<Record<string, unknown>> {
    const code = actor.personCode!;
    const name = actor.personName ?? code;
    let month = requested;
    if (!month) {
      const latest = (await client.query<{ month: string | null; day_month: string | null }>(`SELECT
        (SELECT max(month) FROM month_perf_records WHERE person_code=$1) AS month,
        (SELECT to_char(max(ref_date),'YYYY-MM') FROM person_daily_stats WHERE person_code=$1) AS day_month`, [code])).rows[0];
      month = latest.month || latest.day_month;
    }
    if (!month) return { month: null, person_code: code, ...(!dailyView ? { name } : {}), summary: null, daily: [], currency: 'JPY', hint: '该员工暂无绩效数据（合法结果，不是错误）' };
    const configuration = (await client.query<{ staff_visible_from: string }>('SELECT staff_visible_from FROM sys_configs ORDER BY id DESC LIMIT 1')).rows[0];
    const visible = (configuration?.staff_visible_from || process.env.STAFF_VISIBLE_FROM || '2026-10').trim();
    if (!requested && !dailyView && visible && month < visible) month = visible;
    const base = { month, person_code: code, name, currency: 'JPY' };
    if (visible && month < visible) return { ...base, summary: null, daily: [], hint: `该月（${month}）未对员工开放：员工端当前可见起始月为 ${visible}。如需查看请选择 ${visible} 及之后的月份。` };
    const [start, end] = monthBounds(month);
    const materialized = (await client.query<Perf>(`SELECT records,p1,p2,points,salary,per_point,settle_points,settle_amount,diff_points,diff_amount,rate37,pass37
      FROM month_perf_records WHERE month=$1 AND person_code=$2 ORDER BY id LIMIT 1`, [month, code])).rows[0];
    const daily = (await client.query<Daily>(`SELECT ref_date::text AS date,records,p1,p2,points FROM person_daily_stats
      WHERE person_code=$1 AND ref_date >= $2::date AND ref_date < $3::date ORDER BY ref_date`, [code, start, end])).rows;
    const totals = daily.reduce((sum, row) => ({ records: sum.records + row.records, p1: sum.p1 + row.p1, p2: sum.p2 + row.p2, points: sum.points + row.points }), { records: 0, p1: 0, p2: 0, points: 0 });
    const summary = dailyView ? { ...totals, salary: materialized?.salary ?? null, per_point: materialized?.per_point ?? null } : materialized ?? {
      ...totals, salary: null, per_point: null, settle_points: null, settle_amount: null, diff_points: null, diff_amount: null, rate37: null, pass37: null,
    };
    const hint = dailyView && !daily.length ? '该月此人暂无日明细（合法结果，不是错误）' : !dailyView && !daily.length && !summary.points ? '该月此人暂无明细（合法结果，不是错误）' : undefined;
    return { ...base, summary, daily, ...(hint ? { hint } : {}) };
  }

  private async pay(client: QueryClient, actor: McpActor, month: string | null): Promise<Record<string, unknown>> {
    const code = actor.personCode!;
    const adjusts = (await client.query<Adjustment>(`SELECT id,source_month,person_code,adjust_amount,settled_amount,remaining,status,settled_at,source_task_id,source_row_id
      FROM payroll_adjusts WHERE person_code=$1 ORDER BY source_month,id`, [code])).rows;
    const payments = (await client.query<Payment>(`SELECT id,month,seq,points,amount,bonus,adjust_applied,adjust_leftover,paid_at
      FROM payroll_payments WHERE person_code=$1 ORDER BY month,seq,id`, [code])).rows;
    const links = (await client.query<Link>(`SELECT adjust_id,payment_id,month,amount FROM payroll_settlement_links
      WHERE person_code=$1 ORDER BY month,id`, [code])).rows;
    const paymentMap = new Map(payments.map(payment => [payment.id, payment]));
    const adjustMap = new Map(adjusts.map(adjust => [adjust.id, adjust]));
    const byAdjust: Record<string, AdjustLink[]> = {}, byPayment: Record<string, PayLink[]> = {};
    for (const link of links) {
      const payment = paymentMap.get(link.payment_id), adjust = adjustMap.get(link.adjust_id);
      (byAdjust[link.adjust_id] ??= []).push({ payment_id: link.payment_id, month: link.month, seq: payment?.seq ?? null, payment_amount: payment?.amount ?? null, amount: link.amount });
      (byPayment[link.payment_id] ??= []).push({ adjust_id: link.adjust_id, source_month: adjust?.source_month ?? link.month, adjust_amount: adjust?.adjust_amount ?? null, amount: link.amount });
    }
    const allAdjustments = adjusts.map(adjust => ({ adjust_id: adjust.id, source_month: adjust.source_month, person_code: code,
      adjust_amount: adjust.adjust_amount, settled_amount: adjust.settled_amount, remaining: adjust.remaining, status: adjust.status,
      settled_at: iso(adjust.settled_at), source_task_id: adjust.source_task_id, source_row_id: adjust.source_row_id, recovered_by: byAdjust[adjust.id] ?? [] }));
    const selectedAdjustments = allAdjustments.filter(adjust => !month || adjust.source_month === month);
    const unsettled = selectedAdjustments.filter(adjust => adjust.status !== 'settled'), settled = selectedAdjustments.filter(adjust => adjust.status === 'settled');
    const selectedPayments = payments.filter(payment => !month || payment.month === month).map(payment => ({ payment_id: payment.id, month: payment.month, seq: payment.seq,
      points: payment.points, amount: payment.amount, bonus: payment.bonus, adjust_applied: payment.adjust_applied, adjust_leftover: payment.adjust_leftover,
      paid_at: iso(payment.paid_at), applied_to_adjusts: byPayment[payment.id] ?? [] }));
    // Preserve legacy filtering: nested recovered_by/applied_to_adjusts retain their full historical trace.
    const selectedIds = new Set(selectedAdjustments.map(adjust => String(adjust.adjust_id)));
    const outputLinks = month ? {
      by_adjust: Object.fromEntries(Object.entries(byAdjust).filter(([id]) => selectedIds.has(id)).map(([id, rows]) => [id, rows.filter(link => link.month === month)])),
      by_payment: Object.fromEntries(Object.entries(byPayment).map(([id, rows]) => [id, rows.filter(link => link.source_month === month)])),
    } : { by_adjust: byAdjust, by_payment: byPayment };
    const sum = <T>(items: T[], value: (item: T) => number) => items.reduce((total, item) => total + value(item), 0);
    const remaining = sum(selectedAdjustments, adjust => adjust.remaining);
    return { ...(month ? { month } : {}), person_code: code, name: actor.personName ?? code, currency: 'JPY',
      adjusts: { unsettled, settled, count: selectedAdjustments.length, unsettled_count: unsettled.length, settled_count: settled.length,
        original_total: sum(selectedAdjustments, adjust => adjust.adjust_amount), settled_total: sum(selectedAdjustments, adjust => adjust.settled_amount), remaining_total: remaining },
      payments: selectedPayments, links: outputLinks, summary: { remaining_total: remaining, unsettled_count: unsettled.length, settled_count: settled.length,
        paid_total: sum(selectedPayments, payment => payment.amount), adjust_applied_total: sum(selectedPayments, payment => payment.adjust_applied) },
      ...(!selectedAdjustments.length && !selectedPayments.length ? { hint: month ? `${month} 无该员工的找平/发放记录（合法结果，不是错误）` : '该员工暂无找平/发放记录（合法结果，不是错误）' } : {}) };
  }
}
