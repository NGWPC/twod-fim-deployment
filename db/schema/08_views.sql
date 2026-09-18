DROP VIEW IF EXISTS reach_status;

DROP VIEW IF EXISTS reach_realized_runs;

DROP VIEW IF EXISTS stale_kwse_runs;

DROP VIEW IF EXISTS reach_status;

CREATE VIEW reach_status AS
SELECT
    rn.reach_id,
    rn.reach_to_id AS downstream_reach_id,
    rn.is_headwater,
    rn.is_terminal,

    CASE WHEN d.reach_id IS NULL THEN

        'no_intent'
    WHEN p.halted THEN
        'halted'
    WHEN p.current_step IS NOT NULL THEN
        'in_flight'
    WHEN p.next_retry_at > now() THEN
        'resting'
    WHEN mm.applied_revision >= d.revision
        AND nd.applied_revision >= d.revision
        AND NOT rn.is_terminal
        AND COALESCE(d.ld_ds_z_delta, f.ld_ds_z_delta) IS NULL THEN

        'awaiting_inputs'
    WHEN mm.applied_revision >= d.revision
        AND nd.applied_revision >= d.revision
        AND (rn.is_terminal
            OR kw.applied_revision >= d.revision) THEN

        'finished'
    WHEN p.blocked_on_reach_id IS NOT NULL THEN
        'awaiting_downstream'
    WHEN p.reach_id IS NULL THEN

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

    (d.reach_id IS NOT NULL
        AND (COALESCE(mm.applied_revision, - 1) < d.revision
            OR COALESCE(nd.applied_revision, - 1) < d.revision
            OR (NOT rn.is_terminal
                AND COALESCE(kw.applied_revision, - 1) < d.revision))) AS has_gap,

    (nd.reach_id IS NOT NULL) AS nd_materialized,
    COALESCE(nd.applied_revision, - 1) AS nd_applied_revision,
    cardinality(nd.q_set) AS nd_discharges,
    nd.q_set AS nd_q_set,
    nd.us_wse_max AS nd_us_wse_max,
    nd.confirmed_at AS nd_confirmed_at,
    (kw.reach_id IS NOT NULL) AS kwse_materialized,
    COALESCE(kw.applied_revision, - 1) AS kwse_applied_revision,

    (SELECT count(*) FROM jsonb_array_elements(kw.scenario_index) g,
                          jsonb_array_elements(g -> 'runs') r) AS kwse_scenarios,
    p.consecutive_failures,
    p.last_error,
    p.next_retry_at,
    p.last_checked_at,
    p.check_requested_at
FROM
    reach_network rn
    LEFT JOIN desired_state d ON d.reach_id = rn.reach_id
    LEFT JOIN desired_state_defaults f ON TRUE
    LEFT JOIN materialized_models mm ON mm.reach_id = rn.reach_id
    LEFT JOIN reach_processing p ON p.reach_id = rn.reach_id
    LEFT JOIN materialized_nd_runs nd ON nd.reach_id = rn.reach_id
    LEFT JOIN materialized_kwse_runs kw ON kw.reach_id = rn.reach_id;
