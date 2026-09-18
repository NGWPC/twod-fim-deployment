CREATE TABLE IF NOT EXISTS desired_state_defaults(

    id smallint PRIMARY KEY DEFAULT 1 CONSTRAINT desired_state_defaults_singleton_chk CHECK (id = 1),

    sdr_commit text NOT NULL,
    grid_resolution double precision NOT NULL,
    epsg_code integer NOT NULL,
    dem_source text NOT NULL,
    lulc_source text NOT NULL,

    lulc_lookup text NOT NULL,

    solver text NOT NULL DEFAULT 'lisflood' CONSTRAINT desired_state_defaults_solver_chk CHECK (solver IN
	('lisflood', 'sfincs', 'triton')),

    ld_q_max_depth_increase_range numrange,
    ld_q_median_depth_increase_range numrange,
    ld_q_flooded_area_prcnt_increase_range numrange,
    ld_ds_z_delta double precision,
    kwse_upper_bound double precision,
    revision integer NOT NULL DEFAULT 0,
    CONSTRAINT desired_state_defaults_ld_ds_z_menu_chk CHECK (ld_ds_z_delta IS NULL OR ld_ds_z_delta IN (0.25,
	0.5, 1, 2, 5))
);

CREATE TABLE IF NOT EXISTS desired_state(
    reach_id text PRIMARY KEY REFERENCES reach_network(reach_id) ON DELETE CASCADE,

    q_lower_bound integer,
    q_upper_bound integer,
    initial_dq_step_for_nd integer,

    q_grid_resolution integer,
    solver text,
    CONSTRAINT desired_state_solver_chk CHECK (solver IS NULL OR solver IN ('lisflood', 'sfincs', 'triton')),

    grid_resolution double precision,
    epsg_code integer,
    dem_source text,
    lulc_source text,

    lulc_lookup text,

    model_domain double precision[],

    override_id bigint,

    ld_q_max_depth_increase_range numrange,
    ld_q_median_depth_increase_range numrange,
    ld_q_flooded_area_prcnt_increase_range numrange,
    ld_ds_z_delta double precision,
    q_set integer[],
    kwse_upper_bound double precision,
    revision integer NOT NULL DEFAULT 0,
    CONSTRAINT desired_state_flow_bounds_chk CHECK (q_lower_bound IS NULL OR q_upper_bound IS NULL OR q_lower_bound < q_upper_bound),
    CONSTRAINT desired_state_kwse_bounds_chk CHECK (kwse_upper_bound IS NULL OR kwse_upper_bound > 0),
    CONSTRAINT desired_state_ld_positive_chk CHECK ((ld_ds_z_delta IS NULL OR ld_ds_z_delta > 0) AND
	(ld_q_max_depth_increase_range IS NULL OR lower(ld_q_max_depth_increase_range) >= 0) AND
	(ld_q_median_depth_increase_range IS NULL OR lower(ld_q_median_depth_increase_range) >= 0) AND
	(ld_q_flooded_area_prcnt_increase_range IS NULL OR
	    lower(ld_q_flooded_area_prcnt_increase_range) >= 0)),

    CONSTRAINT desired_state_ld_ds_z_menu_chk CHECK (ld_ds_z_delta IS NULL OR ld_ds_z_delta IN (0.25, 0.5, 1,
	2, 5)),

    CONSTRAINT desired_state_q_grid_menu_chk CHECK (q_grid_resolution IS NULL
	OR q_grid_resolution IN (2, 5, 10, 50, 100)),

    CONSTRAINT desired_state_q_on_grid_chk CHECK (q_grid_resolution IS NULL OR (
	(q_lower_bound IS NULL OR q_lower_bound % q_grid_resolution = 0) AND
	(q_upper_bound IS NULL OR q_upper_bound % q_grid_resolution = 0) AND
	(initial_dq_step_for_nd IS NULL
	    OR initial_dq_step_for_nd % q_grid_resolution = 0))),
    CONSTRAINT desired_state_model_domain_bbox_chk CHECK (model_domain IS NULL OR (
	array_ndims(model_domain) = 1 AND array_lower(model_domain, 1) = 1
	AND cardinality(model_domain) = 4 AND array_position(model_domain, NULL) IS NULL
	AND model_domain[1] < model_domain[3] AND model_domain[2] < model_domain[4]))
);
