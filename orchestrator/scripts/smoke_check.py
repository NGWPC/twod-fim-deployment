"""End-to-end smoke check for the orchestrator.

Loads a reach network from a GeoPackage, seeds the DB, waits for the
reconciliation loop to process eligible reaches, then verifies final DB
and storage state. Exits non-zero on failure.

Requirements:
  - docker compose services (db, minio) running
  - twod-fim-jobs:build_model container built
  - reconciliation_sensor ON (Dagster UI -> Automation)

Usage:
  uv run python scripts/smoke_check.py
  uv run python scripts/smoke_check.py --gpkg /path/to/network.gpkg
  uv run python scripts/smoke_check.py --seed-only

Source GeoPackage must have: reach_id, reach_to_id, total_da_sqkm,
stream_order, slope, geom (EPSG:5070).
"""

import argparse
import sys
import time
from pathlib import Path

from orchestrator.state_store import StateStore
from orchestrator.storage import model_artifact_path, object_exists
from seed import load_network, seed

DEFAULT_GPKG = Path(__file__).parent / "data" / "reach_network.gpkg"
POLL_INTERVAL_S = 30
TIMEOUT_S = 1800


def wait_for_reconcile(store: StateStore, expected: int) -> list[dict]:
    """Poll until expected reaches reconcile, or timeout."""
    deadline = time.monotonic() + TIMEOUT_S
    while True:
        states = store.get_reach_states()
        done = sum(1 for s in states if s["reconciled"])
        print(f"  {done}/{len(states)} reconciled")
        if done >= expected:
            return states
        if time.monotonic() > deadline:
            print(f"  TIMEOUT after {TIMEOUT_S}s")
            return states
        time.sleep(POLL_INTERVAL_S)


def verify(states: list[dict], headwater_ids: set[int]) -> list[str]:
    """Final DB + storage assertions. Returns failure messages (empty = pass)."""
    failures = []

    for s in states:
        reach_id = s["reach_id"]

        if reach_id in headwater_ids:
            if s["reconciled"]:
                print(f"  reach {reach_id}: headwater reconciled (upstream bug is fixed!)")
            else:
                print(f"  reach {reach_id}: headwater not reconciled (expected, twod-fim-jobs upstream query)")
            continue

        if not s["reconciled"]:
            failures.append(f"reach {reach_id}: not reconciled")
            continue
        if not s["model_id"]:
            failures.append(f"reach {reach_id}: missing model_id")
            continue
        path = model_artifact_path(reach_id, s["model_id"])
        if not object_exists(path):
            failures.append(f"reach {reach_id}: missing artifact {path}")

    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description="Orchestrator smoke check")
    parser.add_argument("--gpkg", type=Path, default=DEFAULT_GPKG, help="Path to reach network GeoPackage")
    parser.add_argument("--seed-only", action="store_true", help="Seed the DB and exit without waiting")
    args = parser.parse_args()

    if not args.gpkg.exists():
        sys.exit(f"GeoPackage not found: {args.gpkg}")

    print(f"Loading network from {args.gpkg}...")
    network = load_network(args.gpkg)
    total = len(network)
    headwater_ids = {r["reach_id"] for r in network if r["is_headwater"]}
    non_headwater = total - len(headwater_ids)

    print(f"  {total} reaches ({non_headwater} processable, {len(headwater_ids)} headwater)")
    for r in network:
        flags = []
        if r["is_terminal"]:
            flags.append("terminal")
        if r["is_headwater"]:
            flags.append("headwater")
        flag_str = f" ({', '.join(flags)})" if flags else ""
        print(f"    {r['reach_id']} -> {r['reach_to_id']}{flag_str}")

    store = StateStore()

    print(f"\nSeeding {total} reaches...")
    seed(store, network)
    print(f"Done. {total} reaches in reach_network and desired_state.")

    if args.seed_only:
        print("\n--seed-only: exiting without waiting for reconciliation.")
        return

    print(f"\nWaiting for reconciliation ({non_headwater}/{total} expected)...")
    print("  reconciliation_sensor must be ON in Dagster UI")
    states = wait_for_reconcile(store, non_headwater)

    print("\nVerifying DB + storage...")
    failures = verify(states, headwater_ids)

    if failures:
        print(f"\nFAILED - {len(failures)} issue(s):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)

    reconciled = sum(1 for s in states if s["reconciled"])
    print(f"\nOK - {reconciled}/{total} reaches reconciled, all artifacts present.")
    if reconciled < total:
        print(f"  ({total - reconciled} headwater reaches pending twod-fim-jobs upstream query fix)")


if __name__ == "__main__":
    main()
