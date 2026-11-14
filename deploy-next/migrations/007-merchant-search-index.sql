-- Search-only, rebuildable projection. Apply explicitly; never change source merchants or settlement tables.
-- Tokens live in the GIN expression index, not again in every heap tuple.
-- PostgreSQL substring uses Unicode code points, matching the shared JS character/bigram generator.
CREATE OR REPLACE FUNCTION bd_merchant_search_literal_keys(text,text,text) RETURNS text[]
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
 SELECT ARRAY(SELECT DISTINCT substring(f.value FROM i.pos FOR w.n)
 FROM unnest(ARRAY[$1,$2,$3]) AS f(value)
 CROSS JOIN unnest(ARRAY[1,2]) AS w(n)
 CROSS JOIN LATERAL generate_series(1,char_length(f.value)-w.n+1) AS i(pos))
$$;
CREATE TABLE IF NOT EXISTS bd_merchant_search_generation (
 id uuid PRIMARY KEY,
 strategy_version text NOT NULL,
 source_relation text NOT NULL,
 source_sha256 text,
 document_count integer NOT NULL DEFAULT 0,
 group_count integer NOT NULL DEFAULT 0,
 ready boolean NOT NULL DEFAULT false,
 created_at timestamp with time zone NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS bd_merchant_search_active (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 generation_id uuid NOT NULL REFERENCES bd_merchant_search_generation(id)
);
CREATE TABLE IF NOT EXISTS bd_merchant_search_document (
 generation_id uuid NOT NULL REFERENCES bd_merchant_search_generation(id) ON DELETE CASCADE,
 store_id text NOT NULL,
 name text NOT NULL,
 address text,
 lat double precision,
 lon double precision,
 name_key text NOT NULL,
 normalized_id text NOT NULL,
 normalized_name text NOT NULL,
 normalized_address text NOT NULL,
 PRIMARY KEY(generation_id,store_id)
);
CREATE INDEX IF NOT EXISTS ix_merchant_search_literal ON bd_merchant_search_document USING gin(bd_merchant_search_literal_keys(normalized_id,normalized_name,normalized_address));
CREATE INDEX IF NOT EXISTS ix_merchant_search_name_key ON bd_merchant_search_document(generation_id,name_key);
CREATE TABLE IF NOT EXISTS bd_merchant_search_name (
 generation_id uuid NOT NULL REFERENCES bd_merchant_search_generation(id) ON DELETE CASCADE,
 name_key text NOT NULL,
 name text NOT NULL,
 profile jsonb NOT NULL,
 key_length integer NOT NULL,
 grams text[] NOT NULL,
 core_key text NOT NULL,
 gram_weight double precision NOT NULL DEFAULT 0,
 PRIMARY KEY(generation_id,name_key)
);
CREATE INDEX IF NOT EXISTS ix_merchant_search_name_grams ON bd_merchant_search_name USING gin(grams);
CREATE INDEX IF NOT EXISTS ix_merchant_search_core ON bd_merchant_search_name(generation_id,core_key);
CREATE TABLE IF NOT EXISTS bd_merchant_search_gram_weight (
 generation_id uuid NOT NULL REFERENCES bd_merchant_search_generation(id) ON DELETE CASCADE,
 gram text NOT NULL,
 weight double precision NOT NULL,
 PRIMARY KEY(generation_id,gram)
);

-- Existing (release_id,source_id) uniqueness cannot support global source lookups without a release prefix.
-- This changes no source/association rows and allows the same live geographic mapping after merchant retrieval.
CREATE INDEX IF NOT EXISTS ix_bd_grid_store_source_id ON bd_grid_store(source_id);
