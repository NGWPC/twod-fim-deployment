"""Tests for reconciliation sensor — actual sensor invocation."""

import dagster as dg

from orchestrator.defs.resources import StateStoreResource
from orchestrator.defs.sensors import reconciliation_sensor


def _invoke_sensor(store):
    """Invoke the sensor with a test StateStoreResource via Dagster resource injection."""
    resource = StateStoreResource(connection_string=store.connection_string)
    context = dg.build_sensor_context(resources={"state_store": resource})
    return reconciliation_sensor(context)


class TestReconciliationSensor:
    def test_returns_empty_when_no_eligible(self, store):
        store.insert_reach(1001, None, is_terminal=True)
        store.upsert_desired(1001, revision=1)
        store.update_current(1001, "h+d", "h", "d", applied_revision=1)

        result = _invoke_sensor(store)

        assert result.run_requests == []
        assert result.dynamic_partitions_requests == []

    def test_returns_run_requests_for_eligible(self, store):
        store.insert_reach(1001, None, is_terminal=True)
        store.upsert_desired(1001, revision=1)

        result = _invoke_sensor(store)

        assert len(result.run_requests) == 1
        assert result.run_requests[0].partition_key == "1001"
        assert result.run_requests[0].run_key == "build_model_1001_rev1"

    def test_builds_dynamic_partitions_request(self, store):
        store.insert_reach(1001, None, is_terminal=True)
        store.insert_reach(1002, 1001)
        store.upsert_desired(1001, revision=1)
        store.upsert_desired(1002, revision=1)

        store.update_current(1001, "h+d", "h", "d", applied_revision=1)

        result = _invoke_sensor(store)

        assert len(result.run_requests) == 1
        assert result.run_requests[0].partition_key == "1002"
        assert len(result.dynamic_partitions_requests) == 1
        assert result.dynamic_partitions_requests[0].partition_keys == ["1002"]

    def test_revision_in_run_key(self, store):
        store.insert_reach(1001, None, is_terminal=True)
        store.upsert_desired(1001, revision=3)

        result = _invoke_sensor(store)

        assert result.run_requests[0].run_key == "build_model_1001_rev3"

    def test_multiple_eligible_reaches(self, store):
        """Sensor emits RunRequests for all eligible reaches, not just the first."""
        store.insert_reach(1001, None, is_terminal=True)
        store.insert_reach(1002, 1001)
        store.insert_reach(1003, 1001)
        store.upsert_desired(1001, revision=1)
        store.upsert_desired(1002, revision=1)
        store.upsert_desired(1003, revision=1)

        store.update_current(1001, "h+d", "h", "d", applied_revision=1)

        result = _invoke_sensor(store)

        partition_keys = sorted([r.partition_key for r in result.run_requests])
        run_keys = sorted([r.run_key for r in result.run_requests])
        assert partition_keys == ["1002", "1003"]
        assert run_keys == ["build_model_1002_rev1", "build_model_1003_rev1"]
        assert sorted(result.dynamic_partitions_requests[0].partition_keys) == ["1002", "1003"]
