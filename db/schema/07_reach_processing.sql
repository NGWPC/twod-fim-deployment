-- 07_reach_processing.sql
-- The reconciler's own notes. See knowledge-base system-design/reconciliation-loop.md.
--
-- Two tables:
--   reach_processing  one row per reach, the CURRENT work status (overwritten)
--   reach_activity    append-only history of what happened
--
-- Neither can be rebuilt by rescanning storage — that is exactly why they are
-- separate from current_state (04) and runs (05). Those two describe what
-- exists; these two describe what the system is doing.
-- ---------------------------------------------------------------------------
-- reach_processing: what the loop is DOING to each reach
--
-- Work only. Whether intent has been satisfied is not recorded here — it lives
-- in the materialized_* tables, beside the thing it makes a claim about, so
-- that removing a materialization removes the claim in the same statement.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reach_processing(
    reach_id bigint PRIMARY KEY REFERENCES reach_network(reach_id) ON DELETE CASCADE,
    -- Failed too many times; parked until a person clears it. This is the ONLY
    -- status stored, because it is the only one that is not derivable and the
    -- only one that changes what the loop does — a halted reach stops being
    -- picked up. Everything else a viewer calls a state (checking, in flight,
    -- resting, waiting, finished) is computed from the columns below by
    -- reach_status in 08_views.sql, so this table cannot hold two answers about
    -- the same reach. Why it halted is already recorded: last_error and
    -- consecutive_failures.
    halted boolean NOT NULL DEFAULT FALSE,
    halted_at timestamptz,
    CONSTRAINT reach_processing_halted_pair_chk CHECK (halted = (halted_at IS NOT NULL)),
    -- Which reach we are waiting on, when the gap needs downstream results that
    -- do not exist yet. Lets a viewer draw the wait graph without recomputing
    -- the gap. Such a reach stays a check candidate: the downstream reach asks
    -- for a check here when it finishes, and the sweep finds it regardless.
    blocked_on_reach_id bigint REFERENCES reach_network(reach_id) ON DELETE SET NULL,
    -- ------------------------------------------------------------------
    -- The job in flight (NULL when none is). A check submits a job, records it
    -- here, and ends — it does NOT wait. A later check reads
    -- storage to find out whether the job produced anything, which is why these
    -- columns exist: they are how the loop knows not to submit the same work
    -- twice, and how it knows when a job has been running too long to be alive.
    -- ------------------------------------------------------------------
    current_step text CONSTRAINT reach_processing_current_step_chk CHECK (current_step IS NULL OR current_step IN ('build_model',
	'run_nd_scenarios', 'run_kwse_scenarios')),
    current_step_started_at timestamptz,
    current_step_ref text, -- external job/execution id, for log links
    -- desired_state.revision this step is working towards; if desired_state moves
    -- past it, the step is superseded and gets cancelled.
    current_step_revision integer,
    -- Two facts about one event, so they travel together. The timeout that
    -- decides a job is dead reads started_at, and it must be there whenever a
    -- step is in flight.
    CONSTRAINT reach_processing_step_pair_chk CHECK ((current_step IS NULL) = (current_step_started_at IS NULL)),
    -- ------------------------------------------------------------------
    -- Check scheduling. Asking for a check = set check_requested_at to now().
    -- A check is due when check_requested_at > last_checked_at. Because it is a
    -- single timestamp, many requests arriving before the next check collapse
    -- into one check for free.
    -- ------------------------------------------------------------------
    check_requested_at timestamptz NOT NULL DEFAULT now(),
    last_checked_at timestamptz,
    -- ------------------------------------------------------------------
    -- Failure handling, owned here so a reach can always be retried without a
    -- person editing the database.
    -- ------------------------------------------------------------------
    -- Reset to 0 when a check adopts a materialization for this reach: work
    -- landing is the only thing that honestly ends a streak, and without that
    -- reset "consecutive" would mean "cumulative since someone last cleared a
    -- halt by hand".
    consecutive_failures integer NOT NULL DEFAULT 0,
    next_retry_at timestamptz,
    last_error text
);

-- Candidate query: reaches due a check. The predicate is the same shape as the
-- WHERE in reconciliation-loop.md, so the planner can use this index for it.
CREATE INDEX IF NOT EXISTS reach_processing_due_idx ON reach_processing(reach_id)
WHERE
    NOT halted AND (last_checked_at IS NULL OR check_requested_at > last_checked_at);

-- Viewer: "what is running right now" across the whole network.
CREATE INDEX IF NOT EXISTS reach_processing_running_idx ON reach_processing(current_step_started_at)
WHERE
    current_step IS NOT NULL;

-- Reaches resting on a retry delay, so the sweep can pick them up when due.
CREATE INDEX IF NOT EXISTS reach_processing_retry_idx ON reach_processing(next_retry_at)
WHERE
    next_retry_at IS NOT NULL;

-- Wait graph: "who is waiting on this reach".
CREATE INDEX IF NOT EXISTS reach_processing_blocked_on_idx ON reach_processing(blocked_on_reach_id)
WHERE
    blocked_on_reach_id IS NOT NULL;

COMMENT ON TABLE reach_processing IS 'What the loop is doing to each reach: the job in flight, retries, halted. The reconciler''s own notes; not rebuildable from storage. Holds nothing about whether intent is satisfied — that lives in the materialized_* tables.';

COMMENT ON COLUMN reach_processing.halted IS 'Failed too many times; excluded from the candidate query until a person clears it. The only stored status, because it is the only one not derivable.';

COMMENT ON COLUMN reach_processing.halted_at IS 'When the reach was parked, so ops can see how long it has been waiting on a person.';

COMMENT ON COLUMN reach_processing.blocked_on_reach_id IS 'Reach whose results are needed before this reach can continue; non-NULL is what "waiting downstream" means.';

COMMENT ON COLUMN reach_processing.current_step IS 'Job in flight; NULL when none is. Written when a check submits, cleared by the job status pass or by the check that observes the output. It is what tells a later check that work is already underway.';

COMMENT ON COLUMN reach_processing.current_step_started_at IS 'When the in-flight job was submitted. Informational, plus the clock for giving up on a job the execution system can no longer account for.';

COMMENT ON COLUMN reach_processing.current_step_ref IS 'External execution id for the running step, so a viewer can link to its logs.';

COMMENT ON COLUMN reach_processing.current_step_revision IS 'desired_state.revision the running step targets; if desired_state moves past it the step is superseded.';

COMMENT ON COLUMN reach_processing.check_requested_at IS 'Set to now() to ask for a check. Due when greater than last_checked_at. Many requests collapse into one check.';

COMMENT ON COLUMN reach_processing.last_checked_at IS 'Stamped when a check STARTS, never when it ends: a check asks for its own next check before finishing, and stamping at the end would cancel it. Also what stops one reach being picked up twice in quick succession.';

COMMENT ON COLUMN reach_processing.consecutive_failures IS 'Failed checks since the last success; drives the retry delay.';

COMMENT ON COLUMN reach_processing.next_retry_at IS 'Do not check before this time. NULL = nothing pending.';

-- ---------------------------------------------------------------------------
-- reach_activity: append-only history
-- ---------------------------------------------------------------------------
-- One row per notable event. This is the only table with a time dimension, so
-- it is what a viewer uses to show a timeline, a live feed, or the ripple of
-- work moving upstream. Never updated except to stamp ended_at/outcome on the
-- row opened when a step started.
CREATE TABLE IF NOT EXISTS reach_activity(
    activity_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    reach_id bigint NOT NULL REFERENCES reach_network(reach_id) ON DELETE CASCADE,
    started_at timestamptz NOT NULL DEFAULT now(),
    ended_at timestamptz,
    action text NOT NULL CONSTRAINT reach_activity_action_chk CHECK (action IN ('check', 'build_model', 'run_nd_scenarios',
	'run_kwse_scenarios', 'scan', -- storage scanner corrected current_state / runs
	'stale_detected', -- existing results were invalidated
	'finished' -- gap reached empty
)),
    outcome text CONSTRAINT reach_activity_outcome_chk CHECK (outcome IS NULL OR outcome IN ('running', 'ok', 'failed',
	'blocked', 'no_change', 'superseded')),
    -- desired_state.revision in force when this happened.
    revision integer,
    -- Free-form specifics: scenario points submitted, counts added/removed by a
    -- scan, the reach that caused a staleness, external job ref.
    detail jsonb,
    error text
);

-- Per-reach timeline (newest first).
CREATE INDEX IF NOT EXISTS reach_activity_reach_time_idx ON reach_activity(reach_id, started_at DESC);

-- Global live feed across all reaches.
CREATE INDEX IF NOT EXISTS reach_activity_time_idx ON reach_activity(started_at DESC);

-- Currently-open events (step started, not yet ended).
CREATE INDEX IF NOT EXISTS reach_activity_open_idx ON reach_activity(reach_id)
WHERE
    ended_at IS NULL;

COMMENT ON TABLE reach_activity IS 'Append-only history of what happened to each reach. The only table with a time dimension; source for timelines and live views. Needs a retention policy.';

COMMENT ON COLUMN reach_activity.action IS 'What happened: a check, a step, a storage scan, a staleness detection, or reaching finished.';

COMMENT ON COLUMN reach_activity.outcome IS 'How it ended; running = still open (ended_at NULL).';

COMMENT ON COLUMN reach_activity.detail IS 'Event specifics as JSON: scenario points, scan add/remove counts, causing reach, external job ref.';
