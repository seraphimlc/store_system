-- Apply explicitly after 002-store-search-log.sql, inside a caller-owned transaction.
-- Old API images omit this column and keep the legacy default; business tables are untouched.
ALTER TABLE bd_store_search_log
 ADD COLUMN IF NOT EXISTS strategy_version varchar(40) NOT NULL DEFAULT 'legacy-literal-v0';
