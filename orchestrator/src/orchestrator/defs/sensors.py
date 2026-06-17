import dagster as dg

from orchestrator.defs.assets import process_build_model, reaches_partitions
from orchestrator.defs.resources import StateStoreResource


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

    Double-submission prevented by:
    1. run_key dedup (primary) — Dagster skips duplicate run_keys per reach+revision
    2. processing flag in DB (secondary) — get_eligible_reaches() excludes processing=TRUE
       Note: processing flag is a no-op for fresh reaches (no current_state row yet)
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
