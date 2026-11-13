import { Injectable } from '@nestjs/common';
import type { Database } from './database';
import type { McpActor } from './mcp';
import { McpReadError } from './mcp-read';
import { jstToday } from './business-dates';
import { stationSearchVariants, stationLineChinese } from '../../../packages/domain/src/station-cjk';

const annotations = { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false };
const filters = { tab: { type: 'string', default: '' }, kw: { type: 'string', default: '' }, line_id: { type: ['integer', 'null'] } };
export const mcpTaskReadTools = [
  { name: 'visit_my_tasks', title: '我的任务', description: '读取本人今天派工和此前未完成任务、今天完成数及今日自报。仅当前有效团队成员可读本人任务；只读，不派工或更改进度。', inputSchema: { type: 'object' as const, properties: {}, additionalProperties: false }, annotations },
  { name: 'visit_team_tasks', title: '本队任务', description: '队长读取当前带队任务；管理员读取全部队。tab：空、unassigned（无担当）、doing（有担当）、done。支持关键词及 line_id，附本队全部任务计数和最新进展待确认队列。只读。', inputSchema: { type: 'object' as const, properties: filters, additionalProperties: false }, annotations },
  { name: 'visit_task_board', title: '任务总表', description: '仅管理员：任务行、物理车站池计数、全局停滞数及按队汇总。tab：空、unassigned、assigned、done；支持 line_id、kw、stale_only 和 page/per（默认1/20）。只读。', inputSchema: { type: 'object' as const, properties: { ...filters, stale_only: { type: 'boolean', default: false }, page: { type: 'integer', minimum: 1, default: 1 }, per: { type: 'integer', minimum: 1, default: 20 } }, additionalProperties: false }, annotations },
];
type QueryClient = Pick<Database, 'query'>;
type TaskRow = { id: number; team_id: number | null; station: string; line: string; team: string; assignees: string[]; n_assign: number;
  state: string; pct: number; review_status: string | null; last_reported_pct: number | null; last_date: string | null; start_date: string | null;
  done_date: string | null; assign_date: string | null; dispatch_date: string | null; stale: boolean; days_since: number | null; pending: boolean; matches: boolean };
type BoardResult = { rows: TaskRow[]; total: number; stale_count: number; counts: { assigned: number; done: number }; by_team: unknown[] };
function brief(row: TaskRow, today: string, personal = false) {
  return { task_id: row.id, station: row.station, line: row.line, team: row.team, assignees: row.assignees,
    state: row.state, pct: row.pct, review_status: row.review_status, last_reported_pct: row.last_reported_pct, last_date: row.last_date,
    start_date: row.start_date, done_date: row.done_date, assign_date: row.assign_date,
    dispatch_date: personal ? row.dispatch_date : null,
    carried: personal && !!row.dispatch_date && row.dispatch_date < today && row.state !== 'done', stale: row.stale, days_since: row.days_since };
}
function integer(value: unknown, fallback: number, label: string) {
  if (value == null) return fallback;
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 1 || value > 2147483647) throw new McpReadError('BAD_PARAM', `${label} 必须是正整数`);
  return value;
}

// Scope is applied before joins/aggregation. Personal/leader requests never load another team's task rows.
const taskRows = `WITH visible AS (
 SELECT t.* FROM bd_task t WHERE $3='board' OR ($3='teams' AND t.team_id=ANY($4::int[])) OR ($3='mine'
 AND EXISTS(SELECT 1 FROM bd_task_assign own WHERE own.task_id=t.id AND own.person_code=$1)
 AND EXISTS(SELECT 1 FROM bd_team_member m JOIN bd_team team ON team.id=m.team_id
   WHERE m.team_id=t.team_id AND m.person_code=$1 AND m.end_date IS NULL AND m.start_date<=$2::date AND team.status='active'))
),latest AS (
 SELECT DISTINCT ON(pr.task_id) pr.* FROM bd_task_progress pr JOIN visible v ON v.id=pr.task_id ORDER BY pr.task_id,pr.progress_date DESC
),assigned AS (
 SELECT a.task_id,count(*)::int AS n_assign,json_agg(coalesce(nullif(p.display_name,''),a.person_code) ORDER BY a.id) AS names,
 max(a.dispatch_date) FILTER(WHERE a.person_code=$1) AS dispatch_date FROM bd_task_assign a JOIN visible v ON v.id=a.task_id
 LEFT JOIN persons p ON p.code=a.person_code GROUP BY a.task_id
),rows AS (
 SELECT t.id,t.team_id,coalesce(nullif(pl.name,''),s.name,'') AS station,coalesce(nullif(pl.lines_text,''),s.line,'') AS line,
 coalesce(team.name,'') AS team,coalesce(a.names,'[]'::json) AS assignees,coalesce(a.n_assign,0) AS n_assign,t.state,t.pct,
 CASE WHEN pr.id IS NULL THEN '' ELSE pr.review_status END AS review_status,pr.reported_pct AS last_reported_pct,
 pr.progress_date::text AS last_date,t.start_date::text,t.done_date::text,t.assign_date::text,a.dispatch_date::text,
 ($2::date-coalesce(pr.progress_date,t.assign_date)) AS days_since,
 (t.state<>'done' AND coalesce(a.n_assign,0)>0 AND (pr.id IS NULL OR $2::date-coalesce(pr.progress_date,t.assign_date)>=2)) AS stale,
 (pr.id IS NOT NULL AND pr.reported_pct IS NOT NULL AND pr.review_status='pending') AS pending,
 (($5::int IS NULL OR EXISTS(SELECT 1 FROM bd_station ls WHERE ls.line_id=$5 AND (ls.id=t.station_id OR ls.place_id=t.place_id)))
 AND (cardinality($6::text[])=0 OR coalesce(s.name LIKE ANY($6) OR s.line LIKE ANY($6) OR pl.name LIKE ANY($6)
 OR pl.lines_text LIKE ANY($6) OR line.name LIKE ANY($6) OR team.name LIKE ANY($6) OR s.line_id=ANY($7::int[])
 OR EXISTS(SELECT 1 FROM bd_station linked JOIN bd_line ll ON ll.id=linked.line_id WHERE linked.place_id=t.place_id AND ll.name LIKE ANY($6)),false))) AS matches
 FROM visible t LEFT JOIN bd_station s ON s.id=t.station_id LEFT JOIN bd_station_place pl ON pl.id=t.place_id
 LEFT JOIN bd_line line ON line.id=s.line_id LEFT JOIN bd_team team ON team.id=t.team_id
 LEFT JOIN latest pr ON pr.task_id=t.id LEFT JOIN assigned a ON a.task_id=t.id
)`;
// Legacy board filtering treats a report exactly two days old as recent; row/counter stale uses >=2.
// Preserve that pre-existing boundary difference here rather than silently changing the restored contract.
const boardProjection = `,filtered AS (
 SELECT * FROM rows WHERE matches AND ($10='' OR ($10='done' AND state='done')
 OR ($10='assigned' AND state<>'done' AND (team_id IS NOT NULL OR pct>0 OR n_assign>0))
 OR ($10='unassigned' AND state<>'done' AND team_id IS NULL AND pct=0 AND n_assign=0))
 AND (NOT $11::boolean OR (state<>'done' AND n_assign>0 AND (last_date IS NULL OR days_since>2)))
),paged AS (
 SELECT * FROM filtered ORDER BY assign_date DESC NULLS LAST,id LIMIT $8
 OFFSET least($9::bigint,greatest(ceil((SELECT count(*) FROM filtered)::numeric/$8)-1,0)::bigint*$8)
),summary AS (
 SELECT team_id,max(team) AS team_name,count(*)::int AS total,
 count(*) FILTER(WHERE state NOT IN('doing','done'))::int AS unassigned,count(*) FILTER(WHERE state='doing')::int AS doing,
 count(*) FILTER(WHERE state='done')::int AS done,count(*) FILTER(WHERE stale)::int AS stale FROM rows WHERE team_id IS NOT NULL GROUP BY team_id
)
SELECT coalesce((SELECT json_agg(p ORDER BY assign_date DESC NULLS LAST,id) FROM paged p),'[]') AS rows,
 (SELECT count(*)::int FROM filtered) AS total,(SELECT count(*)::int FROM rows WHERE stale) AS stale_count,
 (SELECT json_build_object('assigned',count(*) FILTER(WHERE matches AND state<>'done' AND (team_id IS NOT NULL OR pct>0 OR n_assign>0)),
 'done',count(*) FILTER(WHERE matches AND state='done')) FROM rows) AS counts,
 coalesce((SELECT json_agg(s ORDER BY stale DESC,team_name) FROM summary s),'[]') AS by_team`;

@Injectable()
export class McpTaskReadService {
  async run(client: QueryClient, actor: McpActor, tool: string, input: Record<string, unknown>): Promise<Record<string, unknown>> {
    if (tool === 'visit_task_board' && actor.role !== 'admin' || tool === 'visit_team_tasks' && !['admin', 'leader'].includes(actor.role)) throw new McpReadError('FORBIDDEN_TOOL', '没有任务查询权限');
    const args = { ...input }, today = jstToday();
    if (tool === 'visit_my_tasks') {
      for (const key of ['person', 'person_code', 'person_id']) {
        if (key in args && args[key] !== actor.personCode) throw new McpReadError('FORBIDDEN_TOOL', '不能查询其他人员的任务');
        delete args[key];
      }
    }
    const allowed = tool === 'visit_my_tasks' ? [] : tool === 'visit_team_tasks' ? ['tab', 'kw', 'line_id'] : ['tab', 'kw', 'line_id', 'stale_only', 'page', 'per'];
    if (!mcpTaskReadTools.some(item => item.name === tool) || Object.keys(args).some(key => !allowed.includes(key))) throw new McpReadError('BAD_PARAM', '包含不支持的任务参数');
    const tab = args.tab ?? '', kw = args.kw ?? '';
    if (typeof kw !== 'string' || typeof tab !== 'string' || !['', ...(tool === 'visit_task_board' ? ['unassigned', 'assigned', 'done'] : ['unassigned', 'doing', 'done'])].includes(tab)) throw new McpReadError('BAD_PARAM', '任务筛选参数无效');
    const lineId = args.line_id == null || args.line_id === 0 ? null : integer(args.line_id, 0, 'line_id');
    if (args.stale_only !== undefined && typeof args.stale_only !== 'boolean') throw new McpReadError('BAD_PARAM', 'stale_only 必须为布尔值');
    const per = integer(args.per, 20, 'per'), page = integer(args.page, 1, 'page'), offset = (page - 1) * per;
    if (!Number.isSafeInteger(offset)) throw new McpReadError('BAD_PARAM', '分页超出支持范围');
    let teams: number[] = [];
    if (tool === 'visit_my_tasks' && !actor.personCode) throw new McpReadError('BAD_PARAM', '账号未绑定人员', '当前账号未绑定人员编号，无法查本人数据；请用管理员账号或绑定人员');
    if (tool === 'visit_team_tasks') {
      teams = actor.role === 'admin' ? (await client.query<{ id: number }>('SELECT id FROM bd_team ORDER BY status,id')).rows.map(row => row.id) :
        (await client.query<{ id: number }>(`SELECT DISTINCT team.id FROM bd_team team JOIN bd_team_member m ON m.team_id=team.id
          WHERE m.person_code=$1 AND m.role='leader' AND m.end_date IS NULL AND m.start_date<=$2::date AND team.status='active'
          AND coalesce((SELECT allowed FROM bd_role_cap WHERE role='leader' AND capability='task.view_team'),true) ORDER BY team.id`, [actor.personCode, today])).rows.map(row => row.id);
      if (!teams.length) throw new McpReadError('FORBIDDEN_TOOL', '只有队长能看本队任务', '该账号不是队长，或还没有带队');
    }
    const variants = stationSearchVariants(kw), patterns = variants.map(value => '%' + value + '%');
    const zhLines = variants.length ? (await client.query<{ id: number; name: string }>('SELECT id,name FROM bd_line')).rows.filter(line => variants.some(value => stationLineChinese(line.name).includes(value))).map(line => line.id) : [];
    const scope = tool === 'visit_my_tasks' ? 'mine' : tool === 'visit_team_tasks' ? 'teams' : 'board';
    const values: unknown[] = [actor.personCode, today, scope, teams, lineId, patterns, zhLines];
    if (scope === 'board') {
      const board = (await client.query<BoardResult>(taskRows + boardProjection, [...values, per, offset, tab, args.stale_only ?? false])).rows[0];
      const unassigned = await this.poolCount(client, lineId, patterns, zhLines, variants);
      return { counts: { unassigned, ...board.counts, all: unassigned + board.counts.assigned + board.counts.done }, stale_count: board.stale_count,
        stale_days: 2, total: board.total, page, per, rows: board.rows.map(row => brief(row, today)), by_team: board.by_team,
        hint: 'counts: unassigned=车站池 / assigned=已派队未完成 / done=pct=100；stale=已分到人但 ≥N 天没提交（没分人的不算）' };
    }
    const rows = (await client.query<{ rows: TaskRow[] }>(taskRows + " SELECT coalesce(json_agg(r ORDER BY state,id),'[]') AS rows FROM rows r", values)).rows[0].rows;
    if (scope === 'mine') {
      const kept = rows.filter(row => row.state !== 'done' || row.dispatch_date === today), open = kept.filter(row => row.state !== 'done');
      const report = (await client.query<{ area: string; p1_cnt: number; p2_cnt: number; total: number }>('SELECT area,p1_cnt,p2_cnt,total_cnt AS total FROM staff_daily_reports WHERE person_code=$1 AND report_date=$2::date LIMIT 1', [actor.personCode, today])).rows[0];
      return { date: today, open: open.map(row => brief(row, today, true)), done_count: kept.length - open.length, n_open: open.length, daily_report: report ?? null,
        hint: 'open = 今天要做的（含昨天没做完自动延续的）；daily_report = 今天已填的点数（可空）' };
    }
    const pending = rows.filter(row => row.pending);
    const selected = rows.filter(row => row.matches && (!tab || tab === 'done' && row.state === 'done' || tab === 'unassigned' && row.state !== 'done' && !row.n_assign || tab === 'doing' && row.state !== 'done' && row.n_assign));
    return { counts: { unassigned: rows.filter(row => row.state !== 'done' && !row.n_assign).length, doing: rows.filter(row => row.state !== 'done' && row.n_assign).length,
      done: rows.filter(row => row.state === 'done').length, pending: pending.length }, rows: selected.map(row => brief(row, today)), pending: pending.map(row => brief(row, today)),
      hint: 'tab 口径：未分配=本队未完成且没分人；进行中=已分人；done=pct=100' };
  }

  private async poolCount(client: QueryClient, lineId: number | null, patterns: string[], zhLines: number[], variants: string[]): Promise<number> {
    const compact = variants.map(value => '%' + value.replace(/[ 　]/g, '') + '%');
    return (await client.query<{ count: number }>(`SELECT count(*)::int AS count FROM bd_station_place pl WHERE pl.status='active'
      AND NOT EXISTS(SELECT 1 FROM bd_task t LEFT JOIN bd_station s ON s.id=t.station_id WHERE t.team_id IS NOT NULL AND (t.place_id=pl.id OR s.place_id=pl.id))
      AND ($1::int IS NULL OR EXISTS(SELECT 1 FROM bd_station s WHERE s.place_id=pl.id AND s.line_id=$1))
      AND (cardinality($2::text[])=0 OR pl.name LIKE ANY($2) OR pl.lines_text LIKE ANY($2) OR replace(pl.lines_text,' ','') LIKE ANY($4::text[])
      OR EXISTS(SELECT 1 FROM bd_station s JOIN bd_line l ON l.id=s.line_id WHERE s.place_id=pl.id AND
      (l.name LIKE ANY($2) OR s.line LIKE ANY($2) OR replace(l.name,' ','') LIKE ANY($4) OR replace(coalesce(l.operator_short,'')||l.name,' ','') LIKE ANY($4) OR l.id=ANY($3::int[]))))`, [lineId, patterns, zhLines, compact])).rows[0].count;
  }
}
