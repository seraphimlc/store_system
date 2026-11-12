/** Shared fields and current work protections for the paged list and viewport. */
export const gridAssetsSelect=`SELECT g.id,g.name,g.source_id AS "sourceId",g.release_id AS "releaseId",g.zone_id AS "zoneId",r.name AS "zoneName",r.revision AS "zoneRevision",g.state,g.revision,g.stations,g.keywords,g.store_count AS stores,g.area_km2 AS "areaKm2",
     (g.state='disabled' AND g.zone_id IS NULL AND g.assignee_id IS NULL AND g.progress=0 AND g.task_state='unvisited'
      AND NOT EXISTS(SELECT 1 FROM bd_store_visit_log v WHERE v.grid_id=g.id)) AS convertible,
     (g.state<>'disabled' AND g.assignee_id IS NULL AND g.progress=0 AND g.task_state='unvisited'
      AND NOT EXISTS(SELECT 1 FROM bd_store_visit_log v WHERE v.grid_id=g.id)
      AND (g.zone_id IS NULL OR (r.state<>'archived' AND r.team_id IS NULL AND r.leader_id IS NULL
       AND NOT EXISTS(SELECT 1 FROM bd_work_grid sibling WHERE sibling.zone_id=r.id AND (sibling.assignee_id IS NOT NULL OR sibling.progress<>0 OR sibling.task_state<>'unvisited'
        OR EXISTS(SELECT 1 FROM bd_store_visit_log v WHERE v.grid_id=sibling.id)))))) AS selectable
    FROM bd_work_grid g JOIN bd_zone_release s ON s.id=g.release_id LEFT JOIN bd_region r ON r.id=g.zone_id`;
