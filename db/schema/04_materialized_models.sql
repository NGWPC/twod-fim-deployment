CREATE TABLE IF NOT EXISTS materialized_models(
    reach_id text PRIMARY KEY REFERENCES reach_network(reach_id) ON DELETE CASCADE,

    identity_hash char(8) NOT NULL CONSTRAINT materialized_models_identity_hash_chk CHECK (identity_hash ~ '^[0-9a-f]{8}$'),

    domain_code text NOT NULL CONSTRAINT materialized_models_domain_code_chk CHECK (domain_code ~ '^N(0|[1-9][0-9]*)S(0|[1-9][0-9]*)E(0|[1-9][0-9]*)W(0|[1-9][0-9]*)$'),

    model_id text GENERATED ALWAYS AS (identity_hash || '_' || domain_code) STORED,

    applied_revision integer NOT NULL,

    confirmed_at timestamptz NOT NULL DEFAULT now()
);
