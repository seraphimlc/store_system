-- Run explicitly inside a transaction; never initializes or rewrites business data.
-- Same columns as the historical search-log migration; Nest deployments use this SQL directly.
CREATE TABLE IF NOT EXISTS bd_store_search_log (
 id serial PRIMARY KEY,
 actor_id integer,
 main_task_id integer NOT NULL,
 region_task_id integer NOT NULL,
 grid_task_id integer NOT NULL,
 query_text varchar(200) NOT NULL DEFAULT '',
 page integer NOT NULL DEFAULT 1,
 task_state varchar(16) NOT NULL DEFAULT '',
 business_status varchar(16) NOT NULL DEFAULT '',
 used_location boolean NOT NULL DEFAULT false,
 total_in_grid integer NOT NULL DEFAULT 0,
 matched_count integer NOT NULL DEFAULT 0,
 keyword_match_count integer NOT NULL DEFAULT 0,
 normalized_match_count integer NOT NULL DEFAULT 0,
 missing_name_count integer NOT NULL DEFAULT 0,
 missing_address_count integer NOT NULL DEFAULT 0,
 missing_source_count integer NOT NULL DEFAULT 0,
 missing_location_count integer NOT NULL DEFAULT 0,
 duration_ms integer NOT NULL DEFAULT 0,
 no_result_reason varchar(24) NOT NULL DEFAULT '',
 created_at timestamp without time zone NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_bd_store_search_log_created_at ON bd_store_search_log(created_at);
CREATE INDEX IF NOT EXISTS ix_bd_store_search_log_grid_time ON bd_store_search_log(grid_task_id,created_at);
