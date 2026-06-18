-- 09_triggers.sql

-- This fires only when an UPDATE actually changes the row (no-op updates that
-- rewrite identical values are skipped)
CREATE OR REPLACE FUNCTION bump_desired_state_revision()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.revision := OLD.revision + 1;   -- authoritative; ignores any value the caller set
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS desired_state_bump_revision ON desired_state;
CREATE TRIGGER desired_state_bump_revision
    BEFORE UPDATE ON desired_state
    FOR EACH ROW
    WHEN (OLD.* IS DISTINCT FROM NEW.*)   -- skip true no-op updates
    EXECUTE FUNCTION bump_desired_state_revision();

COMMENT ON FUNCTION bump_desired_state_revision() IS 'BEFORE UPDATE on desired_state: revision := OLD.revision + 1 (DB-owned heartbeat).';
