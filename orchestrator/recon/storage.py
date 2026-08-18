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


def list_subfolders(path: str) -> list[str]:
    """Immediate child "folder" names under an s3:// prefix.

    One LIST rather than one HEAD per candidate, which is what keeps observing a
    reach cheap enough to do on every check.
    """
    bucket, prefix = parse_s3_path(path)
    prefix = prefix + "/" if prefix else ""
    s3 = get_s3_client()
    names = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix, Delimiter="/"):
        for entry in page.get("CommonPrefixes", []):
            names.append(entry["Prefix"][len(prefix):].rstrip("/"))
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
