"""S3-compatible storage.

Address construction and the read, write and listing helpers the loop uses.
"""

import logging

import boto3

from recon.config import settings

logger = logging.getLogger(__name__)


def get_s3_client():
    kwargs = {}
    if settings.aws_endpoint_url:
        kwargs["endpoint_url"] = settings.aws_endpoint_url
    return boto3.client("s3", **kwargs)


def parse_s3_path(path: str) -> tuple[str, str]:
    """Split 's3://bucket/prefix' into ('bucket', 'prefix')."""
    without_scheme = path.removeprefix("s3://")
    bucket, _, prefix = without_scheme.partition("/")
    return bucket, prefix.strip("/")


def twod_fim_data_root_prefix() -> str:
    """The storage area every artifact address starts with."""
    return settings.twod_fim_data_root_prefix


def model_base_path(reach_id: str) -> str:
    """Base S3 location for a reach's model artifacts."""
    return f"{twod_fim_data_root_prefix()}/models/reach={reach_id}"


def model_artifact_path(reach_id: str, model_id: str) -> str:
    """Full s3:// path to a reach's model_manifest.json."""
    return f"{model_base_path(reach_id)}/{model_id}/model_manifest.json"


MANIFEST_FILENAME = "model_manifest.json"
SCENARIO_MANIFEST_FILENAME = "scenario_manifest.json"
INUNDATED_AREA_FILENAME = "inundated_area.geojson"
STL_FILENAME = "stl.geojson"


def results_root() -> str:
    """The `model_results_base_path` the run jobs take."""
    return f"{twod_fim_data_root_prefix()}/results"


def model_identity_hash(model_id: str) -> str:
    """The identity half of a model_id, without the domain code."""
    return model_id.partition("_")[0]


def run_base_path(reach_id: str, model_id: str, run_identity_hash: str) -> str:
    """Everything one run identity produced for this reach, above the scenario folders."""
    return (
        f"{results_root()}/reach={reach_id}"
        f"/{model_identity_hash(model_id)}/{run_identity_hash}"
    )


def nd_library_path(reach_id: str, model_id: str, run_identity_hash: str) -> str | None:
    """The folder holding one normal-depth library: every q run at one slope."""
    base = run_base_path(reach_id, model_id, run_identity_hash)
    found = list_subfolders(base, prefix="nd=")
    if len(found) != 1:
        if found:
            logger.warning(
                "expected exactly one nd= folder under %s, found %s", base, found
            )
        return None
    return f"{base}/{found[0]}"


REACH_NETWORK_FILENAME = "reach_network.parquet"


def source_data_path(name: str) -> str:
    """External source data: staged by people, read by this system, never written by it."""
    return f"{settings.twod_fim_source_data_prefix}/{name}"


def workspace_path(name: str) -> str:
    """The system's working data: written by seeding, read by jobs."""
    return f"{twod_fim_data_root_prefix()}/workspace/{name}"


def reach_network_path() -> str:
    """The reach network as GeoParquet, which is what jobs read instead of the database."""
    return workspace_path(REACH_NETWORK_FILENAME)


def boundary_polygon_path(kind: str, feature_id: str) -> str:
    """Where a lake or coast outflow polygon is published by the seeder."""
    return workspace_path(f"{kind}s/{feature_id}.geojson")


def list_subfolders(path: str, prefix: str = "") -> list[str]:
    """Immediate child "folder" names under an s3:// prefix."""
    bucket, base = parse_s3_path(path)
    dir_prefix = base + "/" if base else ""
    s3 = get_s3_client()
    names = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(
        Bucket=bucket, Prefix=dir_prefix + prefix, Delimiter="/"
    ):
        for entry in page.get("CommonPrefixes", []):
            names.append(entry["Prefix"][len(dir_prefix) :].rstrip("/"))
    return names


def read_json(path: str) -> dict | None:
    """Read and parse a JSON object, or None if it is not there."""
    from botocore.exceptions import ClientError

    bucket, key = parse_s3_path(path)
    s3 = get_s3_client()
    try:
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
            return None
        raise
    import json

    return json.loads(body)


def scenario_manifest_path(
    reach_id: str, model_id: str, run_identity_hash: str, scenario_dir: str
) -> str:
    """The manifest of one scenario, given the folder its realization names."""
    return (
        f"{run_base_path(reach_id, model_id, run_identity_hash)}"
        f"/{scenario_dir}/{SCENARIO_MANIFEST_FILENAME}"
    )
