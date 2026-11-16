-- Explicit opt-in migration. Never executed against the business database by application startup.
BEGIN;
CREATE TABLE IF NOT EXISTS bd_attendance_event (
 id bigserial PRIMARY KEY,
 person_code text NOT NULL,
 kind text NOT NULL CHECK (kind IN ('in','out')),
 occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 business_date date GENERATED ALWAYS AS (((occurred_at AT TIME ZONE 'Asia/Tokyo') - interval '5 hours')::date) STORED,
 source text NOT NULL CHECK (source IN ('onsite','makeup')),
 latitude double precision,
 longitude double precision,
 accuracy double precision,
 located_at timestamptz,
 request_key uuid NOT NULL,
 actor_id integer NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE (person_code,request_key),
 CHECK ((source='makeup' AND latitude IS NULL AND longitude IS NULL AND accuracy IS NULL AND located_at IS NULL)
 OR (source='onsite' AND latitude IS NOT NULL AND longitude IS NOT NULL AND accuracy IS NOT NULL AND located_at IS NOT NULL
 AND latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180 AND accuracy>=0 AND accuracy<'Infinity'::float8))
);
CREATE INDEX IF NOT EXISTS bd_attendance_event_day_person ON bd_attendance_event(business_date,person_code,occurred_at,id);
CREATE TABLE IF NOT EXISTS bd_attendance_makeup (
 id bigserial PRIMARY KEY,
 person_code text NOT NULL,
 kind text NOT NULL CHECK (kind IN ('in','out')),
 occurred_at timestamptz NOT NULL,
 business_date date GENERATED ALWAYS AS (((occurred_at AT TIME ZONE 'Asia/Tokyo') - interval '5 hours')::date) STORED,
 reason text NOT NULL CHECK (length(trim(reason)) BETWEEN 1 AND 1000),
 request_key uuid NOT NULL,
 status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
 requested_by integer NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 reviewed_by integer,
 reviewed_at timestamptz,
 review_note text NOT NULL DEFAULT '',
 event_id bigint UNIQUE REFERENCES bd_attendance_event(id),
 UNIQUE (person_code,request_key),
 CHECK ((status='pending' AND reviewed_by IS NULL AND reviewed_at IS NULL AND event_id IS NULL)
 OR (status='approved' AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL AND event_id IS NOT NULL)
 OR (status='rejected' AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL AND event_id IS NULL))
);
CREATE INDEX IF NOT EXISTS bd_attendance_makeup_day_person ON bd_attendance_makeup(business_date,person_code,status);
COMMIT;
