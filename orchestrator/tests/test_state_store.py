"""Tests for state_store.py — eligibility queries and state transitions."""

import pytest

from support.db import bump_revision


class TestGetEligibleReaches:
    """Downstream-first traversal: a reach is eligible only when stale AND
    downstream is complete (or terminal)."""

    def test_three_level_chain_traversal_order(self, store):
        """1001 (terminal) → 1002 → 1003 (headwater).
        Only terminal eligible first, then level by level."""
        store.insert_reach(1001, None, is_terminal=True)
        store.insert_reach(1002, 1001)
        store.insert_reach(1003, 1002, is_headwater=True)
        store.upsert_desired(1001)
        store.upsert_desired(1002)
        store.upsert_desired(1003)

        # Level 1: only terminal
        eligible = store.get_eligible_reaches()
        assert [r["reach_id"] for r in eligible] == [1001]

        # Complete 1001
        store.update_current(1001, "aa000001", "N1S1E1W1", applied_revision=0)

        # Level 2: 1002 now eligible
        eligible = store.get_eligible_reaches()
        assert [r["reach_id"] for r in eligible] == [1002]

        # Complete 1002
        store.update_current(1002, "aa000002", "N2S2E2W2", applied_revision=0)

        # Level 3: 1003 now eligible
        eligible = store.get_eligible_reaches()
        assert [r["reach_id"] for r in eligible] == [1003]

    def test_parallel_branches_eligible_simultaneously(self, store):
        """1001 (terminal) with two upstream: 1002, 1003.
        Both should be eligible after 1001 completes."""
        store.insert_reach(1001, None, is_terminal=True)
        store.insert_reach(1002, 1001)
        store.insert_reach(1003, 1001)
        store.upsert_desired(1001)
        store.upsert_desired(1002)
        store.upsert_desired(1003)

        store.update_current(1001, "aa000001", "N1S1E1W1", applied_revision=0)

        eligible = store.get_eligible_reaches()
        reach_ids = sorted([r["reach_id"] for r in eligible])
        assert reach_ids == [1002, 1003]

    def test_stale_downstream_blocks_upstream(self, store):
        """Re-staled downstream blocks upstream. Also verifies that
        bump_revision triggers the DB revision auto-increment."""
        store.insert_reach(1001, None, is_terminal=True)
        store.insert_reach(1002, 1001)
        store.upsert_desired(1001)
        store.upsert_desired(1002)

        store.update_current(1001, "aabb1122", "N1S1E1W1", applied_revision=0)
        store.update_current(1002, "aa000002", "N2S2E2W2", applied_revision=0)

        bump_revision(store, 1001)
        bump_revision(store, 1002)

        eligible = store.get_eligible_reaches()
        eligible_ids = [r["reach_id"] for r in eligible]
        assert 1001 in eligible_ids
        assert 1002 not in eligible_ids
        r1001 = next(r for r in eligible if r["reach_id"] == 1001)
        assert r1001["revision"] == 1

        store.update_current(1001, "aa000003", "N3S3E3W3", applied_revision=1)

        eligible_ids = [r["reach_id"] for r in store.get_eligible_reaches()]
        assert 1002 in eligible_ids


class TestSetProcessing:
    """Processing flag: no-op when no current_state row; toggle excludes/re-includes from eligible."""

    def test_set_processing_noop_when_no_current_state(self, store):
        """set_processing on a fresh reach (no current_state row) is a no-op — no row created."""
        store.insert_reach(1001, None, is_terminal=True)
        store.upsert_desired(1001)

        store.set_processing(1001, True)

        with store._connect() as conn:
            row = conn.execute(
                "SELECT count(*) as cnt FROM current_state WHERE reach_id = %s", (1001,)
            ).fetchone()
        assert row["cnt"] == 0

    def test_set_processing_updates_existing_row(self, store):
        """Toggle processing flag: TRUE excludes from eligible, FALSE re-includes."""
        store.insert_reach(1001, None, is_terminal=True)
        store.upsert_desired(1001)
        bump_revision(store, 1001)
        store.update_current(1001, "aabb1122", "N1S1E1W1", applied_revision=0)

        store.set_processing(1001, True)

        eligible = store.get_eligible_reaches()
        assert len(eligible) == 0

        store.set_processing(1001, False)

        eligible = store.get_eligible_reaches()
        assert len(eligible) == 1


class TestUpdateCurrent:
    def test_insert_new_current_state(self, store):
        """First update_current creates the row; model_id is DB-generated."""
        store.insert_reach(1001, None, is_terminal=True)
        store.upsert_desired(1001)

        store.update_current(1001, "abcdef01", "N200S200E300W200", applied_revision=0)

        with store._connect() as conn:
            row = conn.execute(
                "SELECT model_id, applied_revision, processing FROM current_state WHERE reach_id = %s",
                (1001,),
            ).fetchone()
        assert row["model_id"] == "abcdef01+N200S200E300W200"
        assert row["applied_revision"] == 0
        assert row["processing"] is False

    def test_upsert_overwrites_current_state(self, store):
        """Second update_current overwrites identity and model_id."""
        store.insert_reach(1001, None, is_terminal=True)
        store.upsert_desired(1001)

        store.update_current(1001, "abcdef01", "N1S1E1W1", applied_revision=0)
        store.update_current(1001, "abcdef02", "N1S1E1W1", applied_revision=0)

        with store._connect() as conn:
            row = conn.execute(
                "SELECT model_id, applied_revision FROM current_state WHERE reach_id = %s",
                (1001,),
            ).fetchone()
        assert row["model_id"] == "abcdef02+N1S1E1W1"
        assert row["applied_revision"] == 0

    def test_identity_change_clears_old_runs(self, store):
        """Changing identity_hash in update_current must clear runs under the old identity."""
        store.insert_reach(1001, None, is_terminal=True)
        store.upsert_desired(1001)
        store.update_current(1001, "aabb1122", "N1S1E1W1", applied_revision=0)

        with store._connect() as conn:
            conn.execute(
                """INSERT INTO runs
                       (reach_id, model_identity_hash, domain_code,
                        run_identity_hash, q_cms, bc_type, run_uri)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (1001, "aabb1122", "N1S1E1W1", "ccdd3344", 50, "nd", "s3://test/run1"),
            )
            conn.commit()
            row = conn.execute("SELECT count(*) as cnt FROM runs WHERE reach_id = %s", (1001,)).fetchone()
            assert row["cnt"] == 1

        store.update_current(1001, "eeff5566", "N2S2E2W2", applied_revision=0)

        with store._connect() as conn:
            row = conn.execute("SELECT count(*) as cnt FROM runs WHERE reach_id = %s", (1001,)).fetchone()
            assert row["cnt"] == 0

    def test_same_identity_preserves_runs(self, store):
        """Updating current_state with the same identity_hash must not clear runs."""
        store.insert_reach(1001, None, is_terminal=True)
        store.upsert_desired(1001)
        store.update_current(1001, "aabb1122", "N1S1E1W1", applied_revision=0)

        with store._connect() as conn:
            conn.execute(
                """INSERT INTO runs
                       (reach_id, model_identity_hash, domain_code,
                        run_identity_hash, q_cms, bc_type, run_uri)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (1001, "aabb1122", "N1S1E1W1", "ccdd3344", 50, "nd", "s3://test/run1"),
            )
            conn.commit()

        store.update_current(1001, "aabb1122", "N2S2E2W2", applied_revision=0)

        with store._connect() as conn:
            row = conn.execute("SELECT count(*) as cnt FROM runs WHERE reach_id = %s", (1001,)).fetchone()
            assert row["cnt"] == 1


class TestInsertReachValidation:
    def test_terminal_with_downstream_raises(self, store):
        """Terminal reach with non-null reach_to_id violates terminal_link_chk."""
        with pytest.raises(ValueError, match="Terminal reach"):
            store.insert_reach(1001, 999, is_terminal=True)

    def test_non_terminal_with_reason_raises(self, store):
        """Non-terminal with terminal_reason violates terminal_reason_presence_chk."""
        with pytest.raises(ValueError, match="terminal_reason"):
            store.insert_reach(1001, None, terminal_reason="outlet")

    def test_invalid_terminal_reason_raises(self, store):
        """terminal_reason not in (outlet, lake, coast) violates terminal_reason_chk."""
        with pytest.raises(ValueError, match="terminal_reason"):
            store.insert_reach(1001, None, is_terminal=True, terminal_reason="volcano")


class TestUpsertDesiredValidation:
    def test_invalid_flow_bounds_raises(self, store):
        """Inverted bounds violate desired_state_flow_bounds_chk."""
        store.insert_reach(1001, None, is_terminal=True)
        with pytest.raises(ValueError, match="q_lower_bound"):
            store.upsert_desired(1001, q_lower_bound=100, q_upper_bound=50)

    def test_equal_flow_bounds_raises(self, store):
        """Equal bounds violate desired_state_flow_bounds_chk (strict less-than)."""
        store.insert_reach(1001, None, is_terminal=True)
        with pytest.raises(ValueError, match="q_lower_bound"):
            store.upsert_desired(1001, q_lower_bound=50, q_upper_bound=50)


class TestGetDesired:
    def test_round_trip(self, store):
        """Insert desired_state and read it back with correct flow bounds and revision."""
        store.insert_reach(1001, None, is_terminal=True)
        store.upsert_desired(1001, q_lower_bound=10, q_upper_bound=200)

        row = store.get_desired(1001)
        assert row["reach_id"] == 1001
        assert row["q_lower_bound"] == 10
        assert row["q_upper_bound"] == 200
        assert row["revision"] == 0

    def test_returns_none_for_missing_reach(self, store):
        """get_desired returns None when reach has no desired_state row."""
        assert store.get_desired(9999) is None


class TestUpsertDesiredRevision:
    def test_upsert_update_bumps_revision_via_trigger(self, store):
        """ON CONFLICT DO UPDATE triggers the BEFORE UPDATE trigger on desired_state."""
        store.insert_reach(1001, None, is_terminal=True)
        store.upsert_desired(1001, q_lower_bound=0, q_upper_bound=100)

        row = store.get_desired(1001)
        assert row["revision"] == 0

        store.upsert_desired(1001, q_lower_bound=10, q_upper_bound=200)

        row = store.get_desired(1001)
        assert row["revision"] == 1
        assert row["q_lower_bound"] == 10
        assert row["q_upper_bound"] == 200
