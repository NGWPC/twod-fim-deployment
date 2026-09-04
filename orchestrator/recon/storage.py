"""S3-compatible storage utilities.

Used by orchestrator for artifact read/write/verify.
Points to MinIO locally (via AWS_ENDPOINT_URL), real S3 in production.
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


def results_root() -> str:
    """The `model_results_base_path` the run jobs take. Bare on purpose.

    The job builds the rest of the address itself — it appends
    `reach=<id>/<model_id>/<run_identity_hash>/<scenario point>/` — so anything
    added here is a segment written twice. Sending a per-reach prefix is what
    produced paths with `reach=` in them twice, at which point nothing the loop
    predicted could be found.

    Note the grain: results hang off the whole model_id, DOMAIN CODE INCLUDED,
    so a rebuild that moves the domain files its runs somewhere new. That is
    the job's choice and the loop follows it, but it is also the stricter and
    more honest of the two, because a run is only ever verified against the
    exact model_id currently materialized (identity.verify_scenario_manifest).
    Filing by identity alone kept older runs reachable while the verification
    refused them anyway.
    """
    return f"s3://{settings.artifacts_s3_bucket}/version=v{settings.major_version}/results"


def run_base_path(reach_id: int, model_id: str, run_identity_hash: str) -> str:
    """Everything one run identity produced for this reach, above the scenario folders.

    Not normal-depth specific. A run identity is the solver plus the methodology
    pin, so a reach's `nd=<slope>` and every `kwse=<stage>` folder are siblings
    under this one prefix.
    """
    return f"{results_root()}/reach={reach_id}/{model_id}/{run_identity_hash}"


def nd_library_path(
    reach_id: int, model_id: str, run_identity_hash: str
) -> str | None:
    """The folder holding one normal-depth library: every q run at one slope.

    Discovered, not predicted: the job computes the slope itself from the
    reach's own DEM (elevation drop over its own centerline), so nothing here
    can know it in advance the way it once did from an authored value. None
    when no library has appeared yet, or when the base holds anything other
    than exactly one nd=<slope> folder — more than one should not happen for
    a deterministic job and is logged rather than guessed at.
    """
    base = run_base_path(reach_id, model_id, run_identity_hash)
    found = list_subfolders(base, prefix="nd=")
    if len(found) != 1:
        if found:
            logger.warning("expected exactly one nd= folder under %s, found %s", base, found)
        return None
    return f"{base}/{found[0]}"


REACH_NETWORK_FILENAME = "reach_network.parquet"
LULC_FILENAME = "lulc.tif"


def reference_data_path(filename: str) -> str:
    """Something every job reads and no reach owns, published once per deployment.

    `reference_data/` rather than a reach folder, for the same reason lakes live
    under `shared/`: these describe the world the models are built in, not any
    one model's results.
    """
    return (f"s3://{settings.artifacts_s3_bucket}/version=v{settings.major_version}"
            f"/reference_data/{filename}")


def lulc_path() -> str:
    """The land-cover raster, as an address rather than a mounted file.

    In storage so that running a job needs no volume arranged for it. GDAL
    reads s3:// through /vsis3, but it does NOT read the boto3 endpoint
    variable — a job needs AWS_S3_ENDPOINT and friends in its environment,
    supplied by its SEPEX process definition.
    """
    return reference_data_path(LULC_FILENAME)


def reach_network_path() -> str:
    """The reach network as GeoParquet, which is what jobs read instead of the database.

    One file for the whole deployment, under `reference_data/` rather than any
    reach's folder: it describes the network, not a reach, and every job reads
    the same copy. Written by scripts/seed.py, sorted by reach_id so a job can
    fetch one reach without scanning.
    """
    return reference_data_path(REACH_NETWORK_FILENAME)


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


def scenario_manifest_path(
    reach_id: int, model_id: str, run_identity_hash: str, scenario_dir: str
) -> str:
    """The manifest of one scenario, given the folder its realization names.

    `scenario_dir` is the `<nd=…|kwse=…>/q=…` pair, built by identity.py so that
    the rendering of a boundary value lives in exactly one place.
    """
    return (f"{run_base_path(reach_id, model_id, run_identity_hash)}"
            f"/{scenario_dir}/{SCENARIO_MANIFEST_FILENAME}")
