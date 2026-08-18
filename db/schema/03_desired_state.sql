-- 03_desired_state.sql
CREATE TABLE IF NOT EXISTS desired_state(
    reach_id bigint PRIMARY KEY REFERENCES reach_network(reach_id) ON DELETE CASCADE,
    -- Every field below is nullable on purpose: NULL = "use the default source",
    -- a value = authored intent (guide.md, Key Design Decisions). A column DEFAULT
    -- would erase that distinction by making an unauthored field look authored,
    -- so this table carries no defaults except the DB-owned revision.
    q_lower_bound integer, -- cms (whole-number flows only)
    q_upper_bound integer, -- cms (whole-number flows only)
    initial_dq_step_for_nd integer, -- cms
    solver text,
    CONSTRAINT desired_state_solver_chk CHECK (solver IS NULL OR solver IN ('lisflood', 'sfincs', 'triton')),
    model_domain geometry(polygon, 5070),
    -- override system TBD
    override_id bigint,
    ld_q_mean_stage_delta double precision, -- m
    ld_q_max_stage_delta double precision, -- m
    ld_q_max_extent_prcnt_delta double precision, -- percent
    ld_ds_z_delta double precision, -- m
    q_set integer[],
    kwse_upper_bound double precision, -- m
    revision integer NOT NULL DEFAULT 0, -- DB owned; per-reach counter set by 09_triggers.sql
    CONSTRAINT desired_state_flow_bounds_chk CHECK (q_lower_bound IS NULL OR q_upper_bound IS NULL OR q_lower_bound < q_upper_bound),
    CONSTRAINT desired_state_kwse_bounds_chk CHECK (kwse_upper_bound IS NULL OR kwse_upper_bound > 0),
    CONSTRAINT desired_state_ld_positive_chk CHECK ((ld_q_mean_stage_delta IS NULL OR ld_q_mean_stage_delta > 0) AND
	(ld_q_max_stage_delta IS NULL OR ld_q_max_stage_delta > 0) AND (ld_q_max_extent_prcnt_delta IS NULL OR
	ld_q_max_extent_prcnt_delta > 0) AND (ld_ds_z_delta IS NULL OR ld_ds_z_delta > 0))
);

COMMENT ON TABLE desired_state IS 'Authored intent, one row per reach. NULL field = use default source; non-NULL = authored. Preserved at all cost.';

COMMENT ON COLUMN desired_state.q_lower_bound IS 'Lower discharge bound for the library (cms); NULL = system default.';

COMMENT ON COLUMN desired_state.q_upper_bound IS 'Upper discharge bound for the library (cms); NULL = system default.';

COMMENT ON COLUMN desired_state.initial_dq_step_for_nd IS 'Initial discharge step for the normal-depth adaptive sweep (cms); NULL = default.';

COMMENT ON COLUMN desired_state.solver IS 'Hydraulic engine; NULL = system default, currently lisflood.';

COMMENT ON COLUMN desired_state.model_domain IS 'Authored domain polygon (EPSG:5070); NULL = system computes. A change forces a model rebuild.';

COMMENT ON COLUMN desired_state.override_id IS 'Active override pointer (overrides table TBD); NULL = no override.';

COMMENT ON COLUMN desired_state.ld_q_mean_stage_delta IS 'Adaptive-stepping target: mean/median stage change between library discharges, m (DR-030).';

COMMENT ON COLUMN desired_state.ld_q_max_stage_delta IS 'Adaptive-stepping target: max stage change between library discharges, m (DR-030).';

COMMENT ON COLUMN desired_state.ld_q_max_extent_prcnt_delta IS 'Adaptive-stepping target: max flooded-extent change between library discharges, percent (DR-030).';

COMMENT ON COLUMN desired_state.ld_ds_z_delta IS 'Downstream KWSE standard stage-grid step, m (DR-033).';

COMMENT ON COLUMN desired_state.q_set IS 'Explicitly authored library discharges (cms); NULL = system computes via the adaptive sweep (DR-030).';

COMMENT ON COLUMN desired_state.kwse_upper_bound IS 'Authored upper KWSE bound (m); lower bound is floored by normal-depth WSEL (DR-032). NULL = system computes.';

COMMENT ON COLUMN desired_state.revision IS 'DB owned, per reach: 0 on INSERT, +1 on any real UPDATE (09_triggers.sql). Counts how many times this reach''s intent has changed.';
