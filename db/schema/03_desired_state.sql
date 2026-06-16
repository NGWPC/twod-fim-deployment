-- 03_desired_state.sql

CREATE TABLE IF NOT EXISTS desired_state (
    reach_id                    BIGINT PRIMARY KEY
        REFERENCES reach_network (reach_id) ON DELETE CASCADE,

    min_flow                    INTEGER,                 -- cms (whole-number flows only)
    max_flow                    INTEGER,                 -- cms (whole-number flows only)

    initial_dq_step_for_nd      INTEGER,                 -- cms 


    solver                      TEXT
        CONSTRAINT desired_state_solver_chk
        CHECK (solver IS NULL OR solver IN ('lisflood', 'sfincs', 'triton')),

    model_domain                geometry(Polygon, 5070),

    -- override system TBD
    override_id                 BIGINT,


    ld_q_mean_stage_delta       DOUBLE PRECISION,        -- m
    ld_q_max_stage_delta        DOUBLE PRECISION,        -- m
    ld_q_max_extent_prcnt_delta DOUBLE PRECISION,        -- percent
    ld_ds_z_delta               DOUBLE PRECISION,        -- m

    q_set                       INTEGER[],


    ds_min_kwse                 DOUBLE PRECISION,        -- m
    ds_max_kwse                 DOUBLE PRECISION,        -- m


    revision                    INTEGER NOT NULL DEFAULT 0, -- field autoincrement

    CONSTRAINT desired_state_flow_bounds_chk
        CHECK (min_flow IS NULL OR max_flow IS NULL OR min_flow < max_flow),
    CONSTRAINT desired_state_kwse_bounds_chk
        CHECK (ds_min_kwse IS NULL OR ds_max_kwse IS NULL OR ds_min_kwse <= ds_max_kwse),
    CONSTRAINT desired_state_ld_positive_chk
        CHECK (
            (ld_q_mean_stage_delta       IS NULL OR ld_q_mean_stage_delta       > 0) AND
            (ld_q_max_stage_delta        IS NULL OR ld_q_max_stage_delta        > 0) AND
            (ld_q_max_extent_prcnt_delta IS NULL OR ld_q_max_extent_prcnt_delta > 0) AND
            (ld_ds_z_delta               IS NULL OR ld_ds_z_delta               > 0)
        )
);
