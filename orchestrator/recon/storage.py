"""S3-compatible storage utilities.

Used by orchestrator for artifact read/write/verify.
Points to MinIO locally (via AWS_ENDPOINT_URL), real S3 in production.
"""

import boto3

from recon.config import settings


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


def object_exists(path: str) -> bool:
    """Check if an S3 object exists at the given full s3:// path."""
    from botocore.exceptions import ClientError

    bucket, key = parse_s3_path(path)
    s3 = get_s3_client()
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "404":
            return False
        raise


def model_base_path(reach_id: int) -> str:
    """Base S3 location for a reach's model artifacts."""
    return f"s3://{settings.artifacts_s3_bucket}/version=v{settings.major_version}/models/reach={reach_id}"


def model_artifact_path(reach_id: int, model_id: str) -> str:
    """Full s3:// path to a reach's model_manifest.json."""
    return f"{model_base_path(reach_id)}/{model_id}/model_manifest.json"


MANIFEST_FILENAME = "model_manifest.json"
SCENARIO_MANIFEST_FILENAME = "scenario_manifest.json"
INUNDATED_AREA_FILENAME = "inundated_area.geojson"
STL_FILENAME = "stl.geojson"


def results_base_path(reach_id: int, model_identity_hash: str) -> str:
    """Where a reach's runs live, for one model recipe.

    Runs are filed under the model IDENTITY hash, not the model id — the domain
    code is the model's realization, and widening a domain must not orphan every
    run of that reach. This is the `model_results_base_path` the run jobs take,
    and they append `<run_identity_hash>/<scenario point>/` to it themselves.
    """
    return (f"s3://{settings.artifacts_s3_bucket}/version=v{settings.major_version}"
            f"/results/reach={reach_id}/{model_identity_hash}")


def nd_library_path(
    reach_id: int, model_identity_hash: str, run_identity_hash: str, ds_slope: float
) -> str:
    """The folder holding one normal-depth library: every q run at one slope."""
    from recon.identity import nd_scenario_prefix

    return (f"{results_base_path(reach_id, model_identity_hash)}"
            f"/{run_identity_hash}/{nd_scenario_prefix(ds_slope)}")


def boundary_polygon_path(kind: str, feature_id: str) -> str:
    """Where a lake or coast outflow polygon is published by the seeder.

    A terminal reach's normal-depth boundary is the water body it drains into,
    so the polygon is a property of that body and is shared by every reach
    ending in it — hence `shared/`, written once rather than per reach.
    """
    return (f"s3://{settings.artifacts_s3_bucket}/version=v{settings.major_version}"
            f"/shared/{kind}s/{feature_id}.geojson")


def list_subfolders(path: str, prefix: str = "") -> list[str]:
    """Immediate child "folder" names under an s3:// prefix.

    `prefix` narrows to children whose name starts with it — with a predicted
    identity hash this makes observation a lookup at a known address rather
    than a scan of candidates.
    """
    bucket, base = parse_s3_path(path)
    dir_prefix = base + "/" if base else ""
    s3 = get_s3_client()
    names = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=dir_prefix + prefix, Delimiter="/"):
        for entry in page.get("CommonPrefixes", []):
            # Slice off the directory, not the narrowing prefix: callers get
            # the child's full name either way.
            names.append(entry["Prefix"][len(dir_prefix):].rstrip("/"))
    return names


def read_json(path: str) -> dict | None:
    """Read and parse a JSON object, or None if it is not there.

    None rather than an exception because "absent" is an ordinary answer to the
    loop — an absent manifest is how an incomplete build looks from outside.
    """
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
