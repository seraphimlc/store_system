import { Inject, Injectable } from '@nestjs/common';
import type { Database } from './database';
import type { McpActor } from './mcp';
import { McpReadError } from './mcp-read';
import { PayrollService } from './payroll';
import { bonusConfig, salaryFor, previousMonth } from '../../../packages/domain/src/payroll';

const annotations = { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false };
const monthSchema = { type: ['string', 'null'], pattern: '^[0-9]{4}-(0[1-9]|1[0-2])$' };
export const mcpFinanceReadTools = [
  { name: 'visit_payroll', title: '薪资找平查询', description: '仅管理员：month 必填 YYYY-MM。view=rows 两期已物化快照及本月应扣补；adjusts 已确认找平和结清状态；trace 双向核销轨迹。只读，不登记发放或重算。金额为日元。',
    inputSchema: { type: 'object' as const, properties: { month: { ...monthSchema, type: 'string' }, view: { type: 'string', enum: ['rows', 'adjusts', 'trace'], default: 'rows' } }, required: ['month'], additionalProperties: false }, annotations },
  { name: 'visit_recon', title: '对账查询', description: '仅管理员：view=status 月度任务列表；diff 差异快照与当前正式表核对；interpret 读取已有 AI 解读。diff/interpret 指定 task_id 或 month；不生成 AI、不确认找平，不修改账表。',
    inputSchema: { type: 'object' as const, properties: { month: monthSchema, task_id: { type: ['integer', 'null'], minimum: 1 }, view: { type: 'string', enum: ['status', 'diff', 'interpret'], default: 'status' } }, additionalProperties: false }, annotations },
];
type QueryClient = Pick<Database, 'query'>;
type Row = Record<string, any>;
const monthPattern = /^[0-9]{4}-(0[1-9]|1[0-2])$/;
const compare = (a: string, b: string) => a < b ? -1 : a > b ? 1 : 0;
const sum = (rows: Row[], field: string) => rows.reduce((total, row) => total + Number(row[field] || 0), 0);
const iso = (date: Date | null) => date ? date.toISOString().slice(0, 19) : null;
const value = (row: Row, key: string, fallback: any = null) => Object.hasOwn(row, key) ? row[key] : fallback;
function truth(value: unknown): boolean {
  if (value == null || value === false || value === 0 || value === '') return false;
  return Array.isArray(value) ? value.length > 0 : typeof value === 'object' ? Object.keys(value).length > 0 : true;
}
function month(value: unknown, required = false): string | null {
  if (value == null && !required) return null;
  if (typeof value !== 'string' || !monthPattern.test(value)) throw new McpReadError('BAD_MONTH', '月份格式非法，应为 YYYY-MM', '月份必须是 YYYY-MM，例如 2026-09');
  return value;
}
function nextMonth(month: string) { const [year, part] = month.split('-').map(Number); return part === 12 ? `${year + 1}-01` : `${year}-${String(part + 1).padStart(2, '0')}`; }
// Python str(datetime) keeps six fractional digits when present; JS Date alone would lose microseconds.
const settledText = "to_char(a.settled_at,'YYYY-MM-DD HH24:MI:SS')||CASE WHEN mod(extract(microseconds FROM a.settled_at)::int,1000000)<>0 THEN to_char(a.settled_at,'.US') ELSE '' END";

@Injectable()
export class McpFinanceReadService {
  constructor(@Inject(PayrollService) private readonly payroll: PayrollService) {}

  async run(client: QueryClient, actor: McpActor, tool: string, args: Record<string, unknown>): Promise<Row> {
    if (actor.role !== 'admin') throw new McpReadError('FORBIDDEN_TOOL', '该工具仅管理员可用');
    const payroll = tool === 'visit_payroll', allowed = payroll ? ['month', 'view'] : ['month', 'task_id', 'view'];
    if (!mcpFinanceReadTools.some(item => item.name === tool) || Object.keys(args).some(key => !allowed.includes(key))) throw new McpReadError('BAD_PARAM', '包含不支持的财务查询参数');
    const view = args.view === undefined ? (payroll ? 'rows' : 'status') : args.view;
    if (!(payroll ? ['rows', 'adjusts', 'trace'] : ['status', 'diff', 'interpret']).includes(view as string)) throw new McpReadError('BAD_PARAM', '查询视图无效', payroll ? '可选 view：rows、adjusts、trace' : '可选 view：status、diff、interpret');
    if (payroll) {
      const selected = month(args.month, true)!;
      return view === 'rows' ? this.rows(client, selected) : view === 'adjusts' ? this.adjustState(client, selected) : this.trace(client, selected);
    }
    const id = args.task_id;
    if (id != null && (typeof id !== 'number' || !Number.isSafeInteger(id) || id < 1 || id > 2147483647)) throw new McpReadError('BAD_PARAM', 'task_id 必须是正整数');
    const selected = view === 'interpret' && id != null ? null : month(args.month);
    if (view === 'status') return this.status(client, selected);
    if (id == null && selected == null) throw new McpReadError('BAD_PARAM', '必须提供 task_id 或 month 之一', 'task_id=精确任务；month=该月当前版本任务，二选一');
    const task = id != null ? (await client.query('SELECT * FROM recon_tasks WHERE id=$1', [id])).rows[0] :
      (await client.query("SELECT * FROM recon_tasks WHERE kind='monthly_v3' AND params->>'month'=$1 ORDER BY id DESC", [selected])).rows.find(task => !truth(task.params?.replaced_by));
    if (!task) {
      if (view === 'interpret' || id != null) throw new McpReadError('NOT_FOUND', '对账任务不存在', "请先用 visit_recon(view='status') 查看任务");
      return { task: null, month: selected, person_diffs: [], day_diffs: [], system_only: [], report_totals: [], currency: 'JPY', hint: `${selected} 无对账任务（合法结果，不是错误）` };
    }
    return view === 'interpret' ? this.interpret(task) : this.diff(client, task);
  }

  private async rows(client: QueryClient, selected: string): Promise<Row> {
    const period = (await client.query(`SELECT r.*,coalesce(p.display_name,r.person_code) AS name FROM payroll_period_rows r
      LEFT JOIN persons p ON p.code=r.person_code WHERE month=$1 ORDER BY person_code`, [selected])).rows;
    const previous = new Map((await client.query('SELECT person_code,prev_adjust_points FROM payroll_period_rows WHERE month=$1', [previousMonth(selected)])).rows.map(row => [row.person_code, row.prev_adjust_points]));
    const carry = new Map((await this.payroll.carry(selected, client)).map(row => [row.code, row]));
    const links = (await client.query(`SELECT l.person_code,l.payment_id,l.adjust_id,l.amount,a.source_month FROM payroll_settlement_links l
      LEFT JOIN payroll_adjusts a ON a.id=l.adjust_id WHERE l.month=$1 ORDER BY l.id`, [selected])).rows;
    const payments = (await client.query('SELECT person_code,seq FROM payroll_payments WHERE month=$1', [selected])).rows;
    const paid = new Set(payments.map(row => `${row.person_code}:${row.seq}`)), byPerson = new Map<string, Row[]>();
    for (const { person_code, ...link } of links) { if (!byPerson.has(person_code)) byPerson.set(person_code, []); byPerson.get(person_code)!.push(link); }
    const rows = period.map(row => ({ person_code: row.person_code, name: row.name, half1_points: row.half1_points, half2_points: row.half2_points,
      half1_records: row.half1_records, half1_p1: row.half1_p1, half1_p2: row.half1_p2, half2_records: row.half2_records, half2_p1: row.half2_p1, half2_p2: row.half2_p2,
      half1_bonus: row.half1_bonus, half2_bonus: row.half2_bonus, half1_amount: row.half1_amount, half2_amount: row.half2_amount,
      settle_points: row.settle_points, settle_amount: row.settle_amount, prev_points: previous.get(row.person_code) ?? 0, prev_amount: carry.get(row.person_code)?.amount ?? 0,
      diff_points: row.diff_points, diff_amount: row.diff_amount, adjust_points: row.adjust_points, adjust_amount: row.adjust_amount,
      carry_points: carry.get(row.person_code)?.points ?? 0, carry_amount: carry.get(row.person_code)?.amount ?? 0,
      paid_half1: paid.has(`${row.person_code}:1`), paid_half2: paid.has(`${row.person_code}:2`), settlement_links: byPerson.get(row.person_code) ?? [] }));
    return { month: selected, currency: 'JPY', rows, count: rows.length, paid_summary: { half1_persons: rows.filter(row => row.paid_half1).length, half2_persons: rows.filter(row => row.paid_half2).length,
      note: 'paid_half1/2 来自发放台账（导出发薪表即视为该期已发）' }, ...(!rows.length ? { hint: '该月无找平行（合法结果，不是错误）' } : {}) };
  }

  private async ledger(client: QueryClient, selected: string) {
    const adjustments = (await client.query(`SELECT a.*,coalesce(n.display_name,a.person_code) AS name,${settledText} AS settled_text FROM payroll_adjusts a
      LEFT JOIN persons n ON n.code=a.person_code WHERE a.source_month=$1 OR a.id IN(SELECT l.adjust_id FROM payroll_settlement_links l JOIN payroll_payments p ON p.id=l.payment_id WHERE p.month=$1) ORDER BY a.source_month,a.id`, [selected])).rows;
    const payments = (await client.query(`SELECT p.*,coalesce(n.display_name,p.person_code) AS name FROM payroll_payments p LEFT JOIN persons n ON n.code=p.person_code
      WHERE p.month=$1 OR p.id IN(SELECT l.payment_id FROM payroll_settlement_links l JOIN payroll_adjusts a ON a.id=l.adjust_id WHERE a.source_month=$1) ORDER BY p.id`, [selected])).rows;
    const links = (await client.query(`SELECT l.*,a.source_month,a.adjust_amount,a.status AS adjust_status,p.seq,p.amount AS payment_amount FROM payroll_settlement_links l
      LEFT JOIN payroll_adjusts a ON a.id=l.adjust_id LEFT JOIN payroll_payments p ON p.id=l.payment_id WHERE a.source_month=$1 OR p.month=$1 ORDER BY l.id`, [selected])).rows;
    const adjustLinks = new Map<number, Row[]>(), paymentLinks = new Map<number, Row[]>();
    for (const link of links) {
      if (!adjustLinks.has(link.adjust_id)) adjustLinks.set(link.adjust_id, []);
      adjustLinks.get(link.adjust_id)!.push(link);
      if (!paymentLinks.has(link.payment_id)) paymentLinks.set(link.payment_id, []);
      paymentLinks.get(link.payment_id)!.push({ adjust_id: link.adjust_id, source_month: link.source_month, adjust_amount: link.adjust_amount, amount: link.amount, adjust_status: link.adjust_status });
    }
    const recovered = new Map([...adjustLinks].map(([id, rows]) => [id, rows.sort((a, b) => compare(a.month, b.month) || a.id - b.id).map(row => ({ payment_id: row.payment_id, month: row.month, seq: row.seq, payment_amount: row.payment_amount, amount: row.amount }))]));
    return { adjustments, payments, recovered, paymentLinks };
  }

  private async trace(client: QueryClient, selected: string): Promise<Row> {
    const ledger = await this.ledger(client, selected);
    const adjusts = ledger.adjustments.filter(row => row.source_month === selected).map(row => ({ adjust_id: row.id, source_month: row.source_month, person_code: row.person_code, name: row.name,
      adjust_amount: row.adjust_amount, settled_amount: row.settled_amount, remaining: row.remaining, status: row.status, settled_at: row.settled_text,
      source_task_id: row.source_task_id, source_row_id: row.source_row_id, recovered_by: ledger.recovered.get(row.id) ?? [] }));
    const payments = ledger.payments.filter(row => row.month === selected && ledger.paymentLinks.has(row.id)).map(row => ({ payment_id: row.id, month: row.month, seq: row.seq, person_code: row.person_code, name: row.name,
      amount: row.amount, adjust_applied: row.adjust_applied, adjusts: ledger.paymentLinks.get(row.id) }));
    return { currency: 'JPY', filters: { month: selected, person: null, adjust_id: null, payment_id: null }, adjusts, payments,
      summary: { adjust_count: adjusts.length, settled: adjusts.filter(row => row.status === 'settled').length, in_progress: adjusts.filter(row => row.status !== 'settled').length,
        total_adjust_amount: sum(adjusts, 'adjust_amount'), total_settled: sum(adjusts, 'settled_amount'), total_remaining: sum(adjusts, 'remaining') },
      ...(!adjusts.length && !payments.length ? { hint: '无匹配的找平/回收记录（合法结果，不是错误）' } : {}) };
  }

  private async adjustState(client: QueryClient, selected: string): Promise<Row> {
    const adjusted = (await client.query(`SELECT a.*,coalesce(p.display_name,a.person_code) AS name FROM adjust_records a LEFT JOIN persons p ON p.code=a.person_code WHERE month=$1 ORDER BY person_code`, [selected])).rows;
    const perf = (await client.query(`SELECT m.person_code,m.diff_amount,coalesce(p.display_name,m.person_code) AS name FROM month_perf_records m LEFT JOIN persons p ON p.code=m.person_code WHERE month=$1`, [selected])).rows;
    const periods = (await client.query(`SELECT r.*,coalesce(p.display_name,r.person_code) AS name FROM payroll_period_rows r LEFT JOIN persons p ON p.code=r.person_code WHERE month >= $1 ORDER BY month,person_code`, [selected])).rows;
    const current = periods.filter(row => row.month === selected), byAdjust = new Map(adjusted.map(row => [row.person_code, row])), byPerf = new Map(perf.map(row => [row.person_code, row])), byPeriod = new Map(current.map(row => [row.person_code, row]));
    const codes = [...new Set([...byAdjust.keys(), ...byPerf.keys(), ...byPeriod.keys()])].sort(compare);
    const rows = codes.map(code => { const a = byAdjust.get(code), p = byPerf.get(code), r = byPeriod.get(code); return { person_code: code, name: a?.name ?? p?.name ?? r?.name ?? code,
      confirmed: !!a, adjust_points: a?.amount ?? null, adjust_per_point: a?.per_point ?? null, adjust_amount: a ? a.amount * a.per_point : null, amount_adj: a?.amount_adj ?? null,
      applied_to_month: a?.applied_to_month ?? null, reason: a?.reason ?? null, source_task_id: a?.source_task_id ?? null, source_points_diff: a?.source_points_diff ?? null,
      created_at: a ? iso(a.created_at) : null, perf_diff_amount: p?.diff_amount ?? null, payroll_diff_amount: r?.diff_amount ?? null, payroll_adjust_amount: r?.adjust_amount ?? null }; });
    const ledger = await this.ledger(client, selected), source = ledger.adjustments.filter(row => row.source_month === selected).sort((a, b) => compare(a.person_code, b.person_code));
    let settlement: Row;
    if (source.length) {
      const settled = source.map(row => ({ adjust_id: row.id, person_code: row.person_code, name: row.name, adjust_amount: row.adjust_amount, settled_amount: row.settled_amount,
        remaining: row.remaining, status: row.status, settled_at: row.settled_text, source_task_id: row.source_task_id, source: 'payroll_adjusts', recovered_by: ledger.recovered.get(row.id) ?? [] }));
      settlement = { month: selected, currency: 'JPY', rows: settled, count: settled.length, source: 'payroll_adjusts', settled_count: settled.filter(row => row.status === 'settled').length };
    } else {
      const months = new Set(periods.map(row => row.month)), mapped = new Map(periods.map(row => [`${row.month}:${row.person_code}`, row]));
      const oldRows = current.map(row => { let debt = row.prev_adjust_amount, target = nextMonth(selected); const chain = [{ month: selected, debt }];
        while (months.has(target) && debt !== 0) { const next = mapped.get(`${target}:${row.person_code}`); if (!next) break; debt = next.prev_adjust_amount; chain.push({ month: target, debt }); target = nextMonth(target); }
        return { person_code: row.person_code, name: row.name, diff_amount: row.diff_amount, chain, remaining: debt, recovered: row.diff_amount - debt, status: debt === 0 ? 'settled' : 'in_progress' }; });
      settlement = { month: selected, currency: 'JPY', rows: oldRows, count: oldRows.length, ...(!oldRows.length ? { hint: '该月无找平数据（合法结果，不是错误）' } : {}) };
    }
    return { month: selected, rows, currency: 'JPY', summary: { persons: codes.length, confirmed: adjusted.length,
      total_adjust_amount: adjusted.reduce((total, row) => total + (row.amount || 0) * (row.per_point || 250), 0), total_amount_adj: sum(adjusted, 'amount_adj'), total_perf_diff_amount: sum(perf, 'diff_amount') },
      ...(!rows.length ? { hint: `${selected} 无找平/偏差数据（合法结果，不是错误）` } : {}), settlement };
  }

  private config(selected: string, price: number) {
    // Legacy MCP's difference preview uses its month-price snapshot and bonus schedule, not a new salary calculation in the UI.
    return bonusConfig(selected, { perPoint: price, group: Number(process.env.BONUS_GROUP) || 68, amount: Number(process.env.BONUS_AMOUNT) || 3000,
      groupSchedule: process.env.BONUS_GROUP_SCHEDULE, amountSchedule: process.env.BONUS_AMOUNT_SCHEDULE });
  }
  private async prices(client: QueryClient) { return new Map((await client.query('SELECT DISTINCT ON(month) month,per_point FROM month_perf_records ORDER BY month,id')).rows.map(row => [row.month, row.per_point || 250])); }
  private adjustment(system: number, report: number, selected: string, prices: Map<string, any>) { const config = this.config(selected, prices.get(selected) ?? 250); return salaryFor(report || 0, config) - salaryFor(system || 0, config); }

  private async status(client: QueryClient, selected: string | null): Promise<Row> {
    const tasks = (await client.query("SELECT * FROM recon_tasks WHERE kind='monthly_v3' AND ($1::text IS NULL OR params->>'month'=$1) ORDER BY id DESC", [selected])).rows;
    const ids = tasks.map(task => task.id), monthly = (await client.query('SELECT task_id,submitter_code,system_value,report_value,diff FROM recon_results WHERE task_id=ANY($1::int[])', [ids])).rows;
    const daily = (await client.query('SELECT task_id,person_code FROM recon_day_rows WHERE task_id=ANY($1::int[])', [ids])).rows, prices = await this.prices(client);
    const byMonth = new Map<number, Row[]>(), byDay = new Map<number, Row[]>();
    for (const row of monthly) { if (!byMonth.has(row.task_id)) byMonth.set(row.task_id, []); byMonth.get(row.task_id)!.push(row); }
    for (const row of daily) { if (!byDay.has(row.task_id)) byDay.set(row.task_id, []); byDay.get(row.task_id)!.push(row); }
    const out = tasks.map(task => { const p = task.params || {}, persons = new Set<string>(); let amount = 0;
      for (const row of byMonth.get(task.id) ?? []) if (row.diff) { if (row.submitter_code) persons.add(row.submitter_code); amount += this.adjustment(row.system_value, row.report_value, value(p, 'month', ''), prices); }
      for (const row of byDay.get(task.id) ?? []) if (row.person_code) persons.add(row.person_code);
      return { id: task.id, month: value(p, 'month', ''), kind: 'recon', filename: value(p, 'file', ''), status: task.status, created_at: iso(task.created_at), finished_at: iso(task.finished_at),
        is_previous: truth(p.replaced_by), replaced_by: value(p, 'replaced_by'), version: value(p, 'version'), diff_persons: persons.size, diff_amount: amount, currency: 'JPY' }; });
    return { tasks: out, count: out.length, currency: 'JPY', ...(!out.length ? { hint: selected !== null ? `${selected} 无对账任务（合法结果，不是错误）` : '系统尚无对账任务（合法结果，不是错误）' } : {}) };
  }

  private interpret(task: Row): Row {
    const raw = task.summary?.ai_interpret, ai = raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {}, text = ai.text || '';
    return { task_id: task.id, month: value(task.params || {}, 'month', ''), has_interpretation: !!text, interpretation: text || null, model: value(ai, 'model'), generated_at: ai.at == null ? null : typeof ai.at === 'string' ? ai.at : String(ai.at),
      ...(!text ? { hint: '该任务尚无 AI 解读（未生成或解读失败）；可查看报告或人工核对差异' } : {}) };
  }

  private async diff(client: QueryClient, task: Row): Promise<Row> {
    const p = task.params || {}, s = task.summary || {}, selected = value(p, 'month', ''), prices = await this.prices(client);
    const names = new Map((await client.query('SELECT code,display_name FROM persons')).rows.map(row => [row.code, row.display_name]));
    const adjusted = new Set((await client.query('SELECT person_code FROM adjust_records WHERE source_task_id=$1', [task.id])).rows.map(row => row.person_code));
    const current = selected ? new Map((await client.query('SELECT person_code,coalesce(sum(points),0)::int AS points FROM formal_records WHERE japan_date >= $1::date AND japan_date < $2::date GROUP BY person_code', [selected + '-01', nextMonth(selected) + '-01'])).rows.map(row => [row.person_code, row.points])) : new Map();
    const results = (await client.query('SELECT * FROM recon_results WHERE task_id=$1 ORDER BY submitter_code', [task.id])).rows;
    const person_diffs = results.map(row => ({ person_code: row.submitter_code, name: (row.note || '').replaceAll(' 点数差异', '') || (names.has(row.submitter_code) ? names.get(row.submitter_code) : row.submitter_code),
      system_value: row.system_value || 0, report_value: row.report_value || 0, diff: row.diff || 0, adjust_amount: this.adjustment(row.system_value, row.report_value, selected, prices),
      system_value_current: current.get(row.submitter_code) ?? null, snapshot_stale: current.has(row.submitter_code) && (row.system_value || 0) !== current.get(row.submitter_code),
      adjust_amount_current: this.adjustment(current.get(row.submitter_code) ?? row.system_value, row.report_value, selected, prices), adjusted: adjusted.has(row.submitter_code), note: row.note || '' }));
    const day_diffs = (await client.query('SELECT ref_date::text AS date,person_code,sys_points,rep_points,sys_count,rep_count,diff,side,note FROM recon_day_rows WHERE task_id=$1 ORDER BY ref_date,person_code', [task.id])).rows.map(row => ({ ...row, name: names.get(row.person_code) ?? row.person_code }));
    // Legacy summaries store codes; the new reconciliation worker stores {code,name,points}.
    const system_only = [...(s.sys_only || [])].map(entry => {
      const code = typeof entry === 'string' ? entry : entry?.code;
      if (typeof code !== 'string') throw new McpReadError('BAD_PARAM', '对账系统独有名单格式无效');
      return { person_code: code, name: names.get(code) ?? (typeof entry === 'object' ? entry.name ?? code : code) };
    }).sort((a, b) => compare(a.person_code, b.person_code));
    const totals = new Map<string, Row>();
    for (const row of (await client.query('SELECT person_code,person_name,points,cnt FROM recon_data_rows WHERE task_id=$1 ORDER BY id', [task.id])).rows) {
      if (!totals.has(row.person_code)) totals.set(row.person_code, { person_code: row.person_code, name: row.person_name || (names.has(row.person_code) ? names.get(row.person_code) : row.person_code), points: 0, cnt: 0 });
      totals.get(row.person_code)!.points += row.points || 0; totals.get(row.person_code)!.cnt += row.cnt || 0;
    }
    const stale = person_diffs.filter(row => row.snapshot_stale), snapshot = sum(person_diffs, 'adjust_amount'), now = sum(person_diffs, 'adjust_amount_current');
    const hint = !person_diffs.length && !day_diffs.length && !system_only.length ? '该任务无差异行（两侧一致，合法结果，不是错误）' : stale.length ?
      `注意：${stale.length} 人的系统侧快照已过期（任务创建后正式表被重算）→ 找平金额按快照为 ${snapshot.toLocaleString('en-US')}，按当前为 ${now.toLocaleString('en-US')}；**以 current_total 为准**（与找平表一致）。` : undefined;
    return { task: { id: task.id, month: selected, kind: task.kind === 'monthly_v3' ? 'recon' : value(s, 'kind', value(p, 'kind', '')), filename: value(p, 'file', ''), status: task.status,
      version: value(p, 'version'), is_previous: truth(p.replaced_by), replaced_by: value(p, 'replaced_by'), compared: value(s, 'compared', 0), diff_count: value(s, 'diff_count', 0), created_at: iso(task.created_at) },
      person_diffs, day_diffs, system_only, report_totals: [...totals.keys()].sort(compare).map(code => totals.get(code)), currency: 'JPY',
      amount_scope: { snapshot_total: snapshot, current_total: now, stale_persons: stale.map(row => ({ person_code: row.person_code, name: row.name, snapshot_points: row.system_value, current_points: row.system_value_current })),
        note: 'snapshot=任务创建时的系统侧快照（审计留痕）；current=按当前正式表现算（与找平表/工资口径一致）。两者不同说明任务创建后又重算过正式表。' }, ...(hint ? { hint } : {}) };
  }
}
