import { z } from 'zod';
import { BusinessError } from './errors';
import type { PoolClient } from 'pg';

const id = z.coerce.number().int().positive().max(2147483647);
const day = z.string().regex(/^\d{4}-\d{2}-\d{2}$/).refine(value => {
  const date = new Date(`${value}T00:00:00+09:00`);
  return Number.isFinite(+date) && new Date(+date + 9 * 3600000).toISOString().slice(0, 10) === value;
});
const filters = z.object({
  page: id.default(1), limit: id.max(100).default(50), keyword: z.string().trim().max(120).optional(),
  actorId: id.optional(), teamId: id.optional(), regionId: id.optional(), gridId: id.optional(),
  eventType: z.string().max(80).optional(), from: day.optional(), to: day.optional(),
});

export async function readBatchEvents(db: PoolClient, batchId: number, scope: {
  admin: boolean; captainRegions: number[]; regions: { id: number; label: string }[];
  grids: { id: number; regionId: number; label: string }[];
}, input: unknown) {
  const parsed = filters.safeParse(input);
  if (!parsed.success || (parsed.data.from && parsed.data.to && parsed.data.from > parsed.data.to)) {
    throw new BusinessError('VALIDATION', '日志筛选条件无效', 400);
  }
  const f = parsed.data;
  const args: unknown[] = [batchId, scope.admin, scope.captainRegions, scope.grids.map(g => g.id)];
  const arg = (value: unknown) => `$${args.push(value)}`;
  const structural: string[] = [];
  const period: string[] = [];
  const listing: string[] = [];
  const current: string[] = [];
  const grids = scope.grids.filter(g => (!f.regionId || g.regionId === f.regionId) && (!f.gridId || g.id === f.gridId));
  if (f.regionId) structural.push(scope.regions.some(r => r.id === f.regionId) ? `e.region_task_id=${arg(f.regionId)}` : 'false');
  if (f.gridId) structural.push(scope.grids.some(g => g.id === f.gridId) ? `e.grid_task_id=${arg(f.gridId)}` : 'false');
  if (f.actorId) {
    const param = arg(f.actorId);
    period.push(`e.actor_id=${param}`);
    current.push(`s.actor_id=${param}`);
  }
  if (f.teamId) {
    const param = arg(f.teamId);
    period.push(`e.team_id=${param}`);
    current.push(`r.team_id=${param}`);
  }
  if (f.from) period.push(`e.created_at>=(${arg(`${f.from}T00:00:00+09:00`)}::timestamptz AT TIME ZONE 'UTC')`);
  if (f.to) period.push(`e.created_at<(${arg(`${f.to}T00:00:00+09:00`)}::timestamptz AT TIME ZONE 'UTC')`);
  if (f.eventType) listing.push(`e.event_type=${arg(f.eventType)}`);
  let geography = '';
  if (f.keyword) {
    const param = arg(`%${f.keyword}%`);
    const literal = arg(`%${f.keyword.replace(/[\\%_]/g, '\\$&')}%`);
    geography = `, matching_grids AS (SELECT g.id,g.region_task_id FROM bd_grid_task g
      JOIN bd_region_task r ON r.id=g.region_task_id JOIN bd_work_grid b ON b.id=g.grid_id
      WHERE r.main_task_id=$1 AND (${['name','source_id','id::text','stations::text','keywords::text'].map(col => `b.${col} ILIKE ${literal}`).join(' OR ')})),
      named_regions AS (SELECT r.id FROM bd_region_task r JOIN bd_region b ON b.id=r.region_id
        WHERE r.main_task_id=$1 AND b.name ILIKE ${literal}),
      matching_regions AS (SELECT id FROM named_regions UNION SELECT region_task_id FROM matching_grids)`;
    listing.push(`(${['actor_label','team_label','main_label','region_label','grid_label','store_label','store_source_id','store_task_id::text'].map(col => `e.${col} ILIKE ${param}`).join(' OR ')}
      OR e.grid_task_id IN(SELECT id FROM matching_grids) OR e.region_task_id IN(SELECT id FROM named_regions)
      OR (e.grid_task_id IS NULL AND (e.region_task_id IN(SELECT id FROM matching_regions)
        OR (e.region_task_id IS NULL AND EXISTS(SELECT 1 FROM matching_regions)))))`);
  }
  const and = (conditions: string[]) => conditions.length ? `AND ${conditions.join(' AND ')}` : '';
  const gridArg = arg(grids.map(g => g.id));
  const limit = arg(f.limit), offset = arg((f.page - 1) * f.limit);
  const { rows: [result] } = await db.query(`WITH scoped AS (
    SELECT e.* FROM bd_task_event e WHERE e.main_task_id=$1 AND ($2::boolean
      OR e.region_task_id=ANY($3::int[]) OR e.grid_task_id=ANY($4::int[])
      OR (e.region_task_id IS NULL AND e.grid_task_id IS NULL))
  )${geography}, structural AS (SELECT e.* FROM scoped e WHERE true ${and(structural)}),
  matched AS (SELECT e.* FROM structural e WHERE true ${and([...period,...listing])}),
  page_rows AS (SELECT id,event_type,actor_label,actor_role,team_label,region_label,grid_label,store_label,created_at,old_value,new_value
    FROM matched ORDER BY created_at DESC,id DESC LIMIT ${limit} OFFSET ${offset}),
  completions AS (SELECT e.* FROM structural e WHERE e.event_type='complete_store_task'),
  performers AS (SELECT actor_id,actor_label,actor_role,team_label,count(*)::int AS count FROM completions e
    WHERE true ${and(period)} GROUP BY actor_id,actor_label,actor_role,team_label),
  first_ids AS (SELECT min(id) AS id FROM completions GROUP BY store_task_id),
  current_counts AS (SELECT count(*)::int AS total,count(*) FILTER(WHERE s.status='completed')::int AS done
    FROM bd_store_task s JOIN bd_grid_task g ON g.id=s.grid_task_id JOIN bd_region_task r ON r.id=g.region_task_id
    WHERE s.grid_task_id=ANY(${gridArg}::int[]) ${and(current)})
  SELECT (SELECT count(*)::int FROM matched) AS total,
    (SELECT coalesce(jsonb_agg(p ORDER BY p.created_at DESC,p.id DESC),'[]') FROM page_rows p) AS items,
    jsonb_build_object('firstCompleted',(SELECT count(*) FROM completions e JOIN first_ids USING(id) WHERE true ${and(period)}),
      'completionEvents',(SELECT coalesce(sum(count),0) FROM performers),
      'performers',(SELECT coalesce(jsonb_agg(p ORDER BY p.count DESC,p.actor_label),'[]') FROM performers p),
      'currentTotal',c.total,'currentDone',c.done) AS metrics,
    (SELECT coalesce(jsonb_agg(e.event_type ORDER BY e.event_type),'[]') FROM (SELECT DISTINCT event_type FROM scoped) e) AS "eventTypes",
    (SELECT coalesce(jsonb_agg(a ORDER BY a.label,a.id),'[]') FROM (SELECT DISTINCT actor_id AS id,actor_label AS label FROM scoped WHERE actor_id IS NOT NULL) a) AS actors,
    (SELECT coalesce(jsonb_agg(t ORDER BY t.label,t.id),'[]') FROM (SELECT DISTINCT team_id AS id,team_label AS label FROM scoped WHERE team_id IS NOT NULL) t) AS teams
    FROM current_counts c`, args);
  return { ...result, page: f.page, limit: f.limit, regions: scope.regions, grids: scope.grids };
}
