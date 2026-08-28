"""Predict a model's identity from effective intent.

Intent implies an identity; the identity implies an address; observe looks at
the address. This module is the first arrow. It is a copy of the hashing in
twod-fim-jobs (utils/hashing.py + the Identity model in models/build_model.py),
kept here so the loop can compute an address without launching a container —
the only per-reach input is the reach geometry, which the database already
holds.

A copy of a recipe can drift from the original, so every observation runs the
self-check in verify_manifest(): the manifest carries both the identity object
and the hash the job computed from it, and re-hashing must reproduce it. Drift
becomes a loud error on the first manifest seen, not a silent network-wide
rebuild. When a shared identity package exists, this file is what it replaces.

Two representation details are load-bearing, learned from the job's own types:

  grid_resolution is a FLOAT in the identity (pydantic field), so it must
  serialize as 10.0, never 10 — "10.0" and "10" hash differently.

  lulc_lookup is hashed with INT keys (dict[int, float]); jsonb returns string
  keys, and json.dumps sorts by the original key type before stringifying, so
  {"100": ..} and {100: ..} sort differently once codes pass two digits. Keys
  are coerced back to int before hashing.
"""

import hashlib
import json
from typing import Any, Mapping

from shapely import wkb as shapely_wkb

HASH_ALGORITHM = "sha256"
HASH_LENGTH = 8

# The identity object's exact key set (models/build_model.py::Identity). A
# manifest whose identity carries any other key is refused: it means the jobs
# repo added an identity dimension this copy does not know about, and adopting
# it would mean trusting a hash we cannot reproduce.
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
    """Hash of the reach geometry's WKT, via shapely.

    WKB -> shapely -> .wkt reproduces the bytes the job hashes, because the job
    reads the same geometry through geopandas, which also formats WKT with
    shapely. PostGIS ST_AsText formats differently and must not be used here.
    """
    return hash_str(shapely_wkb.loads(bytes(geom_wkb)).wkt)


def model_identity(intent: Mapping[str, Any]) -> tuple[dict, str]:
    """The identity object and hash this reach's effective intent implies.

    `intent` needs: sdr_commit, grid_resolution, epsg_code, dem_source,
    lulc_source, lulc_lookup (jsonb dict), geom_wkb. Field construction mirrors
    jobs/build_model.py line for line.
    """
    lulc_lookup = {int(k): float(v) for k, v in intent["lulc_lookup"].items()}
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


def verify_manifest(manifest: Mapping[str, Any], reach_id: int, folder_hash: str) -> list[str]:
    """Why this manifest should NOT be adopted; empty list means it is sound.

    Checks are about trust, not correctness of the model itself:
      - the manifest belongs to this reach and to the folder it sits in
      - its identity object hashes to the identity_hash it claims (self-check;
        this is what catches drift between this copy and the job's recipe)
      - its identity carries exactly the keys this copy knows
    """
    problems = []
    if manifest.get("reach_id") != reach_id:
        problems.append(f"manifest reach_id {manifest.get('reach_id')} != {reach_id}")
    claimed = manifest.get("identity_hash", "")
    if claimed != folder_hash:
        problems.append(f"manifest identity_hash {claimed} != folder {folder_hash}")
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


# ---------------------------------------------------------------------------
# Run identity
# ---------------------------------------------------------------------------
# A run's identity is the methodology pin plus the solver — and nothing about
# the reach. Every reach in the deployment therefore shares one run identity
# hash, which is worth knowing before it surprises you: it is the recipe the
# runs were produced by, not an address unique to anything.
#
# The reach-specific part of the address is the model identity hash above it in
# the path, and the scenario point below it.
RUN_IDENTITY_KEYS = frozenset({"sdr_commit_id", "solver"})


def run_identity(intent: Mapping[str, Any]) -> tuple[dict, str]:
    """The run identity object and hash this reach's effective intent implies.

    `intent` needs: sdr_commit, solver.

    solver is just the solver's name (e.g. "lisflood") — the job no longer
    takes a version as part of its identity; the solver binary that runs is
    whichever one is baked into the image the loop chose (by solver and
    hardware), the same way sdr_commit is baked in rather than passed. A
    disagreement between what desired_state names and what got deployed shows
    up as runs that never appear at the address the loop predicted, not as
    corruption.
    """
    identity = {
        "sdr_commit_id": intent["sdr_commit"],
        "solver": intent["solver"],
    }
    return identity, hash_dict(identity)


# ---------------------------------------------------------------------------
# The scenario point — a run's realization
# ---------------------------------------------------------------------------
# Mirrors make_scenario_dir_name/make_scenario_code in the jobs repo. Both
# halves of an ND scenario folder are EMERGENT now:
#
#   nd=<slope>  the job derives the slope itself from the reach's own DEM
#               (elevation drop over its own centerline), so the loop cannot
#               predict it and finds it by listing (storage.nd_library_path).
#   q=<value>   the adaptive step algorithm decides which discharges are
#               hydraulically distinct enough to keep, so the loop cannot
#               predict them either and reads them back. Only the two ends are
#               guaranteed, because the job always runs min and max.
#
# So an ND library is a listing down to the slope, and a listing below it.
RUN_NAME_Q_ROUNDING_PRECISION = 0


def q_folder(q: int) -> str:
    """The `q=<discharge>` folder for one scenario.

    Discharge is integral, so this is a rendering rather than a rounding and
    parse_q_folder recovers the value exactly. The format string keeps the
    job's own precision constant so the two sides cannot drift.
    """
    return f"q={q:.{RUN_NAME_Q_ROUNDING_PRECISION}f}"


def parse_q_folder(name: str) -> int | None:
    """The discharge a `q=<value>` folder names, or None if it is not one."""
    if not name.startswith("q="):
        return None
    try:
        return int(float(name[2:]))
    except ValueError:
        return None


def verify_scenario_manifest(
    manifest: Mapping[str, Any], reach_id: int, run_hash: str, model_id: str, q: int
) -> list[str]:
    """Why this scenario manifest should NOT be adopted; empty means sound.

    The same trust checks as verify_manifest, plus the two that only a run has:
    it was produced against the model intent currently asks for, and it sits in
    the folder its own discharge names.
    """
    problems = []
    if manifest.get("reach_id") != reach_id:
        problems.append(f"manifest reach_id {manifest.get('reach_id')} != {reach_id}")
    claimed = manifest.get("identity_hash", "")
    if claimed != run_hash:
        problems.append(f"manifest identity_hash {claimed} != folder {run_hash}")
    if manifest.get("model_id") != model_id:
        problems.append(f"manifest model_id {manifest.get('model_id')} != {model_id}")

    # Discharge is a whole number of cms everywhere: authored in integer
    # columns, stepped in whole cms, named into the folder, recorded in q_set.
    # So this is an exact comparison, and a manifest that does not match the
    # folder it sits in is either misfiled or was produced by a job that let a
    # fraction through. Both are defects, and both are refused here rather than
    # rounded away — rounding would adopt a library whose recorded discharges
    # are not the ones that were run.
    #
    # Read from the top of the manifest, not from `inputs`. `inputs` holds the
    # literal inputs, where the discharge appears as one of several boundary
    # conditions; this is the scenario's summary value, published alongside
    # scenario_code. Comparing against the folder name is the point — it is the
    # manifest's own claim about where it belongs.
    discharge = manifest.get("us_discharge")
    if discharge is None or discharge != q:
        problems.append(f"manifest us_discharge {discharge} is not in folder q={q}")

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
