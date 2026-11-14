-- Search metadata only. Any committed source-geography change invalidates the in-memory facet version.
CREATE TABLE IF NOT EXISTS bd_merchant_search_geography_version (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 revision bigint NOT NULL DEFAULT 0
);
INSERT INTO bd_merchant_search_geography_version(singleton,revision) VALUES(true,0) ON CONFLICT DO NOTHING;
CREATE OR REPLACE FUNCTION bd_merchant_search_geography_changed() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
 UPDATE bd_merchant_search_geography_version SET revision=revision+1 WHERE singleton;
 RETURN NULL;
END
$$;
DROP TRIGGER IF EXISTS merchant_search_geography_changed ON bd_grid_store;
CREATE TRIGGER merchant_search_geography_changed AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON bd_grid_store FOR EACH STATEMENT EXECUTE FUNCTION bd_merchant_search_geography_changed();
DROP TRIGGER IF EXISTS merchant_search_geography_changed ON bd_work_grid;
CREATE TRIGGER merchant_search_geography_changed AFTER INSERT OR DELETE OR TRUNCATE ON bd_work_grid FOR EACH STATEMENT EXECUTE FUNCTION bd_merchant_search_geography_changed();
DROP TRIGGER IF EXISTS merchant_search_geography_updated ON bd_work_grid;
CREATE TRIGGER merchant_search_geography_updated AFTER UPDATE OF id,release_id,zone_id,name,state ON bd_work_grid FOR EACH STATEMENT EXECUTE FUNCTION bd_merchant_search_geography_changed();
DROP TRIGGER IF EXISTS merchant_search_geography_changed ON bd_region;
CREATE TRIGGER merchant_search_geography_changed AFTER INSERT OR DELETE OR TRUNCATE ON bd_region FOR EACH STATEMENT EXECUTE FUNCTION bd_merchant_search_geography_changed();
DROP TRIGGER IF EXISTS merchant_search_geography_updated ON bd_region;
CREATE TRIGGER merchant_search_geography_updated AFTER UPDATE OF id,release_id,name,state ON bd_region FOR EACH STATEMENT EXECUTE FUNCTION bd_merchant_search_geography_changed();

ALTER TABLE bd_merchant_search_log ADD COLUMN IF NOT EXISTS geography_version bigint NOT NULL DEFAULT 0;
