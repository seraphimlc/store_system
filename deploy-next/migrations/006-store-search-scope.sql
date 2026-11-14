-- Additive only. Legacy writers retain current-grid defaults; no business-table changes.
ALTER TABLE bd_store_search_log ADD COLUMN IF NOT EXISTS search_scope text NOT NULL DEFAULT 'grid';
ALTER TABLE bd_store_search_log ADD COLUMN IF NOT EXISTS scope_grid_task_ids jsonb NOT NULL DEFAULT '[]'::jsonb;
COMMENT ON COLUMN bd_store_search_log.search_scope IS 'grid or authorized batch scope; total_in_grid is total tasks within recorded search scope';
COMMENT ON COLUMN bd_store_search_log.scope_grid_task_ids IS 'Authorized grid-task IDs frozen at request time; current authorization must be checked again for feedback';
ALTER TABLE bd_store_search_log ADD COLUMN IF NOT EXISTS group_grid_task_id integer;
ALTER TABLE bd_store_search_log ADD COLUMN IF NOT EXISTS group_region_task_id integer;
