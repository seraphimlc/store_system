BEGIN;
CREATE TABLE IF NOT EXISTS auth_sessions (
 token_hash varchar(64) PRIMARY KEY,
 user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 created_at timestamptz NOT NULL DEFAULT now(),
 expires_at timestamptz NOT NULL,
 revoked_at timestamptz
);
CREATE INDEX IF NOT EXISTS auth_sessions_user_idx ON auth_sessions(user_id);
CREATE INDEX IF NOT EXISTS auth_sessions_expiry_idx ON auth_sessions(expires_at);
CREATE TABLE IF NOT EXISTS oauth_client_policies (
 client_id integer PRIMARY KEY REFERENCES oauth_clients(id) ON DELETE CASCADE,
 allowed_scopes text NOT NULL CHECK (allowed_scopes IN ('read','read,write')),
 approved boolean NOT NULL DEFAULT false
);
-- Unknown historical registrations fail closed; an administrator must reapprove them.
INSERT INTO oauth_client_policies(client_id,allowed_scopes,approved)
 SELECT id,'read',false FROM oauth_clients ON CONFLICT DO NOTHING;
-- Reapplying is safe: only pending clients lose credentials, approved clients are preserved.
UPDATE api_tokens SET revoked_at=coalesce(revoked_at,timezone('UTC',now()))
 WHERE name IN (SELECT t.name FROM api_tokens t JOIN oauth_clients c ON t.name LIKE 'OAuth:'||c.id::text||':%' JOIN oauth_client_policies p ON p.client_id=c.id WHERE NOT p.approved);
-- Legacy OAuth access names cannot be bound to a trusted policy; retire them as well.
UPDATE api_tokens SET revoked_at=coalesce(revoked_at,timezone('UTC',now())) WHERE name ~ '^OAuth([[:space:]]|$)';
UPDATE oauth_refresh_tokens SET revoked_at=coalesce(revoked_at,timezone('UTC',now()))
 WHERE client_id IN (SELECT client_id FROM oauth_client_policies WHERE NOT approved);
DELETE FROM oauth_codes WHERE used_at IS NULL AND client_id IN (SELECT client_id FROM oauth_client_policies WHERE NOT approved);

CREATE TABLE IF NOT EXISTS security_export_events (
 id bigserial PRIMARY KEY,
 user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 kind text NOT NULL,
 target text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 finished_at timestamptz,
 ok boolean
);
CREATE INDEX IF NOT EXISTS security_export_events_user_idx ON security_export_events(user_id,created_at);

COMMIT;
