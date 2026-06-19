def bump_revision(store, reach_id, times=1):
    """Force revision bumps by updating q_upper_bound (triggers auto-increment).

    Encodes a schema contract: desired_state.revision is DB-owned via a
    BEFORE UPDATE trigger. The only way to bump it is to UPDATE a field.
    """
    for _ in range(times):
        with store._connect() as conn:
            cur = conn.execute(
                """UPDATE desired_state
                   SET q_upper_bound = q_upper_bound + 1
                   WHERE reach_id = %s""",
                (reach_id,),
            )
            assert cur.rowcount == 1, f"bump_revision: reach_id {reach_id} not in desired_state"
            conn.commit()
