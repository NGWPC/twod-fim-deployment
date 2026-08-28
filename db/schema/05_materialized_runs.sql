-- 05_materialized_runs.sql
--
-- Whether each reach's run intent has been materialized, one table per kind.
--
-- A ROW HERE IS PROOF. It exists only because that step's desired state is
-- materialized — the discharge library spans what intent asked for, at the
-- spacing intent asked for. Had any of that failed there would be no row.
--
-- Two things follow, and both are easy to get wrong:
--
--   Nothing here re-states the criteria. No min/max discharge, no worst
--   spacing. Storing evidence for a check that already passed is redundant,
--   and it invites someone to trust the copy instead of re-checking.
--
--   What is here is what LATER STEPS need. The KWSE step runs a stage library
--   at each discharge of the ND library, so it needs the discharge set. The
--   reach immediately upstream needs this reach's upstream-end stage values to
--   work out its own KWSE bounds. Identity hashes are here because results
--   paths are built from them.
--
-- A partially built library therefore produces no row at all, and observe's
-- scan of it is discarded and repeated on the next check. That is the accepted
-- cost of a table that means proof rather than findings.
--
-- Split by kind because the two steps are satisfied independently and carry
-- different payloads. Held in one table they would need nullable columns and
-- CHECK constraints policing which apply to which — the redundancy that was
-- removed from reach_processing.
-- ---------------------------------------------------------------------------
-- materialized_nd_runs
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS materialized_nd_runs(
    reach_id bigint PRIMARY KEY REFERENCES reach_network(reach_id) ON DELETE CASCADE,
    -- Which model these runs were produced against, and under which solver
    -- recipe. Both are compared against what intent now implies, and both are
    -- components of the results path a later step writes to.
    --
    -- The WHOLE model_id, domain code included, not the identity hash alone.
    -- The job files results under the full id, so that is what the address
    -- needs; and a run produced against a different domain was already refused
    -- at verification, so the identity hash never bought the portability its
    -- narrower form implied.
    model_id text NOT NULL CONSTRAINT materialized_nd_runs_model_id_chk CHECK (model_id ~
	'^[0-9a-f]{8}_N(0|[1-9][0-9]*)S(0|[1-9][0-9]*)E(0|[1-9][0-9]*)W(0|[1-9][0-9]*)$'),
    run_identity_hash char(8) NOT NULL CONSTRAINT materialized_nd_runs_run_hash_chk CHECK (run_identity_hash ~ '^[0-9a-f]{8}$'),
    -- Every discharge in the library, ascending. The KWSE step needs it: a
    -- stage library is built at each of these discharges.
    q_set integer[] NOT NULL,
    -- ------------------------------------------------------------------
    -- Values at THIS reach's upstream end (its Stage Transfer Line). They are
    -- read by the reach immediately upstream, which uses them as the bounds of
    -- its own KWSE library (DR-032 ALT-D):
    --   us_wse_max        that reach's ceiling — one value for all discharges
    --   us_min_wse_curve  that reach's floor — per discharge, so it is a curve.
    --                     Shape: [{"q": <cms>, "wse": <m>}, …] ascending by q.
    --                     The upstream reach takes the entry at the nearest
    --                     discharge at or below its own.
    -- ------------------------------------------------------------------
    us_wse_max double precision NOT NULL,
    us_min_wse_curve jsonb NOT NULL CONSTRAINT materialized_nd_runs_curve_chk CHECK (jsonb_typeof(us_min_wse_curve) = 'array'),
    -- The revision this was proof of. Intent moving past it is the gap.
    applied_revision integer NOT NULL,
    confirmed_at timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE materialized_nd_runs IS 'Proof that a reach ND intent is materialized. Exists only when the discharge library satisfies intent; carries what later steps need, never the criteria it already passed.';

COMMENT ON COLUMN materialized_nd_runs.q_set IS 'Every discharge in the library, ascending. The KWSE step builds a stage library at each.';

COMMENT ON COLUMN materialized_nd_runs.us_wse_max IS 'Maximum WSE at this reach upstream end. The reach immediately upstream uses it as the ceiling of its KWSE library, one value for all discharges (DR-032 ALT-D).';

COMMENT ON COLUMN materialized_nd_runs.us_min_wse_curve IS 'Minimum WSE at this reach upstream end, per discharge: [{"q":…,"wse":…}, …] ascending. The upstream reach reads the entry at the nearest discharge at or below its own to get its KWSE floor (DR-032 ALT-D).';

COMMENT ON COLUMN materialized_nd_runs.applied_revision IS 'The intent revision this proves. Co-located with its subject, so deleting the row retracts the proof.';

-- ---------------------------------------------------------------------------
-- materialized_kwse_runs
-- ---------------------------------------------------------------------------
-- Same shape, minus the discharge set: a KWSE library is built at the ND
-- library's discharges, so the upstream reach reads those from the ND row.
-- Its upstream-end values are still needed, because the floor curve the reach
-- above consumes is the minimum across BOTH of this reach's tables — its KWSE
-- runs at low stages sit below its normal-depth run.
CREATE TABLE IF NOT EXISTS materialized_kwse_runs(
    reach_id bigint PRIMARY KEY REFERENCES reach_network(reach_id) ON DELETE CASCADE,
    model_identity_hash char(8) NOT NULL CONSTRAINT materialized_kwse_runs_model_hash_chk CHECK (model_identity_hash ~ '^[0-9a-f]{8}$'),
    run_identity_hash char(8) NOT NULL CONSTRAINT materialized_kwse_runs_run_hash_chk CHECK (run_identity_hash ~ '^[0-9a-f]{8}$'),
    us_wse_max double precision NOT NULL,
    us_min_wse_curve jsonb NOT NULL CONSTRAINT materialized_kwse_runs_curve_chk CHECK (jsonb_typeof(us_min_wse_curve) = 'array'),
    applied_revision integer NOT NULL,
    confirmed_at timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE materialized_kwse_runs IS 'Proof that a reach KWSE intent is materialized. No discharge set: the libraries sit at the ND library discharges.';

COMMENT ON COLUMN materialized_kwse_runs.us_min_wse_curve IS 'Minimum WSE at this reach upstream end per discharge across its stage libraries. The upstream reach floor is the minimum of this and the ND curve, since low-stage KWSE runs sit below the normal-depth run.';
