"""Identity derivation.

Predicts the addresses that intent implies, mirroring how the jobs repo derives
them.
"""

import hashlib
import json
import math
import re
from typing import Any, Mapping, Sequence

from shapely import wkb as shapely_wkb

HASH_ALGORITHM = "sha256"
HASH_LENGTH = 8

IDENTITY_KEYS = frozenset({
    "sdr_commit",
    "reach_geom_hash",
    "grid_resolution",
    "epsg_code",
    "dem_source_inputs_hash",
    "lulc_source_inputs_hash",
    "lulc_lookup_dict_hash",
})


def hash_str(s: str, role_length: int | None = HASH_LENGTH) -> str:
    """sha256 of a string, lowercased hex, truncated. Mirrors the job's hash_str."""
    digest = hashlib.new(HASH_ALGORITHM, s.encode()).hexdigest().lower()
    return digest[:role_length] if role_length else digest

def hash_dict(d: Mapping[Any, Any], role_length: int | None = HASH_LENGTH) -> str:
    """sha256 of canonical JSON (sorted keys, no whitespace). Mirrors hash_dict."""
    return hash_str(json.dumps(dict(d), sort_keys=True, separators=(",", ":")), role_length)

def reach_geom_hash(geom_wkb: bytes) -> str:
    """Hash of the reach geometry's WKT, via shapely."""
    return hash_str(shapely_wkb.loads(bytes(geom_wkb)).wkt)


def lulc_lookup_mapping(path: str) -> dict[int, float]:
    """The land-cover to roughness mapping a path resolves to."""
    from recon import storage

    doc = storage.read_json(path)
    if doc is None:
        raise RuntimeError(f"no land-cover lookup at {path}")
    return {int(k): float(v) for k, v in doc.items()}


def model_identity(intent: Mapping[str, Any]) -> tuple[dict, str]:
    """The identity object and hash this reach's effective intent implies."""
    lulc_lookup = lulc_lookup_mapping(intent["lulc_lookup"])
    identity = {
        "sdr_commit": intent["sdr_commit"],
        "reach_geom_hash": reach_geom_hash(intent["geom_wkb"]),
        "grid_resolution": float(intent["grid_resolution"]),
        "epsg_code": int(intent["epsg_code"]),
        "dem_source_inputs_hash": hash_str(intent["dem_source"]),
        "lulc_source_inputs_hash": hash_str(intent["lulc_source"]),
        "lulc_lookup_dict_hash": hash_dict(lulc_lookup),
    }
    return identity, hash_dict(identity)


def snap_bbox(bbox: Sequence[float], grid_resolution: float) -> list[float]:
    """An authored domain bbox snapped outward to the grid, as the job snaps one."""
    resolution = float(grid_resolution)
    xmin, ymin, xmax, ymax = (float(v) for v in bbox)
    return [
        math.floor(xmin / resolution) * resolution,
        math.floor(ymin / resolution) * resolution,
        math.ceil(xmax / resolution) * resolution,
        math.ceil(ymax / resolution) * resolution,
    ]


def domain_code(bbox: Sequence[float], geom_wkb: bytes, grid_resolution: float) -> str:
    """The realization code a snapped domain bbox (snap_bbox) implies for this reach."""
    resolution = float(grid_resolution)
    centroid = shapely_wkb.loads(bytes(geom_wkb)).centroid
    ax = math.floor(centroid.x / resolution) * resolution
    ay = math.floor(centroid.y / resolution) * resolution
    xmin, ymin, xmax, ymax = (float(v) for v in bbox)
    offsets = ((ymax - ay) / resolution, (ay - ymin) / resolution,
               (xmax - ax) / resolution, (ax - xmin) / resolution)
    return "N{}S{}E{}W{}".format(*[int(i) for i in offsets])


def verify_manifest(
    manifest: Mapping[str, Any],
    reach_id: str,
    model_id: str,
    authored_domain: Sequence[float] | None = None,
) -> list[str]:
    """Why this manifest should NOT be adopted; empty list means it is sound."""
    problems = []
    folder_hash, _, _ = model_id.partition("_")
    if authored_domain is not None:
        built = (manifest.get("domain") or {}).get("bbox")
        wanted = [float(v) for v in authored_domain]
        if not isinstance(built, list) or [float(v) for v in built] != wanted:
            problems.append(f"manifest domain bbox {built} != authored model_domain {wanted}")
    if manifest.get("reach_id") != reach_id:
        problems.append(f"manifest reach_id {manifest.get('reach_id')} != {reach_id}")
    claimed = manifest.get("identity_hash", "")
    if claimed != folder_hash:
        problems.append(f"manifest identity_hash {claimed} != folder {folder_hash}")
    if manifest.get("model_id") != model_id:
        problems.append(f"manifest model_id {manifest.get('model_id')} != folder {model_id}")
    ident = manifest.get("identity")
    if not isinstance(ident, dict):
        problems.append("manifest has no identity object")
        return problems
    keys = set(ident.keys())
    if keys != IDENTITY_KEYS:
        unknown, missing = keys - IDENTITY_KEYS, IDENTITY_KEYS - keys
        problems.append(f"identity keys differ: unknown={sorted(unknown)} missing={sorted(missing)}")
    elif hash_dict(ident) != claimed:
        problems.append(
            f"identity object hashes to {hash_dict(ident)}, manifest claims {claimed}: "
            "the hashing recipe here has drifted from the job's")
    return problems


RUN_IDENTITY_KEYS = frozenset({"sdr_commit_id", "solver"})


def run_identity(intent: Mapping[str, Any]) -> tuple[dict, str]:
    """The run identity object and hash this reach's effective intent implies."""
    identity = {
        "sdr_commit_id": intent["sdr_commit"],
        "solver": intent["solver"],
    }
    return identity, hash_dict(identity)


RUN_NAME_Q_ROUNDING_PRECISION = 0


def q_folder(q: int) -> str:
    """The `q=<discharge>` folder for one scenario."""
    return f"q={q:.{RUN_NAME_Q_ROUNDING_PRECISION}f}"


def parse_q_folder(name: str) -> int | None:
    """The discharge a `q=<value>` folder names, or None if it is not one."""
    if not name.startswith("q="):
        return None
    try:
        return int(float(name[2:]))
    except ValueError:
        return None


RUN_NAME_KWSE_ROUNDING_PRECISION = 1
RUN_NAME_SLOPE_ROUNDING_PRECISION = 1


def kwse_folder(z: float) -> str:
    """The `kwse=<stage>` folder for one KWSE scenario."""
    return f"kwse={z:.{RUN_NAME_KWSE_ROUNDING_PRECISION}f}"


def nd_folder(slope: float) -> str:
    """The `nd=<slope>` folder holding a reach's normal-depth library."""
    formatted = (
        f"{slope:.{RUN_NAME_SLOPE_ROUNDING_PRECISION}e}"
        .replace("-", "").replace("+", "").replace("e", "E")
    )
    return f"nd={formatted}"


def parse_nd_folder(name: str) -> float | None:
    """The slope an `nd=<value>` folder names, or None if it is not one."""
    if not name.startswith("nd="):
        return None
    try:
        return float(name[3:].replace("E", "e"))
    except ValueError:
        return None


_SCENARIO_CODE = re.compile(r"^(ND|KWSE)(.+?)Q(\d+)$")


def scenario_dir_from_code(code: str) -> str | None:
    """The `<nd|kwse>=<value>/q=<value>` directory a scenario code implies."""
    found = _SCENARIO_CODE.match(code or "")
    if not found:
        return None
    kind, ds_value, q_value = found.groups()
    return f"{kind.lower()}={ds_value}/q={q_value}"


def verify_scenario_manifest(
    manifest: Mapping[str, Any], reach_id: str, run_hash: str, model_id: str,
    scenario_dir: str,
) -> list[str]:
    """Why this scenario manifest should NOT be adopted; empty means sound."""
    problems = []
    if manifest.get("reach_id") != reach_id:
        problems.append(f"manifest reach_id {manifest.get('reach_id')} != {reach_id}")
    claimed = manifest.get("identity_hash", "")
    if claimed != run_hash:
        problems.append(f"manifest identity_hash {claimed} != folder {run_hash}")
    if manifest.get("model_id") != model_id:
        problems.append(f"manifest model_id {manifest.get('model_id')} != {model_id}")

    code = manifest.get("scenario_code")
    implied = scenario_dir_from_code(code) if code else None
    if implied != scenario_dir:
        problems.append(
            f"manifest scenario_code {code!r} implies {implied!r}, "
            f"but it sits in {scenario_dir!r}")

    ident = manifest.get("identity")
    if not isinstance(ident, dict):
        problems.append("manifest has no identity object")
        return problems
    keys = set(ident.keys())
    if keys != RUN_IDENTITY_KEYS:
        unknown, missing = keys - RUN_IDENTITY_KEYS, RUN_IDENTITY_KEYS - keys
        problems.append(f"identity keys differ: unknown={sorted(unknown)} missing={sorted(missing)}")
    elif not isinstance(ident.get("solver"), str):
        problems.append(f"solver must be a string, got {ident.get('solver')!r}")
    elif hash_dict(ident) != claimed:
        problems.append(
            f"identity object hashes to {hash_dict(ident)}, manifest claims {claimed}: "
            "the hashing recipe here has drifted from the job's")
    return problems
