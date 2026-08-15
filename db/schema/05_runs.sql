-- 05_runs.sql
-- The ledger: one row per realized scenario output confirmed in storage.
-- Like current_state (04) this is a snapshot of S3, not a work log — what the
-- system is *doing* lives in reach_processing (07).
--
-- Naming note: guide.md's `run_id` (= run identity hash + scenario code, e.g.
-- af1436r4_ND1.2e5Q200) is derivable from run_identity_hash + the realization
-- columns below and is already spelled out by run_uri, so it is not stored.
-- run_row_id is a surrogate key only; it exists so one run can point at another.
CREATE TABLE IF NOT EXISTS runs(
    run_row_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    reach_id bigint NOT NULL REFERENCES reach_network(reach_id) ON DELETE CASCADE,
    -- Model identity these results live under in storage
    -- (results/reach=/<model identity>/<run identity>/<scenario>).
    model_identity_hash char(8) NOT NULL CONSTRAINT runs_model_identity_hash_chk CHECK (model_identity_hash ~ '^[0-9a-f]{8}$'),
    domain_code text NOT NULL CONSTRAINT runs_domain_code_chk CHECK (domain_code ~ '^N(0|[1-9][0-9]*)S(0|[1-9][0-9]*)E(0|[1-9][0-9]*)W(0|[1-9][0-9]*)$'),
    run_identity_hash char(8) NOT NULL CONSTRAINT runs_run_identity_hash_chk CHECK (run_identity_hash ~ '^[0-9a-f]{8}$'),
    -- Effective solver, recorded plainly because run_identity_hash is one-way.
    solver text CONSTRAINT runs_solver_chk CHECK (solver IS NULL OR solver IN ('lisflood', 'sfincs', 'triton')),
    runner_version text,
    -- Realization / scenario point
    q_cms integer NOT NULL, -- discharge (whole-number cms)
    bc_type text NOT NULL -- downstream BC family
    CONSTRAINT runs_bc_type_chk CHECK (bc_type IN ('nd', 'kwse')),
    kwse double precision, -- known WSE; NULL for nd runs, meters
    -- Outputs
    run_uri text NOT NULL,
    us_wse double precision, -- nominal upstream wse at STL
    -- Provenance: the exact downstream run whose output supplied this run's BC.
    -- This is what makes staleness derivable instead of propagated: when the
    -- storage scanner removes the downstream run, this pointer goes NULL and the
    -- upstream run is, by definition, stale (see stale_kwse_runs in 08_views).
    kwse_transfer_run_row_id bigint REFERENCES runs(run_row_id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT NOW(), -- execution time
    -- nd runs carry no downstream stage; kwse runs must.
    CONSTRAINT runs_bc_kwse_chk CHECK ((bc_type = 'nd' AND kwse IS NULL) OR (bc_type = 'kwse' AND kwse IS NOT NULL)),
    -- Only a kwse run can have a transferred BC.
    CONSTRAINT runs_bc_transfer_chk CHECK (bc_type = 'kwse' OR kwse_transfer_run_row_id IS NULL),
    -- One row per addressed path. model_identity_hash is part of the key because
    -- it is part of the path: the same scenario under a different model identity
    -- is a different output, not a conflicting one.
    CONSTRAINT runs_scenario_uq UNIQUE NULLS NOT DISTINCT (reach_id, model_identity_hash, run_identity_hash, q_cms, kwse)
);

CREATE INDEX IF NOT EXISTS runs_reach_model_identity_idx ON runs(reach_id, model_identity_hash);

CREATE INDEX IF NOT EXISTS runs_run_identity_hash_idx ON runs(run_identity_hash);

-- "Which runs upstream consumed this run" — the staleness lookup.
CREATE INDEX IF NOT EXISTS runs_transfer_bc_idx ON runs(kwse_transfer_run_row_id)
WHERE
    kwse_transfer_run_row_id IS NOT NULL;

COMMENT ON TABLE runs IS 'Per run ledger: one row per realized scenario output confirmed in storage. Addressed by (reach, model identity, run identity, scenario point).';

COMMENT ON COLUMN runs.run_row_id IS 'Surrogate key. NOT guide.md''s run_id (identity hash + scenario code), which is derivable and lives in run_uri.';

COMMENT ON COLUMN runs.model_identity_hash IS 'Model identity this run was produced under; also the storage folder results group by.';

COMMENT ON COLUMN runs.domain_code IS 'Domain used by this run (provenance only); a domain expansion does not invalidate older, smaller-domain runs.';

COMMENT ON COLUMN runs.run_identity_hash IS 'hash(sdr_commit + engine + engine version); group/rollback unit for results.';

COMMENT ON COLUMN runs.solver IS 'Effective engine used; provenance, since run_identity_hash cannot be read back.';

COMMENT ON COLUMN runs.runner_version IS 'run_*_scenarios job version (tooling, not solver); provenance for selective rollback.';

COMMENT ON COLUMN runs.q_cms IS 'Discharge for this scenario point (cms).';

COMMENT ON COLUMN runs.bc_type IS 'Downstream BC: nd (normal depth, kwse NULL) or kwse (known WSE, kwse not null).';

COMMENT ON COLUMN runs.kwse IS 'Known downstream WSE applied to this run (m); NULL for nd runs.';

COMMENT ON COLUMN runs.run_uri IS 'location of the run output.';

COMMENT ON COLUMN runs.us_wse IS 'Nominal upstream WSE at this run STL.';

COMMENT ON COLUMN runs.kwse_transfer_run_row_id IS 'Downstream run that supplied this run''s BC. NULL on a kwse run means that source is gone, i.e. this run is stale.';
