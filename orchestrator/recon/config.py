from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv
from pydantic import computed_field
from pydantic_settings import BaseSettings

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


class Settings(BaseSettings):
    postgres_user: str
    postgres_password: str
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "twodfim"
    artifacts_s3_bucket: str = "twod-fim-artifacts"
    # The storage generation every artifact path starts with, written exactly as
    # it appears after `version=`: TWOD_FIM_VERSION=2026.09 is `version=2026.09/`.
    # It is only an address, so a new value is a new, empty storage area.
    twod_fim_version: str
    aws_endpoint_url: str | None = None
    # Two MODEL IDENTITY inputs, authored into desired_state_defaults by
    # `author_intent.py defaults`. Identity is hashed from them, so changing either gives
    # every reach a new identity_hash, a new model_id, and a new address — the
    # whole corpus rebuilds and every result filed under an old model_id is
    # orphaned. That is why they are settings rather than literals buried in an
    # INSERT: the blast radius should be visible where the value is set.
    #
    # Editing them here changes nothing on its own. desired_state_defaults holds
    # what is actually in force until `author_intent.py defaults --yes` writes the
    # change, and that is what fires bump_all_reach_revisions and starts the rebuild.
    #
    # Horizontal resolution the DEM and roughness are resampled to, in the units
    # of epsg_code — metres for 5070.
    grid_resolution: float = 30
    # CRS for every georeferenced artifact. 5070 is CONUS Albers, metres, which
    # is what the schema stores geometry in and what model outputs are compared
    # in.
    epsg_code: int = 5070
    # ------------------------------------------------------------------
    # The rest of desired_state_defaults: what every reach falls back to.
    #
    # System wide, like the two above: `author_intent.py defaults` writes the
    # defaults row from these settings, never an AOI command, so the defaults
    # belong to the deployment rather than to any AOI. An AOI config overrides the three
    # sources for its own reaches only. The same warning applies — every value
    # here feeds what the reconciler predicts identity from.
    # ------------------------------------------------------------------
    # Methodology version pin. Must match what the deployed job images bake in
    # (twod_fim_jobs), or nothing the loop builds will be found where it looked.
    sdr_commit: str = "826a602ddcaf58bf4081dc04b65ba15b82cc8c8a"
    solver: str = "lisflood"
    # Where a job reads elevation and land cover. Addresses a job can open,
    # hashed exactly as written. `{source_data}` is filled in when authored.
    dem_source: str = "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/TIFF/USGS_Seamless_DEM_13.vrt"
    lulc_source: str = "{source_data}/lulc/LC_2023_CU_C1V0.tif"
    # The land-cover to Manning's n mapping: an s3:// JSON file in storage,
    # because the loop reads it to predict identity (which hashes the content,
    # not this address).
    lulc_lookup: str = "{source_data}/lulc_lookups/default_nlcd_mannings_n_v0.json"
    # Where discharge bounds come from when an AOI config names no flow statistics
    # of its own: bound_flows.py's CONUS output, staged as source data, keyed
    # by NHF flowpath id. modify_network keeps the downstream reach's id when it
    # merges reaches, so a network's reach_id matches it directly.
    #
    # Any table works — an AOI config can point at its own file with its own column
    # names; these name what the default file calls them. DR-029 ALT-D takes
    # the bounds from the high flow threshold and the 100-year discharge.
    flow_statistics: str = "{source_data}/flows/nhf_aep_flows.parquet"
    flow_reach_id_column: str = "reach_id"
    flow_q_lower_column: str = "high_flow_threshold"
    flow_q_upper_column: str = "f100year"
    # The columns of the same table f2f.py forecasts with, one set of flows2fim
    # controls and one depth VRT each. Not intent: nothing the loop builds
    # depends on them. As an environment variable, a JSON list.
    flow_aep_columns: list[str] = ["f5year", "f50year", "f100year"]
    # Stage increment for the KWSE libraries, metres. DR-033 ALT-B allows only
    # {0.25, 0.5, 1, 2, 5} and a CHECK constraint enforces it. Nothing else
    # supplies it, and NULL would leave every non-terminal reach awaiting_inputs
    # with no stage library ever planned.
    ld_ds_z_delta: float = 2.0
    # Library resolution (DR-030): the acceptance RANGE of each criterion, over
    # wet cells only, for the increase between consecutive library discharges.
    # Authored but not yet wired — nothing sends these to a job or checks them.
    ld_q_max_depth_increase_range: str = "[1.5,2.5]"  # m
    ld_q_median_depth_increase_range: str = "[0.75,1.5]"  # m
    ld_q_flooded_area_prcnt_increase_range: str = "[10,30]"  # percent
    # Nothing here says which IMAGE runs a job, on what hardware, with which
    # environment or mounts. That belongs to the SEPEX process definition,
    # wherever this deployment's SEPEX reads it from, and the loop never sees
    # it: the loop names a process and hands over a payload. Adding an image
    # setting back here would create a second place to be wrong about the same
    # fact.
    #
    # When a normal-depth run is considered steady and may stop early: the
    # volume change over a save interval, normalized by inflow. DR-022 selects
    # volume convergence as the termination metric and DR-028 sets it to 1e-3.
    #
    # Sent explicitly because the job's own default is 0, and its convergence
    # test is `volume_convergence < tolerance` — a comparison nothing can
    # satisfy, so an unsent tolerance means every scenario runs the full
    # simulation length instead of stopping when the reach settles.
    volume_convergence_tolerance: float = 1e-3
    # Failures in a row before a reach is parked for a person. 1 means no
    # retries at all, which is what you want while developing: a failure should
    # stop and be looked at, not be retried five times over an hour.
    halt_after_failures: int = 1
    # Whether a normal-depth run continues when water reaches an invalid domain
    # edge, rather than aborting the whole adaptive sweep. The job defaults to
    # False, which is the safe production choice: water leaving through an edge
    # it should not means the domain is too tight, and the results along that
    # edge are not trustworthy.
    #
    # True while developing, so a library forms and the loop can be exercised
    # end to end. Treat any library produced this way as provisional — the
    # inundation is bounded by the domain rather than by the terrain.
    allow_water_on_edges: bool = True
    # The execution layer. Required: the loop has no other way to run a job.
    # Needs a scheme — urllib rejects a bare host:port — and SEPEX's port, 5050.
    sepex_url: str
    # There is no job-side view of any service here. A job reads the reach
    # network from a file and its artifacts from storage, and gets the endpoint
    # for the latter from SEPEX's own process definition. Nothing the loop
    # builds has to be resolvable from inside a job container any more, which
    # is what removed the second hostname this class used to carry.

    @computed_field
    @property
    def pipeline_db_connection_string(self) -> str:
        """How the loop reaches the database. The loop's own, never handed out."""
        return (
            f"postgresql://{quote_plus(self.postgres_user)}:{quote_plus(self.postgres_password)}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
