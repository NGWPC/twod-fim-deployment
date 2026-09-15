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
    """The `model_results_base_path` the run jobs take. Bare on purpose.

    The job builds the rest of the address itself — it appends
    `reach=<id>/<model identity hash>/<run_identity_hash>/<scenario point>/` —
    so anything added here is a segment written twice. Sending a per-reach
    prefix is what produced paths with `reach=` in them twice, at which point
    nothing the loop predicted could be found.

    Note the grain: results hang off the model's IDENTITY hash, without the
    domain code, per system-design/guide.md — "runs file under identity, not
    under id which will have domain code". The domain is a realization, so
    widening it must not strand every run the reach already has.

    One coupling this does NOT fix on its own: verify_scenario_manifest still
    refuses a manifest whose model_id is not the one currently materialized. So
    runs from a previous domain sit in the right folder and are still refused.
    Until that check compares identity halves, a reach that changes domain has
    old and new runs mixed in one folder, and the old ones fail its library.
    """
    return f"{twod_fim_data_root_prefix()}/results"


def model_identity_hash(model_id: str) -> str:
    """The identity half of a model_id, without the domain code.

    model_id is `<identity_hash>_<domain_code>`. Callers hold whole model_ids —
    that is what materialized_models records — so the split happens here rather
    than at each of them, and mirrors RunScenarioInputs.model_identity_hash in
    the jobs repo, which is what actually names the folder.
    """
    return model_id.partition("_")[0]


def run_base_path(reach_id: str, model_id: str, run_identity_hash: str) -> str:
    """Everything one run identity produced for this reach, above the scenario folders.

    Not normal-depth specific. A run identity is the solver plus the methodology
    pin, so a reach's `nd=<slope>` and every `kwse=<stage>` folder are siblings
    under this one prefix.

    Takes a whole model_id and uses only its identity half: see results_root()
    for why the domain code is not in this address.
    """
    return (
        f"{results_root()}/reach={reach_id}"
        f"/{model_identity_hash(model_id)}/{run_identity_hash}"
    )


def nd_library_path(reach_id: str, model_id: str, run_identity_hash: str) -> str | None:
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
            logger.warning(
                "expected exactly one nd= folder under %s, found %s", base, found
            )
        return None
    return f"{base}/{found[0]}"


REACH_NETWORK_FILENAME = "reach_network.parquet"


def source_data_path(name: str) -> str:
    """External source data: staged by people, read by this system, never written by it.

    Any number of variants can sit side by side — a CONUS or regional
    hydrofabric, coastal influence polygons, several DEMs, land-cover rasters
    and their lookups, flow statistics. An AOI config says which ones an AOI
    uses; new data is added beside the old rather than replacing it.

    Under its own root, outside every storage area: source data has nothing to
    do with versioning, and every storage area reads the same copy.
    """
    return f"{settings.twod_fim_source_data_prefix}/{name}"


def workspace_path(name: str) -> str:
    """The system's working data: written by seeding, read by jobs.

    The reach network, lake and coast polygons. Rewritten
    whenever its source is seeded again — but not scratch space: jobs read these
    files, so removing one breaks work in flight.
    """
    return f"{twod_fim_data_root_prefix()}/workspace/{name}"


def reach_network_path() -> str:
    """The reach network as GeoParquet, which is what jobs read instead of the database.

    One file for the whole deployment, under `workspace/` rather than any reach's
    folder: it describes the network, not a reach, and every job reads the same
    copy. Written by `seed.py network` from the database, sorted by reach_id so
    a job can fetch one reach without scanning.
    """
    return workspace_path(REACH_NETWORK_FILENAME)


def boundary_polygon_path(kind: str, feature_id: str) -> str:
    """Where a lake or coast outflow polygon is published by the seeder.

    A terminal reach's normal-depth boundary is the water body it drains into,
    so the polygon is a property of that body and is shared by every reach
    ending in it — hence `workspace/`, written once rather than per reach.
    """
    return workspace_path(f"{kind}s/{feature_id}.geojson")


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
    for page in paginator.paginate(
        Bucket=bucket, Prefix=dir_prefix + prefix, Delimiter="/"
    ):
        for entry in page.get("CommonPrefixes", []):
            # Slice off the directory, not the narrowing prefix: callers get
            # the child's full name either way.
            names.append(entry["Prefix"][len(dir_prefix) :].rstrip("/"))
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
    reach_id: str, model_id: str, run_identity_hash: str, scenario_dir: str
) -> str:
    """The manifest of one scenario, given the folder its realization names.

    `scenario_dir` is the `<nd=…|kwse=…>/q=…` pair, built by identity.py so that
    the rendering of a boundary value lives in exactly one place.
    """
    return (
        f"{run_base_path(reach_id, model_id, run_identity_hash)}"
        f"/{scenario_dir}/{SCENARIO_MANIFEST_FILENAME}"
    )
