from pathlib import Path

from dotenv import load_dotenv
from pydantic import computed_field
from pydantic_settings import BaseSettings

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


class Settings(BaseSettings):
    pg_username: str
    pg_password: str
    pg_host: str
    pg_port: int = 5432
    pipeline_pg_db: str = "pipeline"
    store_root: str
    major_version: int = 1
    aws_endpoint_url: str | None = None

    @computed_field
    @property
    def pipeline_db_connection_string(self) -> str:
        return f"postgresql://{self.pg_username}:{self.pg_password}@{self.pg_host}:{self.pg_port}/{self.pipeline_pg_db}"


settings = Settings()
