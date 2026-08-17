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
-- reach_processing: where each reach currently stands
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reach_processing(
    reach_id bigint PRIMARY KEY REFERENCES reach_network(reach_id) ON DELETE CASCADE,
    -- The states of reconciliation-loop.md's state diagram, one to one. Which
    -- job is running is NOT encoded here — that is current_step — so the two
    -- can never disagree about it.
    phase text NOT NULL DEFAULT 'new' CONSTRAINT reach_processing_phase_chk CHECK (phase IN ('new', -- never checked
	'pending_check', -- a check has been asked for and not yet started
	'checking', -- claimed, working out the gap
	'processing', -- a job was submitted and is running (see current_step)
	'waiting_downstream', -- gap needs downstream results that do not exist yet
	'resting', -- a step failed; waiting out the retry delay
	'halted', -- failed too many times; parked for a person
	'finished' -- no gap
)),
    -- Which reach we are waiting on, when phase = 'waiting_downstream'. Lets a
    -- viewer draw the wait graph without recomputing the gap. Note this phase is
    -- still a check candidate: the downstream reach asks for a check here when
    -- it finishes, and the sweep would find it regardless.
    blocked_on_reach_id bigint REFERENCES reach_network(reach_id) ON DELETE SET NULL,
    CONSTRAINT reach_processing_blocked_pair_chk CHECK (blocked_on_reach_id IS NULL OR phase = 'waiting_downstream'),
    -- Highest desired_state.revision FULLY satisfied (gap empty at that revision).
    -- Set only when the gap is empty, never per step.
    applied_revision integer NOT NULL DEFAULT - 1,
    -- ------------------------------------------------------------------
    -- What is running right now (NULL when nothing is). Viewer reads these.
    -- ------------------------------------------------------------------
    current_step text CONSTRAINT reach_processing_current_step_chk CHECK (current_step IS NULL OR current_step IN ('build_model',
	'run_nd_scenarios', 'run_kwse_scenarios')),
    current_step_started_at timestamptz,
    current_step_ref text, -- external job/execution id, for log links
    -- desired_state.revision this step is working towards; if desired_state moves
    -- past it, the step is superseded and gets cancelled.
    current_step_revision integer,
    CONSTRAINT reach_processing_step_pair_chk CHECK ((current_step IS NULL) = (current_step_started_at IS NULL)),
    -- A step is running exactly when the reach is in the processing phase.
    CONSTRAINT reach_processing_step_phase_chk CHECK ((current_step IS NOT NULL) = (phase = 'processing')),
    -- ------------------------------------------------------------------
    -- Expected scenario counts, so a viewer can show "12 of 20" without
    -- rerunning the gap calculation. Only the expected side is stored: it is an
    -- output of the gap calculation and cannot be recovered from any other
    -- table. The done side IS derivable (count rows in runs) and so, per
    -- guide.md, is not stored — reach_status computes it.
    -- ------------------------------------------------------------------
    nd_expected integer,
    kwse_expected integer,
    -- ------------------------------------------------------------------
    -- Check scheduling. Asking for a check = set check_requested_at to now().
    -- A check is due when check_requested_at > last_checked_at. Because it is a
    -- single timestamp, many requests arriving before the next check collapse
    -- into one check for free.
    -- ------------------------------------------------------------------
    check_requested_at timestamptz NOT NULL DEFAULT now(),
    last_checked_at timestamptz,
    -- ------------------------------------------------------------------
    -- Claim: one thing works a reach at a time. The deadline is what makes it
    -- safe — a reconciler that dies holding a claim has it expire on its own,
    -- where a plain boolean flag would wedge the reach forever.
    -- ------------------------------------------------------------------
    claimed_by text,
    claim_expires_at timestamptz,
    CONSTRAINT reach_processing_claim_pair_chk CHECK ((claimed_by IS NULL) = (claim_expires_at IS NULL)),
    -- ------------------------------------------------------------------
    -- Failure handling, owned here so a reach can always be retried without a
    -- person editing the database.
    -- ------------------------------------------------------------------
    consecutive_failures integer NOT NULL DEFAULT 0,
    next_retry_at timestamptz,
    last_error text,
    CONSTRAINT reach_processing_resting_chk CHECK (phase <> 'resting' OR next_retry_at IS NOT NULL)
);

-- Candidate query: reaches due a check. The predicate is the same shape as the
-- WHERE in reconciliation-loop.md, so the planner can use this index for it.
CREATE INDEX IF NOT EXISTS reach_processing_due_idx ON reach_processing(reach_id)
WHERE
    phase <> 'halted' AND (last_checked_at IS NULL OR check_requested_at > last_checked_at);

-- Viewer: group reaches by status.
CREATE INDEX IF NOT EXISTS reach_processing_phase_idx ON reach_processing(phase);

-- Viewer: "what is running right now" across the whole network.
CREATE INDEX IF NOT EXISTS reach_processing_running_idx ON reach_processing(current_step_started_at)
WHERE
    current_step IS NOT NULL;

-- Live claims, for ops inspection and for finding lapsed ones.
CREATE INDEX IF NOT EXISTS reach_processing_claim_idx ON reach_processing(claim_expires_at)
WHERE
    claimed_by IS NOT NULL;

-- Reaches resting on a retry delay, so the sweep can pick them up when due.
CREATE INDEX IF NOT EXISTS reach_processing_retry_idx ON reach_processing(next_retry_at)
WHERE
    next_retry_at IS NOT NULL;

-- Wait graph: "who is waiting on this reach".
CREATE INDEX IF NOT EXISTS reach_processing_blocked_on_idx ON reach_processing(blocked_on_reach_id)
WHERE
    blocked_on_reach_id IS NOT NULL;

COMMENT ON TABLE reach_processing IS 'Current work status per reach: phase, what is running, claim, retries, expected counts. The reconciler''s own notes; not rebuildable from storage.';

COMMENT ON COLUMN reach_processing.phase IS 'State from reconciliation-loop.md''s state diagram. Only halted removes a reach from the candidate query.';

COMMENT ON COLUMN reach_processing.blocked_on_reach_id IS 'Reach whose results are needed before this reach can continue; set only with phase = waiting_downstream.';

COMMENT ON COLUMN reach_processing.applied_revision IS 'Highest desired_state.revision fully satisfied. Set only when the gap is empty; -1 = never.';

COMMENT ON COLUMN reach_processing.current_step IS 'Job executing right now; NULL when nothing is. Paired with phase = processing.';

COMMENT ON COLUMN reach_processing.current_step_ref IS 'External execution id for the running step, so a viewer can link to its logs.';

COMMENT ON COLUMN reach_processing.current_step_revision IS 'desired_state.revision the running step targets; if desired_state moves past it the step is superseded.';

COMMENT ON COLUMN reach_processing.nd_expected IS 'Scenario points the last gap calculation expected for nd; done side is counted from runs.';

COMMENT ON COLUMN reach_processing.kwse_expected IS 'Scenario points the last gap calculation expected for kwse; done side is counted from runs.';

COMMENT ON COLUMN reach_processing.check_requested_at IS 'Set to now() to ask for a check. Due when greater than last_checked_at. Many requests collapse into one check.';

COMMENT ON COLUMN reach_processing.last_checked_at IS 'Stamped when a check STARTS (at claim), never when it ends: a check asks for its own next check before releasing, and stamping on release would cancel it.';

COMMENT ON COLUMN reach_processing.claimed_by IS 'Reconciler instance working this reach; NULL = unclaimed. Always paired with an expiry.';

COMMENT ON COLUMN reach_processing.claim_expires_at IS 'Claim deadline. A dead holder''s claim lapses here rather than wedging the reach.';

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
