-- 09_triggers.sql
-- desired_state.revision counts how many times a reach's intent has changed. It
-- is per reach and DB owned: the application never writes it.
--
-- The loop's candidate query rests on `applied_revision < revision`, so the one
-- thing that must never happen is revision going backwards while a stale
-- applied_revision survives. That can only occur one way — a desired_state row
-- being deleted and re-created, restarting at 0, while reach_processing (a
-- different table, untouched by that delete) still holds a high number. The
-- reach would then look permanently satisfied and never be checked again.
--
-- The delete trigger below closes that, which is why the counter can be a plain
-- per-row one rather than a global sequence.
CREATE OR REPLACE FUNCTION set_desired_state_revision()
    RETURNS TRIGGER
    LANGUAGE plpgsql
    AS $$
BEGIN
    -- Authoritative; ignores whatever the caller supplied.
    IF TG_OP = 'INSERT' THEN
        NEW.revision := 0;
    ELSE
        NEW.revision := OLD.revision + 1;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS desired_state_set_revision ON desired_state;

CREATE TRIGGER desired_state_set_revision
    BEFORE INSERT ON desired_state
    FOR EACH ROW
    EXECUTE FUNCTION set_desired_state_revision();

DROP TRIGGER IF EXISTS desired_state_bump_revision ON desired_state;

CREATE TRIGGER desired_state_bump_revision
    BEFORE UPDATE ON desired_state
    FOR EACH ROW
    WHEN (OLD.* IS DISTINCT FROM NEW.*) -- a rewrite with identical values is not a change
    EXECUTE FUNCTION set_desired_state_revision();

-- Deleting intent retracts what was achieved against it. Without this, a
-- re-created row starts at revision 0 while applied_revision still claims a
-- higher one, and the reach is never looked at again.
CREATE OR REPLACE FUNCTION forget_applied_revision()
    RETURNS TRIGGER
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE reach_processing
    SET applied_revision = -1
    WHERE reach_id = OLD.reach_id;
    RETURN OLD;
END;
$$;

DROP TRIGGER IF EXISTS desired_state_forget_applied ON desired_state;

CREATE TRIGGER desired_state_forget_applied
    AFTER DELETE ON desired_state
    FOR EACH ROW
    EXECUTE FUNCTION forget_applied_revision();

DROP SEQUENCE IF EXISTS desired_state_revision_seq;

COMMENT ON FUNCTION set_desired_state_revision() IS 'BEFORE INSERT/UPDATE on desired_state: revision starts at 0 and increments per reach on any real change.';

COMMENT ON FUNCTION forget_applied_revision() IS 'AFTER DELETE on desired_state: clears applied_revision, so a re-created row starting at 0 is not shadowed by a stale claim.';
