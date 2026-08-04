-- 04_current_state.sql
CREATE TABLE IF NOT EXISTS current_state(
    reach_id bigint PRIMARY KEY REFERENCES reach_network(reach_id) ON DELETE CASCADE,
    identity_hash char(8) CONSTRAINT current_state_identity_hash_chk CHECK (identity_hash IS NULL OR identity_hash ~ '^[0-9a-f]{8}$'),
    domain_code text CONSTRAINT current_state_domain_code_chk CHECK (domain_code IS NULL OR domain_code ~ '^N-?(0|[1-9][0-9]*)S-?(0|[1-9][0-9]*)E-?(0|[1-9][0-9]*)W-?(0|[1-9][0-9]*)$'),
    model_id text GENERATED ALWAYS AS ( CASE WHEN identity_hash IS NULL OR domain_code IS NULL THEN
        NULL
    ELSE
        identity_hash || '_' || domain_code
    END) STORED,
    processing boolean NOT NULL DEFAULT FALSE,
    build_model_version text,
    applied_revision integer NOT NULL DEFAULT -1,
    -- FK target that lets the runs ledger pin each reach to exactly ONE model
    -- identity (see 05_runs.sql). reach_id is already unique (PK); this names the
    -- exact (reach_id, identity_hash) key the ledger's composite FK references.
    CONSTRAINT current_state_reach_identity_uq UNIQUE (reach_id, identity_hash)
);

-- Find reaches currently being worked
CREATE INDEX IF NOT EXISTS current_state_processing_idx ON current_state(processing)
WHERE
    processing;

COMMENT ON TABLE current_state IS 'Observed effective state, one row per reach. No row, or applied_revision < desired_state.revision = a gap to reconcile.';

COMMENT ON COLUMN current_state.identity_hash IS 'Current model identity (8-hex); stable across domain changes (results group under it).';

COMMENT ON COLUMN current_state.domain_code IS 'Realized domain as grid-snapped N/S/E/W offset code.';

COMMENT ON COLUMN current_state.model_id IS 'Generated identity_hash_domain_code (also name of model folder).';

COMMENT ON COLUMN current_state.processing IS 'TRUE while the reconciliation loop has work in flight for this reach.';

COMMENT ON COLUMN current_state.build_model_version IS 'build_model job version that produced the current model; provenance for selective rollback (not part of identity_hash).';

COMMENT ON COLUMN current_state.applied_revision IS 'Highest desired_state.revision satisfied; default -1 = uninitialized (gap).';
