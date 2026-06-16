-- 04_current_state.sql


CREATE TABLE IF NOT EXISTS current_state (
    reach_id        BIGINT PRIMARY KEY
        REFERENCES reach_network (reach_id) ON DELETE CASCADE,


    identity_hash   CHAR(8)
        CONSTRAINT current_state_identity_hash_chk
        CHECK (identity_hash IS NULL OR identity_hash ~ '^[0-9a-f]{8}$'),
    domain_code     TEXT
        CONSTRAINT current_state_domain_code_chk
        CHECK (domain_code IS NULL OR domain_code ~ '^N(0|[1-9][0-9]*)S(0|[1-9][0-9]*)E(0|[1-9][0-9]*)W(0|[1-9][0-9]*)$'),
    model_id        TEXT GENERATED ALWAYS AS (
                        CASE
                            WHEN identity_hash IS NULL OR domain_code IS NULL THEN NULL
                            ELSE identity_hash || '+' || domain_code
                        END
                    ) STORED,


    processing      BOOLEAN NOT NULL DEFAULT FALSE,

    build_model_version TEXT,

    applied_revision INTEGER NOT NULL DEFAULT -1,

    -- FK target that lets the runs ledger pin each reach to exactly ONE model
    -- identity (see 05_runs.sql). reach_id is already unique (PK); this names the
    -- exact (reach_id, identity_hash) key the ledger's composite FK references.
    CONSTRAINT current_state_reach_identity_uq UNIQUE (reach_id, identity_hash)
);



-- Find reaches currently being worked
CREATE INDEX IF NOT EXISTS current_state_processing_idx ON current_state (processing) WHERE processing;