-- Explicit telemetry-only migration, inside a caller-owned transaction, after 002/003.
ALTER TABLE bd_store_search_log ADD COLUMN IF NOT EXISTS request_id uuid;
ALTER TABLE bd_store_search_log ADD COLUMN IF NOT EXISTS query_session_id uuid;
ALTER TABLE bd_store_search_log ADD COLUMN IF NOT EXISTS result_task_ids jsonb NOT NULL DEFAULT '[]';
ALTER TABLE bd_store_search_log ADD COLUMN IF NOT EXISTS context_duration_ms integer;
ALTER TABLE bd_store_search_log ADD COLUMN IF NOT EXISTS query_duration_ms integer;
CREATE UNIQUE INDEX IF NOT EXISTS ix_bd_store_search_log_request ON bd_store_search_log(request_id) WHERE request_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS bd_store_search_feedback (
 event_id uuid PRIMARY KEY,
 search_log_id integer NOT NULL REFERENCES bd_store_search_log(id) ON DELETE CASCADE,
 actor_id integer NOT NULL,
 kind varchar(24) NOT NULL CHECK(kind IN ('opened','confirmed','suggestion_selected')),
 store_task_id integer,
 suggested_name varchar(200) NOT NULL DEFAULT '',
 strategy_version varchar(40) NOT NULL,
 created_at timestamp without time zone NOT NULL DEFAULT timezone('UTC',now()),
 CHECK((kind IN ('opened','confirmed') AND store_task_id IS NOT NULL AND suggested_name='') OR (kind='suggestion_selected' AND store_task_id IS NULL AND suggested_name<>''))
);
CREATE INDEX IF NOT EXISTS ix_bd_store_search_feedback_search_time ON bd_store_search_feedback(search_log_id,created_at);
CREATE INDEX IF NOT EXISTS ix_bd_store_search_feedback_created ON bd_store_search_feedback(created_at);
