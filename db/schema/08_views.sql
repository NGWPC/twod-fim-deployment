-- 08_views.sql
-- Per guide.md, what can be derived is not stored.
-- ---------------------------------------------------------------------------
-- reach_realized_runs: what the ledger says has actually been realized.
-- ---------------------------------------------------------------------------
-- q_set / n_discharges: distinct library discharges for THIS reach (DR-030).
-- max_kwse         : highest known-WSE actually run on THIS reach (nd runs have
--                    kwse NULL and so are ignored by max()).
-- ds_r_max_us_wse  : the DOWNSTREAM reach's upstream WSEL (us_wse) max, which
--                    bounds this reach's KWSE library. NULL at terminals or
--                    until the downstream reach has runs.
-- Since it is a view, we drop it first and then create it.
DROP VIEW IF EXISTS current_state_realized;

DROP VIEW IF EXISTS reach_realized_runs;

CREATE VIEW reach_realized_runs AS
SELECT
    r.reach_id,
    array_agg(DISTINCT r.q_cms ORDER BY r.q_cms) AS q_set,
    count(DISTINCT r.q_cms) AS n_discharges,
    count(*) FILTER (WHERE r.bc_type = 'nd') AS nd_done,
    count(*) FILTER (WHERE r.bc_type = 'kwse') AS kwse_done,
    max(r.kwse) AS max_kwse,
    (
        SELECT
            max(d.us_wse)
        FROM
            runs d
        WHERE
            d.reach_id = rn.reach_to_id) AS ds_r_max_us_wse
FROM
    runs r
    JOIN reach_network rn ON rn.reach_id = r.reach_id
GROUP BY
    r.reach_id,
    rn.reach_to_id;

COMMENT ON VIEW reach_realized_runs IS 'What the runs ledger has realized per reach: discharges, counts by BC type, KWSE ceiling, and the downstream bound on this reach KWSE library.';

-- ---------------------------------------------------------------------------
-- stale_kwse_runs: results that exist but are no longer valid.
-- ---------------------------------------------------------------------------
-- Staleness is derived, not propagated. A kwse run records the exact downstream
-- run that supplied its BC; when the scanner removes that downstream run the
-- pointer goes NULL and the run shows up here. Nothing walks the network.
DROP VIEW IF EXISTS stale_kwse_runs;

CREATE VIEW stale_kwse_runs AS
SELECT
    r.*
FROM
    runs r
WHERE
    r.bc_type = 'kwse'
    AND r.kwse_transfer_run_row_id IS NULL;

COMMENT ON VIEW stale_kwse_runs IS 'KWSE runs whose source downstream run no longer exists; the gap calculation must redo these.';

-- ---------------------------------------------------------------------------
-- reach_status: one row per reach, everything a status viewer needs.
-- ---------------------------------------------------------------------------
-- Joins intent (desired_state), what exists (current_state, runs), and what the
-- system is doing (reach_processing) so a dashboard is a single SELECT. History
-- and live feeds come from reach_activity instead.
DROP VIEW IF EXISTS reach_status;

CREATE VIEW reach_status AS
SELECT
    rn.reach_id,
    rn.reach_to_id AS downstream_reach_id,
    rn.is_headwater,
    rn.is_terminal,
    COALESCE(p.phase, 'new') AS phase,
    p.blocked_on_reach_id,
    p.current_step,
    p.current_step_started_at,
    now() - p.current_step_started_at AS current_step_elapsed,
    p.current_step_ref,
    cs.model_id,
    cs.identity_hash,
    cs.confirmed_at AS model_confirmed_at,
    d.revision AS desired_revision,
    COALESCE(p.applied_revision, - 1) AS applied_revision,
    -- TRUE when what we want has moved past what we have achieved.
    (d.revision IS NOT NULL
        AND COALESCE(p.applied_revision, - 1) < d.revision) AS has_gap,
    COALESCE(rr.nd_done, 0) AS nd_done,
    p.nd_expected,
    COALESCE(rr.kwse_done, 0) AS kwse_done,
    p.kwse_expected,
    p.consecutive_failures,
    p.last_error,
    p.next_retry_at,
    p.claimed_by,
    p.claim_expires_at,
    p.last_checked_at,
    p.check_requested_at
FROM
    reach_network rn
    LEFT JOIN desired_state d ON d.reach_id = rn.reach_id
    LEFT JOIN current_state cs ON cs.reach_id = rn.reach_id
    LEFT JOIN reach_processing p ON p.reach_id = rn.reach_id
    LEFT JOIN reach_realized_runs rr ON rr.reach_id = rn.reach_id;

COMMENT ON VIEW reach_status IS 'One row per reach joining intent, what exists, and current work status. Backs the status viewer.';
