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
