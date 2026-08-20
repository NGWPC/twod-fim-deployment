from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv
from pydantic import computed_field
from pydantic_settings import BaseSettings

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


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
    run_nd_scenarios_image: str = "ghcr.io/ngwpc/twod-fim-jobs/run_nd_scenarios-lisflood-gpu:dev"
    docker_network: str = "twodfim"
    docker_platform: str | None = None
    build_model_timeout: int = 3600
    # When a normal-depth run is considered steady and may stop early: the
    # volume change over a save interval, normalized by inflow. DR-022 selects
    # volume convergence as the termination metric and DR-028 sets it to 1e-3.
    #
    # Sent explicitly because the job's own default is 0, and its convergence
    # test is `volume_convergence < tolerance` — a comparison nothing can
    # satisfy, so an unsent tolerance means every scenario runs the full
    # simulation length instead of stopping when the reach settles.
    volume_convergence_tolerance: float = 1e-3
    docker_data_dir: str | None = None
    lulc_source: str | None = None
    # Hostname the database answers to from inside a job container. Jobs run as
    # siblings on the compose network, where the host's "localhost" is their own
    # container, so they cannot use the same connection string the loop does.
    postgres_host_for_jobs: str = "db"
    # Same idea for object storage: jobs run as siblings on the compose network,
    # so the endpoint the loop uses (localhost) is not reachable from inside one.
    aws_endpoint_url_for_jobs: str = "http://minio:9000"

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
            f"@{self.postgres_host_for_jobs}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
