-- 05_runs.sql


CREATE TABLE IF NOT EXISTS runs (
    run_id                   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    reach_id                 BIGINT NOT NULL,

    model_identity_hash      CHAR(8) NOT NULL
        CONSTRAINT runs_model_identity_hash_chk CHECK (model_identity_hash ~ '^[0-9a-f]{8}$'),

    domain_code              TEXT NOT NULL
        CONSTRAINT runs_domain_code_chk
        CHECK (domain_code ~ '^N(0|[1-9][0-9]*)S(0|[1-9][0-9]*)E(0|[1-9][0-9]*)W(0|[1-9][0-9]*)$'),

    run_identity_hash        CHAR(8) NOT NULL
        CONSTRAINT runs_run_identity_hash_chk CHECK (run_identity_hash ~ '^[0-9a-f]{8}$'),

    runner_version           TEXT,

    -- Realization / scenario point
    q_cms                    INTEGER NOT NULL,             -- discharge (whole-number cms)
    bc_type                  TEXT NOT NULL                 -- downstream BC family
        CONSTRAINT runs_bc_type_chk CHECK (bc_type IN ('nd', 'kwse')),
    kwse_m                   DOUBLE PRECISION,             -- known WSE; NULL for nd runs

    -- Outputs
    run_uri                  TEXT NOT NULL,                
    us_wse                  DOUBLE PRECISION,              -- nominal upstream wse at STL
    transfer_bc_from_run_hash CHAR(8)                      -- downstream run that supplied this BC (provenance)
        CONSTRAINT runs_transfer_hash_chk
        CHECK (transfer_bc_from_run_hash IS NULL OR transfer_bc_from_run_hash ~ '^[0-9a-f]{8}$'),

    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),  -- execution time

    -- nd runs carry no downstream stage; kwse runs must.
    CONSTRAINT runs_bc_kwse_chk CHECK (
        (bc_type = 'nd'   AND kwse_m IS NULL) OR
        (bc_type = 'kwse' AND kwse_m IS NOT NULL)
    ),


    CONSTRAINT runs_scenario_uq
        UNIQUE NULLS NOT DISTINCT (reach_id, run_identity_hash, q_cms, kwse_m),

    -- Ledger-level invariant: every run must match the reach's CURRENT model
    -- identity. Superseding a model (new identity_hash in current_state) requires
    -- clearing the old runs first, so the ledger never holds more than one model
    -- identity per reach. ON DELETE CASCADE removes a reach's runs when its
    -- current_state row is deleted; identity changes are NO ACTION (must clear runs).
    CONSTRAINT runs_reach_identity_fk
        FOREIGN KEY (reach_id, model_identity_hash)
        REFERENCES current_state (reach_id, identity_hash) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS runs_reach_model_identity_idx    ON runs (reach_id, model_identity_hash);
CREATE INDEX IF NOT EXISTS runs_run_identity_hash_idx ON runs (run_identity_hash);
-- BC-provenance lookup during the upstream cascade.
CREATE INDEX IF NOT EXISTS runs_transfer_bc_idx       ON runs (transfer_bc_from_run_hash)
    WHERE transfer_bc_from_run_hash IS NOT NULL;