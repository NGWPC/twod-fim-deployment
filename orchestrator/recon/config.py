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
    major_version: int = 1
    aws_endpoint_url: str | None = None
    build_model_image: str = "ghcr.io/ngwpc/twod-fim-jobs/build_model:dev"
    # One image per job: the job name is baked into each image's ENTRYPOINT, so
    # the runner picks an image by step rather than passing a command.
    run_nd_scenarios_image: str = (
        "ghcr.io/ngwpc/twod-fim-jobs/run_nd_scenarios-lisflood-gpu:dev"
    )
    docker_network: str = "twodfim"
    # Value for docker run --gpus, e.g. "all" or "device=0". None passes the
    # flag at all, which is right on a machine without a GPU: the run image is
    # CUDA-capable but does not require one.
    #
    # Needs the NVIDIA Container Toolkit on the host — `nvidia-smi` working
    # outside Docker is not enough, the daemon needs the runtime too. Check
    # with: docker info | grep -i runtimes
    docker_gpus: str | None = None
    docker_platform: str | None = None
    # When a normal-depth run is considered steady and may stop early: the
    # volume change over a save interval, normalized by inflow. DR-022 selects
    # volume convergence as the termination metric and DR-028 sets it to 1e-3.
    #
    # Sent explicitly because the job's own default is 0, and its convergence
    # test is `volume_convergence < tolerance` — a comparison nothing can
    # satisfy, so an unsent tolerance means every scenario runs the full
    # simulation length instead of stopping when the reach settles.
    volume_convergence_tolerance: float = 1e-3
    # How far build_model buffers the bounding box around the reach geometry,
    # in CRS units (m at EPSG:5070). The job's own default is 0, which leaves
    # domains too tight for water to stay inside at higher discharges.
    #
    # NOTE this is a REALIZATION input, not an identity one: changing it moves
    # the domain_code, never the identity hash. So a change here does not
    # invalidate existing models — observe matches on the identity prefix and
    # accepts whatever domain code it finds. Rebuild deliberately if you want a
    # new buffer applied to models that already exist.
    domain_buffer: float = 50.0
    # Normal-depth slope for the downstream boundary, used until the hydrofabric
    # supplies real slopes. Seeded into reach_network.slope, from where
    # intent.boundary_slope reads it, so build_model and run_nd_scenarios agree.
    default_ds_slope: float = 0.01
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
    sepex_url: str | None = None
    docker_data_dir: str | None = None
    # Hostname the database answers to from inside a job container. Jobs run as
    # siblings on the compose network, where the host's "localhost" is their own
    # container, so they cannot use the same connection string the loop does.
    postgres_host_for_jobs: str | None = None
    aws_endpoint_url_for_jobs: str | None = None

    @computed_field
    @property
    def effective_postgres_host_for_jobs(self) -> str:
        return self.postgres_host_for_jobs or self.postgres_host

    @computed_field
    @property
    def effective_aws_endpoint_url_for_jobs(self) -> str:
        return self.aws_endpoint_url_for_jobs or self.aws_endpoint_url or ""

    @computed_field
    @property
    def pipeline_db_connection_string(self) -> str:
        """How the loop reaches the database."""
        return (
            f"postgresql://{quote_plus(self.postgres_user)}:{quote_plus(self.postgres_password)}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field
    @property
    def job_db_connection_string(self) -> str:
        """How a job container reaches the database. Handed to jobs, never used here."""
        return (
            f"postgresql://{quote_plus(self.postgres_user)}:{quote_plus(self.postgres_password)}"
            f"@{self.effective_postgres_host_for_jobs}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
