"""Tests for state_store.py — eligibility queries and state transitions."""


class TestGetEligibleReaches:
    """Downstream-first traversal: a reach is eligible only when stale AND
    downstream is complete (or terminal)."""

    def test_terminal_reach_eligible_when_stale(self, store):
        store.insert_reach(1001, None, is_terminal=True)
        store.upsert_desired(1001, revision=1)

        eligible = store.get_eligible_reaches()
        assert len(eligible) == 1
        assert eligible[0]["reach_id"] == 1001

    def test_non_terminal_eligibility_depends_on_downstream(self, store):
        """Non-terminal blocked until downstream complete, then eligible."""
        store.insert_reach(1001, None, is_terminal=True)
        store.insert_reach(1002, 1001)
        store.upsert_desired(1001, revision=1)
        store.upsert_desired(1002, revision=1)

        eligible_ids = [r["reach_id"] for r in store.get_eligible_reaches()]
        assert 1002 not in eligible_ids

        store.update_current(1001, "hash+domain", "hash", "domain", applied_revision=1)

        eligible_ids = [r["reach_id"] for r in store.get_eligible_reaches()]
        assert 1002 in eligible_ids
        assert 1001 not in eligible_ids

    def test_processing_flag_excludes_reach(self, store):
        store.insert_reach(1001, None, is_terminal=True)
        store.upsert_desired(1001, revision=1)

        store.update_current(1001, "hash+domain", "hash", "domain", applied_revision=0)
        store.set_processing(1001, True)

        eligible = store.get_eligible_reaches()
        assert len(eligible) == 0

    def test_completed_reach_not_eligible(self, store):
        store.insert_reach(1001, None, is_terminal=True)
        store.upsert_desired(1001, revision=1)
        store.update_current(1001, "hash+domain", "hash", "domain", applied_revision=1)

        eligible = store.get_eligible_reaches()
        assert len(eligible) == 0

    def test_new_revision_makes_reach_stale_again(self, store):
        store.insert_reach(1001, None, is_terminal=True)
        store.upsert_desired(1001, revision=1)
        store.update_current(1001, "hash+domain", "hash", "domain", applied_revision=1)

        store.upsert_desired(1001, revision=2)

        eligible = store.get_eligible_reaches()
        assert len(eligible) == 1
        assert eligible[0]["revision"] == 2

    def test_three_level_chain_traversal_order(self, store):
        """1001 (terminal) → 1002 → 1003 (headwater).
        Only terminal eligible first, then level by level."""
        store.insert_reach(1001, None, is_terminal=True)
        store.insert_reach(1002, 1001)
        store.insert_reach(1003, 1002, is_headwater=True)
        store.upsert_desired(1001, revision=1)
        store.upsert_desired(1002, revision=1)
        store.upsert_desired(1003, revision=1)

        # Level 1: only terminal
        eligible = store.get_eligible_reaches()
        assert [r["reach_id"] for r in eligible] == [1001]

        # Complete 1001
        store.update_current(1001, "h1+d1", "h1", "d1", applied_revision=1)

        # Level 2: 1002 now eligible
        eligible = store.get_eligible_reaches()
        assert [r["reach_id"] for r in eligible] == [1002]

        # Complete 1002
        store.update_current(1002, "h2+d2", "h2", "d2", applied_revision=1)

        # Level 3: 1003 now eligible
        eligible = store.get_eligible_reaches()
        assert [r["reach_id"] for r in eligible] == [1003]

    def test_parallel_branches_eligible_simultaneously(self, store):
        """1001 (terminal) with two upstream: 1002, 1003.
        Both should be eligible after 1001 completes."""
        store.insert_reach(1001, None, is_terminal=True)
        store.insert_reach(1002, 1001)
        store.insert_reach(1003, 1001)
        store.upsert_desired(1001, revision=1)
        store.upsert_desired(1002, revision=1)
        store.upsert_desired(1003, revision=1)

        store.update_current(1001, "h1+d1", "h1", "d1", applied_revision=1)

        eligible = store.get_eligible_reaches()
        reach_ids = sorted([r["reach_id"] for r in eligible])
        assert reach_ids == [1002, 1003]

    def test_stale_downstream_blocks_upstream(self, store):
        """If downstream becomes stale again (rev bumped), upstream is blocked
        even though downstream was previously complete."""
        store.insert_reach(1001, None, is_terminal=True)
        store.insert_reach(1002, 1001)
        store.upsert_desired(1001, revision=1)
        store.upsert_desired(1002, revision=1)

        store.update_current(1001, "h+d", "h", "d", applied_revision=1)
        store.update_current(1002, "h2+d2", "h2", "d2", applied_revision=1)

        store.upsert_desired(1001, revision=2)
        store.upsert_desired(1002, revision=2)

        eligible_ids = [r["reach_id"] for r in store.get_eligible_reaches()]
        assert 1001 in eligible_ids
        assert 1002 not in eligible_ids

        store.update_current(1001, "h3+d3", "h3", "d3", applied_revision=2)

        eligible_ids = [r["reach_id"] for r in store.get_eligible_reaches()]
        assert 1002 in eligible_ids


class TestSetProcessing:
    def test_set_processing_noop_when_no_current_state(self, store):
        """set_processing on a fresh reach (no current_state row) is a no-op — no row created."""
        store.insert_reach(1001, None, is_terminal=True)
        store.upsert_desired(1001, revision=1)

        store.set_processing(1001, True)

        with store._connect() as conn:
            row = conn.execute(
                "SELECT count(*) as cnt FROM current_state WHERE reach_id = %s", (1001,)
            ).fetchone()
        assert row["cnt"] == 0

    def test_set_processing_updates_existing_row(self, store):
        store.insert_reach(1001, None, is_terminal=True)
        store.upsert_desired(1001, revision=1)
        store.update_current(1001, "h+d", "h", "d", applied_revision=0)

        store.set_processing(1001, True)

        eligible = store.get_eligible_reaches()
        assert len(eligible) == 0

        store.set_processing(1001, False)

        eligible = store.get_eligible_reaches()
        assert len(eligible) == 1


class TestUpdateCurrent:
    def test_insert_new_current_state(self, store):
        store.insert_reach(1001, None, is_terminal=True)
        store.upsert_desired(1001, revision=1)

        store.update_current(1001, "abc+N200S200E300W200", "abc", "N200S200E300W200", applied_revision=1)

        state = store.get_all_state()
        assert len(state) == 1
        assert state[0]["model_id"] == "abc+N200S200E300W200"
        assert state[0]["applied_revision"] == 1
        assert state[0]["processing"] is False

    def test_upsert_overwrites_current_state(self, store):
        store.insert_reach(1001, None, is_terminal=True)
        store.upsert_desired(1001, revision=2)

        store.update_current(1001, "v1+d", "v1", "d", applied_revision=1)
        store.update_current(1001, "v2+d", "v2", "d", applied_revision=2)

        state = store.get_all_state()
        assert state[0]["model_id"] == "v2+d"
        assert state[0]["applied_revision"] == 2
