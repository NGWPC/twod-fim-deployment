CREATE TABLE IF NOT EXISTS materialized_nd_runs(
    reach_id text PRIMARY KEY REFERENCES reach_network(reach_id) ON DELETE CASCADE,

    model_id text NOT NULL CONSTRAINT materialized_nd_runs_model_id_chk CHECK (model_id ~ '^[0-9a-f]{8}_N(0|[1-9][0-9]*)S(0|[1-9][0-9]*)E(0|[1-9][0-9]*)W(0|[1-9][0-9]*)$'),
    run_identity_hash char(8) NOT NULL CONSTRAINT materialized_nd_runs_run_hash_chk CHECK (run_identity_hash ~ '^[0-9a-f]{8}$'),

    q_set integer[] NOT NULL,

    us_wse_max double precision NOT NULL,
    us_min_wse_curve jsonb NOT NULL CONSTRAINT materialized_nd_runs_curve_chk CHECK (jsonb_typeof(us_min_wse_curve) = 'array'),

    applied_revision integer NOT NULL,
    confirmed_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS materialized_kwse_runs(
    reach_id text PRIMARY KEY REFERENCES reach_network(reach_id) ON DELETE CASCADE,

    model_id text NOT NULL CONSTRAINT materialized_kwse_runs_model_id_chk CHECK (model_id ~ '^[0-9a-f]{8}_N(0|[1-9][0-9]*)S(0|[1-9][0-9]*)E(0|[1-9][0-9]*)W(0|[1-9][0-9]*)$'),
    run_identity_hash char(8) NOT NULL CONSTRAINT materialized_kwse_runs_run_hash_chk CHECK (run_identity_hash ~ '^[0-9a-f]{8}$'),

    scenario_index jsonb NOT NULL CONSTRAINT materialized_kwse_runs_index_chk CHECK (jsonb_typeof(scenario_index) = 'array'),
    applied_revision integer NOT NULL,
    confirmed_at timestamptz NOT NULL DEFAULT now()
);
