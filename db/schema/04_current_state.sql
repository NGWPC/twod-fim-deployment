-- 04_current_state.sql
-- What exists, as last confirmed against storage. S3 is the reality; this table
-- is only the latest snapshot of it, so every row here must have been observed
-- in storage first (reconciliation-loop.md, rule 3). Deleting from storage is a
-- supported action: the storage scanner removes the row, and the next check
-- rebuilds whatever is still desired.
CREATE TABLE IF NOT EXISTS current_state(
    reach_id bigint PRIMARY KEY REFERENCES reach_network(reach_id) ON DELETE CASCADE,
    identity_hash char(8) CONSTRAINT current_state_identity_hash_chk CHECK (identity_hash IS NULL OR identity_hash ~ '^[0-9a-f]{8}$'),
    domain_code text CONSTRAINT current_state_domain_code_chk CHECK (domain_code IS NULL OR domain_code ~ '^N(0|[1-9][0-9]*)S(0|[1-9][0-9]*)E(0|[1-9][0-9]*)W(0|[1-9][0-9]*)$'),
    -- Separator is '_', matching the model folder name in storage
    -- (guide.md: 5f14368c_N350S296E449W355).
    model_id text GENERATED ALWAYS AS ( CASE WHEN identity_hash IS NULL OR domain_code IS NULL THEN
        NULL
    ELSE
        identity_hash || '_' || domain_code
    END) STORED,
    build_model_version text,
    -- When storage last confirmed this row. The scanner stamps it on every pass,
    -- whether or not anything changed, so a stale snapshot is visible as an old
    -- timestamp rather than being indistinguishable from a fresh one.
    confirmed_at timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE current_state IS 'What exists for each reach, confirmed against storage. Rebuildable from storage alone; work tracking lives in reach_processing.';

COMMENT ON COLUMN current_state.identity_hash IS 'Model identity (8-hex) confirmed present in storage; stable across domain changes (results group under it). NULL = no model exists.';

COMMENT ON COLUMN current_state.domain_code IS 'Realized domain as grid-snapped N/S/E/W offset code.';

COMMENT ON COLUMN current_state.model_id IS 'Generated identity_hash_domain_code (also the name of the model folder).';

COMMENT ON COLUMN current_state.build_model_version IS 'build_model job version that produced the model; provenance for selective rollback (not part of identity_hash).';

COMMENT ON COLUMN current_state.confirmed_at IS 'Last time storage confirmed this row.';
