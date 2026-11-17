-- Explicit opt-in migration. Never executed against the business database by application startup.
-- Nullable on purpose: old rows and approved makeups have no place. A failed geocode leaves address null.
BEGIN;
ALTER TABLE bd_attendance_event ADD COLUMN IF NOT EXISTS address text;
ALTER TABLE bd_attendance_event ADD COLUMN IF NOT EXISTS inside_grid boolean;
COMMIT;
