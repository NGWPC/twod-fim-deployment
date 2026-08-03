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
    build_model_image: str = "twod-fim-jobs:build_model"
    docker_network: str = "twodfim"
    docker_platform: str | None = None
    build_model_timeout: int = 3600
    docker_data_dir: str | None = None
    lulc_source: str | None = None

    @computed_field
    @property
    def pipeline_db_connection_string(self) -> str:
        return (
            f"postgresql://{quote_plus(self.postgres_user)}:{quote_plus(self.postgres_password)}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
