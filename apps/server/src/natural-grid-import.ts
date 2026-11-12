/** Natural grids remain unassigned base assets. Caller owns the transaction; only bd_* writes. */
import type { PoolClient } from 'pg';
import { BusinessError } from './errors';
import { naturalGridCandidate, sha, matches, type NaturalGridSource } from '../../../packages/domain/src/natural-grids';
import { arena, union, gridToMetric, type GridGeometry, type GridPolygon } from '../../../packages/domain/src/grid-geometry';
import { parts, index } from '../../../packages/domain/src/zone-snapshot';
import { sum } from '../../../packages/domain/src/zone-balance';
export type NaturalCandidate = Awaited<ReturnType<typeof naturalGridCandidate>>;
const denied = (message: string) => new BusinessError('NATURAL_IMPORT_PROTECTED', message, 409);
export async function validateNaturalCandidate(result: NaturalCandidate) {
    const doc = result.source_document, audit = result.audit, expected = await naturalGridCandidate(doc);
    if (!audit.geometric_pass || !audit.merchant_pass || sha(result.features) !== expected.audit.geojson_sha256 || sha(audit) !== sha(expected.audit))
        throw denied('候选内容或审计指纹不一致');
    const cells = doc.road_cells, ids = cells.flatMap(c => c.merchant_ids);
    await arena(s => {
        const geoms = cells.map(c => s.read(c.metric_geometry)), land = s.own(s.g.GEOSGeom_setPrecision(s.read(gridToMetric(doc.work_area)), .001, 0)), combined = union(s, geoms), area = sum(geoms.map(p => s.area(p)));
        if (geoms.some(g => s.g.GEOSisValid(g) !== 1 || s.g.GEOSisEmpty(g) !== 0 || s.g.GEOSGeomTypeId(union(s, parts(s, g).map(piece => s.read({ type: 'Polygon', coordinates: [(s.json(piece) as GridPolygon).coordinates[0]] })))) !== 3) || !matches(s, land, combined) || Math.abs(area - s.area(combined)) > Math.max(.01, Math.abs(s.area(combined)) * 1e-9) || new Set(cells.map(c => c.id)).size !== cells.length || ids.length !== new Set(ids).size || ids.length !== doc.store_count)
            throw denied('网格覆盖或商户唯一归属校验失败');
    });
    return ids;
}
export async function importNaturalCandidate(client: PoolClient, actorId: number, result: NaturalCandidate, options: {
    replace?: boolean;
    appendRegion?: boolean;
} = {}) {
    const { rows: [actor] } = await client.query('SELECT id,username,display_name,role,status,is_active FROM users WHERE id=$1 FOR SHARE', [actorId]);
    if (!actor || !actor.is_active || actor.status !== 'active')
        throw denied('账号未在岗');
    if (actor.role !== 'admin')
        throw new BusinessError('FORBIDDEN', '仅管理员可操作', 403);
    await client.query('SELECT pg_advisory_xact_lock(70422301)');
    if (options.replace && options.appendRegion)
        throw denied('追加地区不能覆盖旧划分');
    const doc = result.source_document, region = doc.region_code ?? 'tokyo23';
    if (!['tokyo23', 'tokyo26', 'kanagawa', 'saitama', 'chiba'].includes(region))
        throw denied('地区不存在');
    const fingerprint = sha(result.features);
    if (options.appendRegion) {
        if ((await client.query("SELECT g.id FROM bd_work_grid g JOIN bd_zone_release r ON r.id=g.release_id WHERE coalesce(r.source->>'region_code','tokyo23')=$1 LIMIT 1", [region])).rowCount)
            throw denied('该地区已有正式网格，不能重复追加');
        const { rows: [existing] } = await client.query('SELECT id FROM bd_zone_release WHERE fingerprint=$1', [fingerprint]);
        if (existing)
            return { id: existing.id, reused: true };
    }
    if (!options.replace && !options.appendRegion && (await client.query('SELECT id FROM bd_zone_release LIMIT 1')).rowCount)
        throw denied('已有正式版本；自然网格仅保留候选，须单独完成备份及版本切换');
    const ids = await validateNaturalCandidate(result);
    let audit: Record<string, unknown> = { ...result.audit };
    if ((options.replace || options.appendRegion) && !(audit as any).quality_pass)
        throw denied('网格极端面积、形状或商户负载检查未通过，旧数据未清空');
    let previous: unknown[] = [];
    if (options.replace) {
        await client.query('LOCK TABLE bd_zone_release,bd_region,bd_work_grid,bd_grid_store,bd_store_visit_log,bd_region_task,bd_grid_task IN EXCLUSIVE MODE');
        if ((await client.query('SELECT id FROM bd_store_visit_log LIMIT 1')).rowCount || (await client.query("SELECT id FROM bd_work_grid WHERE assignee_id IS NOT NULL OR progress<>0 OR task_state<>'unvisited' LIMIT 1")).rowCount)
            throw denied('存在网格作业或巡店历史，不能直接清空重建');
        if ((await client.query('SELECT id FROM bd_grid_task LIMIT 1')).rowCount || (await client.query('SELECT id FROM bd_region_task LIMIT 1')).rowCount)
            throw denied('已有巡店批次任务引用片区或网格，不能清空历史任务的基础范围');
        previous = (await client.query('SELECT id,team_id,leader_id FROM bd_region ORDER BY id')).rows;
        await client.query('DELETE FROM bd_grid_store');
        await client.query('DELETE FROM bd_work_grid');
        await client.query('DELETE FROM bd_region');
        // The legacy release archive is retained; never delete historical release rows.
        if ((await client.query("SELECT to_regclass('public.bd_zone') AS legacy")).rows[0].legacy)
            await client.query('DELETE FROM public.bd_zone');
    }
    if (options.appendRegion) {
        if ((await client.query('SELECT id FROM bd_grid_store WHERE source_id=ANY($1::text[]) LIMIT 1', [ids])).rowCount)
            throw denied('追加地区包含已归属其他地区的商户');
        const { rows: existing } = await client.query('SELECT geometry FROM bd_work_grid');
        const overlap = await arena(s => {
            const prior = existing.map(row => s.own(s.g.GEOSGeom_setPrecision(s.read(gridToMetric(row.geometry)), .001, 0))), tree = index(s, prior);
            let total = 0;
            try {
                for (const cell of doc.road_cells) {
                    const geom = s.read(cell.metric_geometry);
                    for (const i of tree.query(geom)) {
                        const area = s.area(s.own(s.g.GEOSIntersection(geom, prior[i])));
                        if (area > .01 && (!matches(s, prior[i], s.own(s.g.GEOSDifference(prior[i], geom))) || !matches(s, geom, s.own(s.g.GEOSDifference(geom, prior[i])))))
                            throw denied('追加地区与现有正式网格存在空间重叠');
                        total += area;
                    }
                }
            }
            finally {
                tree.close();
            }
            return total;
        });
        audit = { ...audit, append_boundary_quantization_overlap_m2: overlap };
    }
    const { rows: [release] } = await client.query('INSERT INTO bd_zone_release(fingerprint,source,parameters,audit,reviews,created_by,created_at) VALUES($1,$2,$3,$4,$5,$6,now()) RETURNING id', [fingerprint, JSON.stringify({ ...doc.source, region_code: region }), JSON.stringify({ generation: doc.generation_version ?? 'natural-1' }), JSON.stringify(audit), JSON.stringify((doc as any).review_merchants ?? []), actor.id]);
    const featureMap = new Map(result.features.map(f => [f.id, f]));
    const areas = await arena(s => doc.road_cells.map(c => s.area(s.read(c.metric_geometry)) / 1e6));
    const grids = doc.road_cells.map((c, i) => ({ source_id: c.id, geometry: featureMap.get(c.id)!.geometry, area_km2: areas[i], store_count: c.merchant_ids.length }));
    const { rows: inserted } = await client.query(`INSERT INTO bd_work_grid(release_id,zone_id,source_id,geometry,area_km2,store_count,state,progress,task_state,revision,updated_at) SELECT $1,NULL,source_id,geometry,area_km2,store_count,'ready',0,'unvisited',0,now() FROM jsonb_to_recordset($2::jsonb) AS x(source_id text,geometry jsonb,area_km2 double precision,store_count int) RETURNING id,source_id`, [release.id, JSON.stringify(grids)]), gridIds = new Map(inserted.map(g => [g.source_id, g.id]));
    let batch: {
        grid_id: number;
        source_id: string;
    }[] = [];
    async function flush() {
        if (!batch.length)
            return;
        await client.query('INSERT INTO bd_grid_store(release_id,grid_id,source_id) SELECT $1,grid_id,source_id FROM jsonb_to_recordset($2::jsonb) AS x(grid_id int,source_id text)', [release.id, JSON.stringify(batch)]);
        batch = [];
    }
    for (const cell of doc.road_cells)
        for (const merchant of cell.merchant_ids) {
            batch.push({ grid_id: gridIds.get(cell.id)!, source_id: merchant });
            if (batch.length === 5000)
                await flush();
        }
    await flush();
    await client.query("INSERT INTO bd_log(domain,action,ref_id,ref_label,field,old_value,new_value,actor,actor_name,note,created_at) VALUES('zone','import_natural_grids',$1,$2,'operations',$3,$4,$5,$6,'',now())", [release.id, String(release.id), JSON.stringify({ cleared_regions: previous }), JSON.stringify({ grids: grids.length, fingerprint }), actor.username, actor.display_name || actor.username]);
    return { id: release.id, reused: false, grids: grids.length, merchants: ids.length };
}
