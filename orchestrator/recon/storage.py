"""S3-compatible storage utilities.

Used by orchestrator for artifact read/write/verify.
Points to MinIO locally (via AWS_ENDPOINT_URL), real S3 in production.
"""

import boto3

from orchestrator.config import settings


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
