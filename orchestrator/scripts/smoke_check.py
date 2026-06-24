"""End-to-end smoke check for the orchestrator.

Seeds the demo network, waits for the reconciliation loop to process every
reach, then verifies final DB and storage state. Exits non-zero on failure.
The `reconciliation_sensor` must be ON (Dagster UI -> Automation).

Network topology (5 levels, downstream-first):

                        1001 (terminal)
                       /              \
                   1002                1003
                  /    \              /    \
              1004    1005        1006    1007
             /   \     |         /   \   /   \
          1008  1009  1010    1011  1012 1013 1014
           |     |     |       |     |    |
         1015  1016  1017    1018  1019  1020 (headwaters)

Edges point downstream via reach_to_id. Terminal: reach_to_id = NULL.
"""

import sys
import time

from orchestrator.state_store import StateStore
from orchestrator.storage import model_artifact_path, object_exists

POLL_INTERVAL_S = 10
TIMEOUT_S = 600

NETWORK = [
    # (reach_id, reach_to_id, is_terminal, is_headwater)
    (1001, None, True, False),
    (1002, 1001, False, False),
    (1003, 1001, False, False),
    (1004, 1002, False, False),
    (1005, 1002, False, False),
    (1006, 1003, False, False),
    (1007, 1003, False, False),
    (1008, 1004, False, False),
    (1009, 1004, False, False),
    (1010, 1005, False, False),
    (1011, 1006, False, False),
    (1012, 1006, False, False),
    (1013, 1007, False, False),
    (1014, 1007, False, False),
    (1015, 1008, False, True),
    (1016, 1009, False, True),
    (1017, 1010, False, True),
    (1018, 1011, False, True),
    (1019, 1012, False, True),
    (1020, 1013, False, True),
]


def seed(store: StateStore) -> None:
    for reach_id, reach_to_id, is_terminal, is_headwater in NETWORK:
        store.insert_reach(reach_id, reach_to_id, is_terminal, is_headwater)
        store.upsert_desired(reach_id)


def wait_for_reconcile(store: StateStore, total: int) -> list[dict]:
    """Poll until every reach reconciles, or timeout."""
    deadline = time.monotonic() + TIMEOUT_S
    while True:
        states = store.get_reach_states()
        done = sum(1 for s in states if s["reconciled"])
        print(f"  {done}/{total} reconciled")
        if done == total:
            return states
        if time.monotonic() > deadline:
            print(f"  TIMEOUT after {TIMEOUT_S}s")
            return states
        time.sleep(POLL_INTERVAL_S)


def verify(states: list[dict]) -> list[str]:
    """Final DB + storage assertions. Returns failure messages (empty = pass)."""
    failures = []

    for s in states:
        reach_id = s["reach_id"]
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
    total = len(NETWORK)
    store = StateStore()

    print(f"Seeding {total} reaches...")
    seed(store)
    print(f"Done. {total} reaches in reach_network and desired_state.")

    print("Waiting for reconciliation (reconciliation_sensor must be ON)...")
    states = wait_for_reconcile(store, total)

    print("Verifying DB + storage...")
    failures = verify(states)

    if failures:
        print(f"\nFAILED — {len(failures)} issue(s):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)

    print(f"\nOK — {total} reaches reconciled, all artifacts present.")


if __name__ == "__main__":
    main()
