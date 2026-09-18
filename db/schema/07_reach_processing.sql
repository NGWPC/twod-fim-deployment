CREATE TABLE IF NOT EXISTS reach_processing(
    reach_id text PRIMARY KEY REFERENCES reach_network(reach_id) ON DELETE CASCADE,

    halted boolean NOT NULL DEFAULT FALSE,
    halted_at timestamptz,
    CONSTRAINT reach_processing_halted_pair_chk CHECK (halted = (halted_at IS NOT NULL)),

    blocked_on_reach_id text REFERENCES reach_network(reach_id) ON DELETE SET NULL,

    current_step text CONSTRAINT reach_processing_current_step_chk CHECK (current_step IS NULL OR current_step IN ('build_model',
	'run_nd_scenarios', 'run_kwse_scenarios')),
    current_step_started_at timestamptz,
    current_step_ref text,

    current_step_revision integer,

    CONSTRAINT reach_processing_step_pair_chk CHECK ((current_step IS NULL) = (current_step_started_at IS NULL)),

    check_requested_at timestamptz NOT NULL DEFAULT now(),
    last_checked_at timestamptz,

    consecutive_failures integer NOT NULL DEFAULT 0,
    next_retry_at timestamptz,
    last_error text
);

CREATE INDEX IF NOT EXISTS reach_processing_due_idx ON reach_processing(reach_id)
WHERE
    NOT halted AND (last_checked_at IS NULL OR check_requested_at > last_checked_at);

CREATE INDEX IF NOT EXISTS reach_processing_running_idx ON reach_processing(current_step_started_at)
WHERE
    current_step IS NOT NULL;

CREATE INDEX IF NOT EXISTS reach_processing_retry_idx ON reach_processing(next_retry_at)
WHERE
    next_retry_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS reach_processing_blocked_on_idx ON reach_processing(blocked_on_reach_id)
WHERE
    blocked_on_reach_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS reach_activity(
    activity_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    reach_id text NOT NULL REFERENCES reach_network(reach_id) ON DELETE CASCADE,
    started_at timestamptz NOT NULL DEFAULT now(),
    ended_at timestamptz,
    action text NOT NULL CONSTRAINT reach_activity_action_chk CHECK (action IN ('check', 'build_model', 'run_nd_scenarios',
	'run_kwse_scenarios', 'scan',
	'stale_detected',
	'finished'
)),
    outcome text CONSTRAINT reach_activity_outcome_chk CHECK (outcome IS NULL OR outcome IN ('running', 'ok', 'failed',
	'blocked', 'no_change', 'superseded')),

    revision integer,

    detail jsonb,
    error text
);

CREATE INDEX IF NOT EXISTS reach_activity_reach_time_idx ON reach_activity(reach_id, started_at DESC);

CREATE INDEX IF NOT EXISTS reach_activity_time_idx ON reach_activity(started_at DESC);

CREATE INDEX IF NOT EXISTS reach_activity_open_idx ON reach_activity(reach_id)
WHERE
    ended_at IS NULL;
