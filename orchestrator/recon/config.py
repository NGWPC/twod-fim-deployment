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
    docker_network: str = "twodfim"
    docker_platform: str | None = None
    build_model_timeout: int = 3600
    docker_data_dir: str | None = None
    lulc_source: str | None = None
    # Hostname the database answers to from inside a job container. Jobs run as
    # siblings on the compose network, where the host's "localhost" is their own
    # container, so they cannot use the same connection string the loop does.
    postgres_host_for_jobs: str = "db"

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
