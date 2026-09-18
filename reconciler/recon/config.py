"""Configuration.

Settings read from the environment, with their defaults.
"""

from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv
from pydantic import computed_field, field_validator
from pydantic_settings import BaseSettings

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


class Settings(BaseSettings):
    postgres_user: str
    postgres_password: str
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "twodfim"
    twod_fim_data_root_prefix: str
    twod_fim_source_data_prefix: str
    aws_endpoint_url: str | None = None

    @field_validator("twod_fim_data_root_prefix", "twod_fim_source_data_prefix")
    @classmethod
    def _s3_root(cls, value: str) -> str:
        if not value.startswith("s3://") or not value.removeprefix("s3://").strip("/"):
            raise ValueError(f"must be an s3:// address, not {value!r}")
        return value.rstrip("/")

    grid_resolution: float = 30
    epsg_code: int = 5070
    sdr_commit: str = "826a602ddcaf58bf4081dc04b65ba15b82cc8c8a"
    solver: str = "lisflood"
    dem_source: str = "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/TIFF/USGS_Seamless_DEM_13.vrt"
    lulc_source: str = "{source_data}/lulc/LC_2023_CU_C1V0.tif"
    lulc_lookup: str = "{source_data}/lulc_lookups/default_nlcd_mannings_n_v0.json"
    flow_statistics: str = "{source_data}/flows/nhf_v1.2.3_aep_flows.parquet"
    flow_reach_id_column: str = "reach_id"
    flow_q_lower_column: str = "high_flow_threshold"
    flow_q_upper_column: str = "f100year"
    flow_aep_columns: list[str] = ["f5year", "f50year", "f100year"]
    ld_ds_z_delta: float = 2.0
    ld_q_max_depth_increase_range: str = "[1.5,2.5]"
    ld_q_median_depth_increase_range: str = "[0.75,1.5]"
    ld_q_flooded_area_prcnt_increase_range: str = "[10,30]"
    volume_convergence_tolerance: float = 1e-3
    halt_after_failures: int = 1
    allow_water_on_edges: bool = True
    sepex_url: str

    @computed_field
    @property
    def pipeline_db_connection_string(self) -> str:
        """How the loop reaches the database. The loop's own, never handed out."""
        return (
            f"postgresql://{quote_plus(self.postgres_user)}:{quote_plus(self.postgres_password)}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
