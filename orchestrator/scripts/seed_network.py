"""Seed the 20-reach demo network into reach_network + desired_state.

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

from orchestrator.state_store import StateStore

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


def seed():
    store = StateStore()

    print(f"Seeding {len(NETWORK)} reaches...")
    for reach_id, reach_to_id, is_terminal, is_headwater in NETWORK:
        store.insert_reach(reach_id, reach_to_id, is_terminal, is_headwater)
        store.upsert_desired(reach_id)

    print(f"Done. {len(NETWORK)} reaches in reach_network and desired_state.")


if __name__ == "__main__":
    seed()
