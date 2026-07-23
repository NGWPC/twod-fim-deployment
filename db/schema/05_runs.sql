-- 05_runs.sql
CREATE TABLE IF NOT EXISTS runs(
    run_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    reach_id bigint NOT NULL,
    model_identity_hash char(8) NOT NULL CONSTRAINT runs_model_identity_hash_chk CHECK (model_identity_hash ~ '^[0-9a-f]{8}$'),
    domain_code text NOT NULL CONSTRAINT runs_domain_code_chk CHECK (domain_code ~ '^N(0|[1-9][0-9]*)S(0|[1-9][0-9]*)E(0|[1-9][0-9]*)W(0|[1-9][0-9]*)$'),
    run_identity_hash char(8) NOT NULL CONSTRAINT runs_run_identity_hash_chk CHECK (run_identity_hash ~ '^[0-9a-f]{8}$'),
    runner_version text,
    -- Realization / scenario point
    q_cms integer NOT NULL, -- discharge (whole-number cms)
    bc_type text NOT NULL -- downstream BC family
    CONSTRAINT runs_bc_type_chk CHECK (bc_type IN ('nd', 'kwse')),
    kwse double precision, -- known WSE; NULL for nd runs, meters
    -- Outputs
    run_uri text NOT NULL,
    us_wse double precision, -- nominal upstream wse at STL
    kwse_transfer_run_identity char(8) -- downstream run that supplied this BC (provenance)
    CONSTRAINT runs_transfer_hash_chk CHECK (kwse_transfer_run_identity IS NULL OR kwse_transfer_run_identity ~ '^[0-9a-f]{8}$'),
    created_at timestamptz NOT NULL DEFAULT NOW(), -- execution time
    -- nd runs carry no downstream stage; kwse runs must.
    CONSTRAINT runs_bc_kwse_chk CHECK ((bc_type = 'nd' AND kwse IS NULL) OR (bc_type = 'kwse' AND kwse IS NOT NULL)),
    CONSTRAINT runs_scenario_uq UNIQUE NULLS NOT DISTINCT (reach_id, run_identity_hash, q_cms, kwse),
    -- Ledger-level invariant: every run must match the reach's CURRENT model
    -- identity. Superseding a model (new identity_hash in current_state) requires
    -- clearing the old runs first, so the ledger never holds more than one model
    -- identity per reach. ON DELETE CASCADE removes a reach's runs when its
    -- current_state row is deleted; identity changes are NO ACTION (must clear runs).
    CONSTRAINT runs_reach_identity_fk FOREIGN KEY (reach_id, model_identity_hash) REFERENCES current_state(reach_id,
	identity_hash) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS runs_reach_model_identity_idx ON runs(reach_id, model_identity_hash);

CREATE INDEX IF NOT EXISTS runs_run_identity_hash_idx ON runs(run_identity_hash);

-- BC-provenance lookup during the upstream cascade.
CREATE INDEX IF NOT EXISTS runs_transfer_bc_idx ON runs(kwse_transfer_run_identity)
WHERE
    kwse_transfer_run_identity IS NOT NULL;

COMMENT ON TABLE runs IS 'Per run ledger: one row per realized scenario output. One model identity per reach allowed (runs_reach_identity_fk).';

COMMENT ON COLUMN runs.model_identity_hash IS 'Model identity this run was produced under.';

COMMENT ON COLUMN runs.domain_code IS 'Domain used by this run (provenance only); a domain expansion does not invalidate older, smaller-domain runs.';

COMMENT ON COLUMN runs.run_identity_hash IS 'hash(engine + engine version); group/rollback unit for results.';

COMMENT ON COLUMN runs.runner_version IS 'run_*_scenarios job version (tooling, not solver); provenance for selective rollback.';

COMMENT ON COLUMN runs.q_cms IS 'Discharge for this scenario point (cms).';

COMMENT ON COLUMN runs.bc_type IS 'Downstream BC: nd (normal depth, kwse_m NULL) or kwse (known WSE, kwse_m not null).';

COMMENT ON COLUMN runs.kwse IS 'Known downstream WSE applied to this run (m); NULL for nd runs.';

COMMENT ON COLUMN runs.run_uri IS 'location of the run output.';

COMMENT ON COLUMN runs.us_wse IS 'Nominal upstream WSE at this run STL.';

COMMENT ON COLUMN runs.kwse_transfer_run_identity IS 'Provenance: downstream run whose output supplied this run BC';

COMMENT ON CONSTRAINT runs_reach_identity_fk ON runs IS 'Pins the reach runs to one model identity at the ledger level';
