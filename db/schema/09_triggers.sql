-- 09_triggers.sql
-- desired_state.revision counts how many times a reach's intent has changed. It
-- is per reach and DB owned: the application never writes it.
--
-- The loop's candidate query rests on `applied_revision < revision`, so the one
-- thing that must never happen is revision going backwards while a stale
-- applied_revision survives. That can only occur one way — a desired_state row
-- being deleted and re-created, restarting at 0, while the materialized_* tables
-- (untouched by that delete, since they cascade from reach_network) still hold a
-- higher number. The reach would then look permanently satisfied and never be
-- checked again.
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
--
-- The claim is retracted rather than the rows deleted. -1 is already the
-- schema's sentinel for "satisfies no revision" (08_views.sql, and the loop's
-- candidate query), so this says exactly what is true: something was seen in
-- storage, and it is proof of nothing. The rows themselves stay because what
-- they record — the identity found, the realized domain_code, the discharge set,
-- the upstream-end stages — was seen in storage and still was. Deleting them
-- would assert more than the delete of an intent row justifies, and would throw
-- away values (domain_code above all) that cannot be predicted and would have to
-- be re-read from S3. The next check re-observes and restores a real revision.
CREATE OR REPLACE FUNCTION forget_applied_revision()
    RETURNS TRIGGER
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE materialized_models
    SET applied_revision = -1
    WHERE reach_id = OLD.reach_id;
    UPDATE materialized_nd_runs
    SET applied_revision = -1
    WHERE reach_id = OLD.reach_id;
    UPDATE materialized_kwse_runs
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

-- Changing a default changes every reach's effective intent, because effective
-- intent is COALESCE(desired_state.x, desired_state_defaults.x). So every reach
-- revision moves. That is not a convenience — it is the same statement, said
-- about every row it applies to.
--
-- One mechanism rather than two: the loop compares one revision per reach and
-- never has to reason about a second, global counter running alongside it. The
-- cost is a bulk UPDATE, paid only when a deployment-wide value changes, which
-- is exactly the moment a full re-check is wanted anyway.
CREATE OR REPLACE FUNCTION bump_all_reach_revisions()
    RETURNS TRIGGER
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.revision := OLD.revision + 1;
    -- Touch every reach. Incrementing revision is what makes each row DISTINCT
    -- from its old self, which is what lets desired_state's own BEFORE UPDATE
    -- trigger fire; that trigger then computes the same value authoritatively.
    -- A no-op touch (SET reach_id = reach_id) would be suppressed by that
    -- trigger's WHEN (OLD.* IS DISTINCT FROM NEW.*) guard and bump nothing.
    UPDATE desired_state
    SET revision = revision + 1;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS desired_state_defaults_bump_all ON desired_state_defaults;

CREATE TRIGGER desired_state_defaults_bump_all
    BEFORE UPDATE ON desired_state_defaults
    FOR EACH ROW
    WHEN (OLD.* IS DISTINCT FROM NEW.*)
    EXECUTE FUNCTION bump_all_reach_revisions();

COMMENT ON FUNCTION bump_all_reach_revisions() IS 'BEFORE UPDATE on desired_state_defaults: every reach effective intent changed, so every reach revision moves.';

DROP SEQUENCE IF EXISTS desired_state_revision_seq;

COMMENT ON FUNCTION set_desired_state_revision() IS 'BEFORE INSERT/UPDATE on desired_state: revision starts at 0 and increments per reach on any real change.';

COMMENT ON FUNCTION forget_applied_revision() IS 'AFTER DELETE on desired_state: sets applied_revision to -1 in every materialized_* table for the reach, so a re-created row starting at 0 is not shadowed by a stale claim. The rows stay: what they record was seen in storage.';
