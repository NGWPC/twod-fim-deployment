CREATE OR REPLACE FUNCTION set_desired_state_revision()
    RETURNS TRIGGER
    LANGUAGE plpgsql
    AS $$
BEGIN

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
    WHEN (OLD.* IS DISTINCT FROM NEW.*)
    EXECUTE FUNCTION set_desired_state_revision();

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

CREATE OR REPLACE FUNCTION bump_all_reach_revisions()
    RETURNS TRIGGER
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.revision := OLD.revision + 1;

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

DROP SEQUENCE IF EXISTS desired_state_revision_seq;
