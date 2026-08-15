-- 09_triggers.sql
-- desired_state.revision is the loop's only signal that intent moved. The whole
-- candidate query rests on `applied_revision < revision`, so revision must never
-- go backwards for a reach. A per-row counter starting at 0 does go backwards:
-- delete a desired_state row and re-insert it and revision returns to 0, while
-- reach_processing.applied_revision (a separate table, untouched by that delete)
-- still holds the old higher number — and the reach silently stops being picked
-- up. Drawing from one global sequence makes every new value larger than every
-- value ever issued, so that hole closes.
CREATE SEQUENCE IF NOT EXISTS desired_state_revision_seq AS integer START 1;

COMMENT ON SEQUENCE desired_state_revision_seq IS 'Global source of desired_state.revision values; monotonic across rows so a revision can never be reused or regress.';

CREATE OR REPLACE FUNCTION bump_desired_state_revision()
    RETURNS TRIGGER
    LANGUAGE plpgsql
    AS $$
BEGIN
    -- Authoritative; ignores any value the caller set.
    NEW.revision := nextval('desired_state_revision_seq');
    RETURN NEW;
END;
$$;

-- INSERT is stamped too, so a re-created row starts above whatever
-- applied_revision an older incarnation of that reach left behind.
DROP TRIGGER IF EXISTS desired_state_set_revision ON desired_state;

CREATE TRIGGER desired_state_set_revision
    BEFORE INSERT ON desired_state
    FOR EACH ROW
    EXECUTE FUNCTION bump_desired_state_revision();

DROP TRIGGER IF EXISTS desired_state_bump_revision ON desired_state;

CREATE TRIGGER desired_state_bump_revision
    BEFORE UPDATE ON desired_state
    FOR EACH ROW
    WHEN (OLD.* IS DISTINCT FROM NEW.*) -- skip true no-op updates
    EXECUTE FUNCTION bump_desired_state_revision();

COMMENT ON FUNCTION bump_desired_state_revision() IS 'BEFORE INSERT/UPDATE on desired_state: revision := nextval(desired_state_revision_seq) (DB-owned, monotonic).';
