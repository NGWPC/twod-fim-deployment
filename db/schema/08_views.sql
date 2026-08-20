-- 08_views.sql
-- Per guide.md, what can be derived is not stored.
--
-- Views are dropped and recreated rather than replaced, so this file drops them
-- in dependency order — dependents first. On a fresh boot the order is
-- irrelevant; on a re-run against a live database it is the difference between
-- working and not.
DROP VIEW IF EXISTS reach_status;
-- reach_realized_runs and stale_kwse_runs are gone. The first computed
-- aggregates from per-scenario rows; those aggregates are now stored by observe
-- in the materialized_* tables, so there is nothing to compute. The second
-- looked for KWSE runs whose source run had been deleted; staleness is no
-- longer detected that way — a KWSE library's bounds are recomputed from what
-- the downstream reach currently materializes, so a downstream change fails the
-- span check with no provenance involved.
DROP VIEW IF EXISTS reach_realized_runs;

DROP VIEW IF EXISTS stale_kwse_runs;

-- ---------------------------------------------------------------------------
-- reach_status: one row per reach, everything a status viewer needs.
-- ---------------------------------------------------------------------------
-- Joins intent (desired_state), what has been materialized (materialized_models),
-- and what the system is doing (reach_processing), so a dashboard is a single
-- SELECT. History and live feeds come from reach_activity instead.
DROP VIEW IF EXISTS reach_status;

CREATE VIEW reach_status AS
SELECT
    rn.reach_id,
    rn.reach_to_id AS downstream_reach_id,
    rn.is_headwater,
    rn.is_terminal,
    -- The single place a reach's state is named. reach_processing stores only
    -- halted; everything else here is read off the columns that already say it,
    -- so there is no second answer able to disagree with the first. Order is
    -- precedence: no intent outranks everything, then halted, then a job in
    -- flight over a retry wait.
    CASE WHEN d.reach_id IS NULL THEN
        -- In the network, but nobody has asked for anything here. The loop will
        -- never look at it: the candidate query starts FROM desired_state, so a
        -- reach without intent cannot be a candidate. Named separately from
        -- 'new' because 'new' means "not looked at yet" and this means "never
        -- will be" — a distinction anyone watching this view needs.
        'no_intent'
    WHEN p.halted THEN
        'halted'
    WHEN p.current_step IS NOT NULL THEN
        'in_flight'
    WHEN p.next_retry_at > now() THEN
        'resting'
    WHEN mm.applied_revision >= d.revision
        AND nd.applied_revision >= d.revision THEN
        -- Every implemented step satisfied at the current revision. Each step
        -- carries its own revision and this is their conjunction, so a reach is
        -- finished only when all of them are current. The kwse claim joins this
        -- AND when that step is implemented.
        --
        -- This outranks 'new' deliberately: a reach whose work is materialized
        -- and current is finished whether or not the loop has ever looked at
        -- it. That is the ordinary case after a database is rebuilt against a
        -- populated bucket, and calling it 'new' would suggest work is pending
        -- when there is none. It outranks 'waiting_downstream' for the same
        -- reason — a satisfied reach is satisfied even if a wait pointer from
        -- an earlier rung was left behind.
        'finished'
    WHEN rn.is_terminal
        AND rn.lake_to_id IS NULL
        AND rn.coast_to_id IS NULL
        AND mm.applied_revision >= d.revision THEN
        -- A terminal that names no water body has no normal-depth boundary to
        -- drain through, and no job can produce one. Named for who it waits on,
        -- because that is what separates it from 'waiting_downstream': that one
        -- resolves itself as the wave arrives, this one only when a person
        -- authors the missing data. Deliberately NOT called 'blocked' —
        -- blocked_on_reach_id already means the other kind of waiting, and one
        -- word covering both would be worse than useless to anyone reading it.
        --
        -- Gated on the model being proved so this mirrors the gap calculation,
        -- which only reaches the nd rung once the model rung is satisfied.
        -- Without that gate a fresh network would report reaches as awaiting
        -- inputs while they still had a model to build — true of their nd step,
        -- but misleading about whether the loop has work to do on them.
        'awaiting_inputs'
    WHEN p.blocked_on_reach_id IS NOT NULL THEN
        'waiting_downstream'
    WHEN p.reach_id IS NULL THEN
        -- Never looked at, and nothing materialized to say otherwise.
        'new'
    ELSE
        'due'
    END AS state,
    p.halted,
    p.halted_at,
    p.blocked_on_reach_id,
    p.current_step,
    p.current_step_started_at,
    now() - p.current_step_started_at AS current_step_elapsed,
    p.current_step_ref,
    mm.model_id,
    mm.identity_hash,
    mm.domain_code,
    mm.confirmed_at AS model_confirmed_at,
    d.revision AS desired_revision,
    COALESCE(mm.applied_revision, - 1) AS model_applied_revision,
    -- TRUE when intent has moved past what has been materialized. An absent
    -- materialization row reads as -1, so "never built" and "built against
    -- older intent" answer the same way, which is what the loop wants.
    (d.reach_id IS NOT NULL
        AND (COALESCE(mm.applied_revision, - 1) < d.revision
            OR COALESCE(nd.applied_revision, - 1) < d.revision)) AS has_gap,
    -- Presence of a run row IS the proof that step is materialized, so these
    -- are booleans rather than counts of anything.
    (nd.reach_id IS NOT NULL) AS nd_materialized,
    COALESCE(nd.applied_revision, - 1) AS nd_applied_revision,
    cardinality(nd.q_set) AS nd_discharges,
    nd.q_set AS nd_q_set,
    nd.us_wse_max AS nd_us_wse_max,
    nd.confirmed_at AS nd_confirmed_at,
    (kw.reach_id IS NOT NULL) AS kwse_materialized,
    p.consecutive_failures,
    p.last_error,
    p.next_retry_at,
    p.last_checked_at,
    p.check_requested_at
FROM
    reach_network rn
    LEFT JOIN desired_state d ON d.reach_id = rn.reach_id
    LEFT JOIN materialized_models mm ON mm.reach_id = rn.reach_id
    LEFT JOIN reach_processing p ON p.reach_id = rn.reach_id
    LEFT JOIN materialized_nd_runs nd ON nd.reach_id = rn.reach_id
    LEFT JOIN materialized_kwse_runs kw ON kw.reach_id = rn.reach_id;

COMMENT ON VIEW reach_status IS 'One row per reach joining intent, what exists, and current work status. Backs the status viewer.';
