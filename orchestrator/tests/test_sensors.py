"""Tests for reconciliation sensor — actual sensor invocation."""

import dagster as dg

from orchestrator.defs.resources import StateStoreResource
from orchestrator.defs.sensors import reconciliation_sensor
from support.db import bump_revision


def _invoke_sensor(store):
    """Invoke the sensor with a test StateStoreResource via Dagster resource injection."""
    resource = StateStoreResource(connection_string=store.connection_string)
    context = dg.build_sensor_context(resources={"state_store": resource})
    return reconciliation_sensor(context)


class TestReconciliationSensor:
    def test_returns_empty_when_no_eligible(self, store):
        """Completed reach produces empty run_requests and dynamic_partitions_requests."""
        store.insert_reach(1001, None, is_terminal=True)
        store.upsert_desired(1001)
        store.update_current(1001, "aabb1122", "N1S1E1W1", applied_revision=0)

        result = _invoke_sensor(store)

        assert result.run_requests == []
        assert result.dynamic_partitions_requests == []

    def test_returns_run_requests_for_eligible(self, store):
        """Eligible reach produces RunRequest with correct partition_key and run_key."""
        store.insert_reach(1001, None, is_terminal=True)
        store.upsert_desired(1001)

        result = _invoke_sensor(store)

        assert len(result.run_requests) == 1
        assert result.run_requests[0].partition_key == "1001"
        assert result.run_requests[0].run_key == "build_model_1001_rev0"

    def test_builds_dynamic_partitions_request(self, store):
        """Sensor emits AddDynamicPartitionsRequest with the eligible partition_keys."""
        store.insert_reach(1001, None, is_terminal=True)
        store.upsert_desired(1001)

        result = _invoke_sensor(store)

        assert len(result.dynamic_partitions_requests) == 1
        assert result.dynamic_partitions_requests[0].partition_keys == ["1001"]

    def test_revision_in_run_key(self, store):
        """run_key encodes the current revision for dedup across revision bumps."""
        store.insert_reach(1001, None, is_terminal=True)
        store.upsert_desired(1001)
        bump_revision(store, 1001, times=3)

        result = _invoke_sensor(store)

        assert result.run_requests[0].run_key == "build_model_1001_rev3"
