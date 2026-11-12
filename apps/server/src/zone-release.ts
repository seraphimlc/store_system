/** Formal release writes only bd_* tables. Caller owns the transaction; no source merchant reads. */
import { createHash } from 'node:crypto';
import type { PoolClient } from 'pg';
import { BusinessError } from './errors';
import { arena, gridToMetric, type GridGeometry } from '../../../packages/domain/src/grid-geometry';
import { zoneCountLimit } from '../../../packages/domain/src/zone-rules';
import type { ScopeSource } from '../../../packages/domain/src/zone-scope';
export type ZoneCandidate = Awaited<ReturnType<typeof import('../../../packages/domain/src/zone-scope').previewScopeZones>>;
const reject = (message: string) => new BusinessError('ZONE_RELEASE_PROTECTED', message, 409);
const floatKeys = new Set(['target_area', 'area_weight', 'load_min', 'load_max']);
function pythonJson(value: any, key = ''): string {
    if (value === null)
        return 'null';
    if (Array.isArray(value))
        return '[' + value.map(v => pythonJson(v)).join(', ') + ']';
    if (typeof value === 'object')
        return '{' + Object.keys(value).sort().map(k => pythonJson(k) + ': ' + pythonJson(value[k], k)).join(', ') + '}';
    if (typeof value === 'string')
        return JSON.stringify(value).replace(/[\u007f-\uffff]/g, c => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0'));
    if (typeof value === 'number') {
        if (!Number.isFinite(value))
            throw reject('生成参数无效');
        if (!floatKeys.has(key))
            return String(value);
        if (Object.is(value, -0))
            return '-0.0';
        let text = Math.abs(value) !== 0 && (Math.abs(value) < .0001 || Math.abs(value) >= 1e16) ? value.toExponential() : String(value);
        if (text.includes('e'))
            text = text.replace(/e([+-]?)(\d+)$/, (_, sign, digits) => 'e' + (sign || '+') + digits.padStart(2, '0'));
        else if (!text.includes('.'))
            text += '.0';
        return text;
    }
    if (typeof value === 'boolean')
        return value ? 'true' : 'false';
    throw reject('生成参数无效');
}
export function zoneReleaseFingerprint(result: ZoneCandidate, doc: ScopeSource) { const identity = { source: doc.snapshot_id, parameters: result.summary.balance, zones: result.features.map(f => f.id).sort(), grids: result.grids.map(f => f.id).sort() }; return createHash('sha256').update(pythonJson(identity)).digest('hex'); }
export async function validateZoneCandidateHeader(result: ZoneCandidate, doc: ScopeSource) {
    if (!result.audit?.geometric_pass || !result.audit?.merchant_pass)
        throw reject('覆盖或商户唯一归属检查失败，不能正式生成');
    if (result.meta.snapshot_id !== doc.snapshot_id)
        throw reject('商户版本不一致');
    const limit = await arena(s => zoneCountLimit(s.area(s.own(s.g.GEOSGeom_setPrecision(s.read(gridToMetric(doc.work_area)), .001, 0))) / 1e6, result.summary.target_area_km2)), policy = result.audit.generation_rules;
    if (result.features.length > limit || (policy && policy.max_zone_count !== limit))
        throw reject('片区数量超过生成规则上限');
}
export async function validateZoneMembership(result: ZoneCandidate, doc: ScopeSource) {
    const cells = doc.road_cells, features = result.features, grids = result.grids;
    if (!cells?.length || cells.some(c => !c.metric_geometry))
        throw reject('正式生成需要原生坐标道路基础面');
    const zoneIds = new Set(features.map(f => f.id));
    if (zoneIds.size !== features.length || grids.some(g => !zoneIds.has(g.properties.zone_id)))
        throw reject('片区或网格关联无效');
    const indices = new Map(cells.map((c, i) => [c.id, i]));
    if (indices.size !== cells.length)
        throw reject('街块标识重复');
    const owners = new Map<number, number>();
    grids.forEach((grid, j) => { for (const id of grid.properties.cell_ids ?? []) {
        const i = indices.get(id);
        if (i === undefined)
            throw reject('网格包含未知街块');
        if (owners.has(i))
            throw reject('街块重复归属');
        owners.set(i, j);
    } });
    const excludedIds = result.audit.excluded_empty_cell_ids ?? [];
    if (excludedIds.length && !result.audit.generation_rules)
        throw reject('缺少无商户区域排除审计');
    if (new Set(excludedIds).size !== excludedIds.length || excludedIds.some(id => !indices.has(id)))
        throw reject('排除街块标识无效');
    const excluded = new Set(excludedIds.map(id => indices.get(id)!));
    if ([...excluded].some(i => cells[i].store_count !== 0 || cells[i].merchant_ids?.length))
        throw reject('不能排除有商户的街块');
    if ([...owners.keys()].some(i => excluded.has(i)) || cells.some((_, i) => !owners.has(i) && !excluded.has(i)))
        throw reject('存在未归属街块');
    await arena(s => { const base = cells.map(c => s.own(s.g.GEOSGeom_setPrecision(s.read(c.metric_geometry!), .001, 0))), polygons = grids.map(g => s.own(s.g.GEOSGeom_setPrecision(s.read(gridToMetric(g.geometry as GridGeometry)), .001, 0))); for (const [i, j] of owners)
        if (s.area(s.own(s.g.GEOSDifference(base[i], polygons[j]))) > 1)
            throw reject('网格切断了街块'); });
    const merchants = cells.flatMap(c => c.merchant_ids ?? []);
    if (merchants.length !== new Set(merchants).size || merchants.length !== result.audit.assigned_merchants)
        throw reject('正式门店映射不完整');
    return { owners, excluded, merchants };
}
async function admin(client: PoolClient, id: number) { const { rows: [actor] } = await client.query('SELECT id,username,display_name,role,status,is_active FROM users WHERE id=$1 FOR SHARE', [id]); if (!actor || !actor.is_active || actor.status !== 'active')
    throw reject('账号未在岗'); if (actor.role !== 'admin')
    throw new BusinessError('FORBIDDEN', '仅管理员可操作', 403); return actor; }
async function log(client: PoolClient, actor: any, action: string, id: number, before: unknown, after: unknown) { await client.query("INSERT INTO bd_log(domain,action,ref_id,ref_label,field,old_value,new_value,actor,actor_name,note,created_at) VALUES('zone',$1,$2,$3,'operations',$4,$5,$6,$7,'',now())", [action, id, String(id), JSON.stringify(before), JSON.stringify(after), actor.username, actor.display_name || actor.username]); }
async function generate(client: PoolClient, actor: any, result: ZoneCandidate, doc: ScopeSource) {
    await validateZoneCandidateHeader(result, doc);
    const fingerprint = zoneReleaseFingerprint(result, doc), { rows: [prior] } = await client.query('SELECT id FROM bd_zone_release WHERE fingerprint=$1', [fingerprint]);
    if (prior)
        return { id: prior.id, reused: true };
    if ((await client.query('SELECT id FROM bd_zone_release LIMIT 1')).rowCount)
        throw reject('已有正式版本，禁止覆盖或重复生成任务');
    const { owners, excluded, merchants } = await validateZoneMembership(result, doc), { rows: [release] } = await client.query('INSERT INTO bd_zone_release(fingerprint,source,parameters,audit,reviews,created_by,created_at) VALUES($1,$2,$3,$4,$5,$6,now()) RETURNING id', [fingerprint, JSON.stringify(result.meta.source), JSON.stringify(result.summary.balance), JSON.stringify({ ...result.audit, issues: result.issues, confirmation_ready: result.summary.confirmation_ready }), JSON.stringify(result.review_merchants), actor.id]);
    const regions = result.features.map((f, i) => { const p = f.properties, reasons: string[] = []; if (p.needs_review)
        reasons.push(p.review_reason || 'constraint_conflict'); if (!p.shape_valid)
        reasons.push('shape_conflict'); if (result.grids.some(g => g.properties.zone_id === f.id && !g.properties.area_valid))
        reasons.push('grid_area_conflict'); return { source_id: f.id, name: '片区 ' + String(i + 1).padStart(3, '0'), geometry: f.geometry, area_km2: p.area_km2, store_count: p.store_count, workload: p.workload, review: reasons }; });
    const { rows: insertedRegions } = await client.query(`INSERT INTO bd_region(release_id,source_id,name,geometry,area_km2,store_count,workload,review,state,merge_boundaries,revision,updated_at)
 SELECT $1,source_id,name,geometry,area_km2,store_count,workload,review,'ready',true,0,now() FROM jsonb_to_recordset($2::jsonb) AS x(source_id text,name text,geometry jsonb,area_km2 double precision,store_count int,workload double precision,review jsonb) RETURNING id,source_id`, [release.id, JSON.stringify(regions)]), regionIds = new Map(insertedRegions.map(row => [row.source_id, row.id]));
    const grids = result.grids.map(f => ({ zone_id: regionIds.get(f.properties.zone_id), source_id: f.id, geometry: f.geometry, area_km2: f.properties.area_km2, store_count: f.properties.store_count }));
    const { rows: insertedGrids } = await client.query(`INSERT INTO bd_work_grid(release_id,zone_id,source_id,geometry,area_km2,store_count,state,progress,task_state,revision,updated_at)
 SELECT $1,zone_id,source_id,geometry,area_km2,store_count,'ready',0,'unvisited',0,now() FROM jsonb_to_recordset($2::jsonb) AS x(zone_id int,source_id text,geometry jsonb,area_km2 double precision,store_count int) RETURNING id,source_id`, [release.id, JSON.stringify(grids)]), gridIds = new Map(insertedGrids.map(row => [row.source_id, row.id]));
    let batch: {
        grid_id: number;
        source_id: string;
    }[] = [];
    async function flush() { if (!batch.length)
        return; await client.query('INSERT INTO bd_grid_store(release_id,grid_id,source_id) SELECT $1,grid_id,source_id FROM jsonb_to_recordset($2::jsonb) AS x(grid_id int,source_id text)', [release.id, JSON.stringify(batch)]); batch = []; }
    for (let i = 0; i < doc.road_cells!.length; i++) {
        if (excluded.has(i))
            continue;
        const gridId = gridIds.get(result.grids[owners.get(i)!].id)!;
        for (const sourceId of doc.road_cells![i].merchant_ids ?? []) {
            batch.push({ grid_id: gridId, source_id: sourceId });
            if (batch.length === 5000)
                await flush();
        }
    }
    await flush();
    await log(client, actor, 'generate', release.id, {}, { zones: regions.length, grids: grids.length, merchants: merchants.length });
    return { id: release.id, reused: false };
}
export async function formalZoneRelease(client: PoolClient, actorId: number, result: ZoneCandidate, doc: ScopeSource, replaceUnused = false) {
    const actor = await admin(client, actorId);
    await client.query('SELECT pg_advisory_xact_lock(70422301)');
    if (!replaceUnused)
        return generate(client, actor, result, doc);
    await client.query('LOCK TABLE bd_zone_release,bd_region,bd_work_grid,bd_grid_store,bd_store_visit_log,bd_main_task IN EXCLUSIVE MODE');
    const { rows: old } = await client.query('SELECT id FROM bd_zone_release ORDER BY id FOR UPDATE');
    if (!old.length)
        return generate(client, actor, result, doc);
    if ((await client.query('SELECT id FROM bd_main_task LIMIT 1')).rowCount)
        throw reject('已有巡店批次历史，禁止覆盖片区和网格版本');
    if ((await client.query('SELECT id FROM bd_store_visit_log LIMIT 1')).rowCount || (await client.query("SELECT id FROM bd_work_grid WHERE assignee_id IS NOT NULL OR progress<>0 OR task_state<>'unvisited' LIMIT 1")).rowCount)
        throw reject('已有网格派发或巡店进展，禁止覆盖划分');
    const cellOwner = new Map(result.grids.flatMap(g => (g.properties.cell_ids ?? []).map(id => [id, g.properties.zone_id] as const))), merchantOwner = new Map<string, string>();
    for (const cell of doc.road_cells ?? [])
        if (cellOwner.has(cell.id))
            for (const merchant of cell.merchant_ids ?? [])
                merchantOwner.set(merchant, cellOwner.get(cell.id)!);
    const { rows: assigned } = await client.query('SELECT r.id,r.team_id,r.leader_id,array_remove(array_agg(m.source_id),NULL) AS merchants FROM bd_region r LEFT JOIN bd_work_grid g ON g.zone_id=r.id LEFT JOIN bd_grid_store m ON m.grid_id=g.id WHERE r.team_id IS NOT NULL GROUP BY r.id ORDER BY r.id'), assignments = new Map<string, {
        team_id: number;
        leader_id: number | null;
    }>();
    for (const zone of assigned) {
        if (!zone.merchants.length || zone.merchants.some((id: string) => !merchantOwner.has(id)))
            throw reject('已有团队商户缺少新片区归属，禁止覆盖');
        for (const sourceId of new Set<string>(zone.merchants.map((id: string) => merchantOwner.get(id)!))) {
            const prior = assignments.get(sourceId);
            if (prior && (prior.team_id !== zone.team_id || prior.leader_id !== zone.leader_id))
                throw reject('多个团队归属冲突，禁止覆盖');
            assignments.set(sourceId, { team_id: zone.team_id, leader_id: zone.leader_id });
        }
    }
    await client.query('DELETE FROM bd_grid_store');
    await client.query('DELETE FROM bd_work_grid');
    await client.query('DELETE FROM bd_region');
    await client.query('DELETE FROM bd_zone_release');
    const release = await generate(client, actor, result, doc);
    for (const [sourceId, assignment] of assignments) {
        const { rows: [zone] } = await client.query('UPDATE bd_region SET team_id=$1,leader_id=$2,revision=revision+1 WHERE release_id=$3 AND source_id=$4 RETURNING id', [assignment.team_id, assignment.leader_id, release.id, sourceId]);
        if (!zone)
            throw reject('已有团队商户缺少新片区归属，禁止覆盖');
        await log(client, actor, 'carry_assignment', zone.id, {}, { ...assignment, previous_releases: old.map(row => row.id) });
    }
    await log(client, actor, 'replace_release', release.id, { releases: old.map(row => row.id) }, { release_id: release.id, carried_assignments: assignments.size });
    return { ...release, replaced: true, carriedAssignments: assignments.size };
}
