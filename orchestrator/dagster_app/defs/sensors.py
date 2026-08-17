import dagster as dg

from dagster_app.defs.assets import process_build_model, reaches_partitions
from dagster_app.defs.resources import StateStoreResource


@dg.sensor(
    target=process_build_model,
    minimum_interval_seconds=30,
)
def reconciliation_sensor(
    context: dg.SensorEvaluationContext,
    state_store: StateStoreResource,
) -> dg.SensorResult:
    """Reconciliation sensor: polls DB for eligible reaches and submits build_model runs.

    Downstream-first traversal: a reach is eligible only when it is stale AND
    its downstream dependency is complete (or it is terminal).

    Double-submission guard: run_key dedup — Dagster skips duplicate run_keys
    per reach+revision. The DB processing flag is reserved for the future
    cascade coordinator (build → nd → kwse) and is not set by current workers.

    Note: After Dagster retries are exhausted for a given run_key, the reach stays
    stale but the sensor will not resubmit it (same run_key). To retry:
    bump desired_state (any field change increments revision via trigger,
    producing a new run_key) or manually re-launch the asset.
    """
    store = state_store.get_store()
    eligible = store.get_eligible_reaches()

    if not eligible:
        context.log.info("No eligible reaches")
        return dg.SensorResult(run_requests=[], dynamic_partitions_requests=[])

    partition_keys = [str(r["reach_id"]) for r in eligible]

    run_requests = [
        dg.RunRequest(
            partition_key=str(r["reach_id"]),
            run_key=f"build_model_{r['reach_id']}_rev{r['revision']}",
        )
        for r in eligible
    ]

    context.log.info(f"Submitting {len(run_requests)} reaches: {partition_keys}")

    return dg.SensorResult(
        run_requests=run_requests,
        dynamic_partitions_requests=[
            reaches_partitions.build_add_request(partition_keys),
        ],
    )
