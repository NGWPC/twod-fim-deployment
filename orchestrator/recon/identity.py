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
