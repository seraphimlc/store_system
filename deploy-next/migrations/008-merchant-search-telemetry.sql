-- Independent merchant-level telemetry. Never repurpose task IDs, total_in_grid or historical task logs.
CREATE TABLE IF NOT EXISTS bd_merchant_search_log (
 id uuid PRIMARY KEY,
 actor_id integer NOT NULL,
 query_text text NOT NULL,
 search_scope text NOT NULL DEFAULT 'all_merchants' CHECK(search_scope='all_merchants'),
 source_version uuid NOT NULL,
 strategy_version text NOT NULL,
 group_grid_id integer,
 group_region_id integer,
 page integer NOT NULL,
 matched_total integer NOT NULL,
 filtered_total integer NOT NULL,
 result_merchant_ids jsonb NOT NULL,
 duration_ms integer NOT NULL,
 created_at timestamp with time zone NOT NULL DEFAULT now(),
 CHECK(group_grid_id IS NULL OR group_region_id IS NULL)
);
CREATE INDEX IF NOT EXISTS ix_merchant_search_log_created ON bd_merchant_search_log(created_at);
CREATE TABLE IF NOT EXISTS bd_merchant_search_feedback (
 event_id uuid PRIMARY KEY,
 search_log_id uuid NOT NULL REFERENCES bd_merchant_search_log(id) ON DELETE CASCADE,
 actor_id integer NOT NULL,
 merchant_id text NOT NULL,
 kind text NOT NULL CHECK(kind IN('opened','confirmed')),
 created_at timestamp with time zone NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_merchant_search_feedback_log ON bd_merchant_search_feedback(search_log_id,created_at);
