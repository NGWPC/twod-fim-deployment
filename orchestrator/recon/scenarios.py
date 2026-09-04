"""The KWSE scenarios a reach should have, gathered from the database and storage.

plan.py decides which scenarios belong in a library and is deliberately pure.
This module is the impure half: it reads the rows and folders that plan.py needs,
and hands back the answer together with the addresses a caller has to build from
it.

It exists as its own module because TWO callers need the identical plan and must
not disagree about it:

  check.py   turns the plan into a job payload — the scenarios to run
  observe.py turns the plan into a materialization check — the scenarios that
             must be present for the step to count as satisfied

That second use is what keeps the loop from spinning. A stage target with no
downstream run within Δz/2 is skipped rather than run (DR-033), so a check that
looked for the whole grid would never be satisfied, would resubmit forever, and
would never let the reach above it start. The plan is what intent actually asks
for, and because plan.py is a function of current state, the plan computed when
work is submitted and the plan recomputed when results are read agree.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg

from recon import db, identity, intent, plan, storage


@dataclass(frozen=True)
class Planned:
    """A reach's KWSE plan, plus what is needed to address the files involved."""

    plan: plan.Plan
    model_id: str  # this reach's model, which its results are filed under
    run_identity_hash: str  # shared by this reach's nd and kwse runs alike
    nd_slope: float  # names this reach's own nd= folder, the root of every chain
    downstream_id: int
    ds_model_id: str
    ds_run_identity_hash: str


class NotPlannable(Exception):
    """Why no plan can be produced for this reach yet.

    Not every cause is a fault: a downstream neighbour that has not finished is
    the ordinary case on a network still building upward. The caller decides
    whether that is a gap to wait on or a failure to record.
    """


def nd_slope(reach_id: int, model_id: str, run_hash: str) -> float:
    """The slope naming this reach's `nd=<slope>` folder.

    Emergent — the job derives it from the reach's own DEM — so the folder the
    job created is the only place it can be read. Recovering it is enough to
    name that folder again, which is all a hotstart reference needs.
    """
    library = storage.nd_library_path(reach_id, model_id, run_hash)
    if library is None:
        raise NotPlannable(f"reach {reach_id} has no single nd=<slope> folder")
    slope = identity.parse_nd_folder(library.rsplit("/", 1)[-1])
    if slope is None:
        raise NotPlannable(f"reach {reach_id} nd folder {library} names no slope")
    return slope


def downstream_runs(
    downstream_id: int, *, conn: psycopg.Connection | None = None
) -> list[plan.DownstreamRun]:
    """Every scenario the downstream reach has, as candidate boundaries.

    Assembled from BOTH of that reach's proofs. A low target often binds to its
    normal-depth run and a higher one to its stage libraries, and DR-033 draws no
    distinction — whichever achieved stage sits nearest wins.

    Each run contributes two different stages: the achieved one comes from the
    materialized rows, and the imposed one is what named its folder. For a
    normal-depth run that is the slope, read back from the folder itself.
    """
    nd = db.one("SELECT model_id, run_identity_hash, us_min_wse_curve"
                " FROM materialized_nd_runs WHERE reach_id = %s",
                (downstream_id,), conn=conn)
    if nd is None:
        raise NotPlannable(f"downstream reach {downstream_id} has no nd library")

    # One ND run per discharge, so the per-discharge minimum IS that run's
    # achieved stage: there is nothing else at that discharge to be lower.
    slope = nd_slope(downstream_id, nd["model_id"], nd["run_identity_hash"])
    runs = [plan.DownstreamRun(q=int(p["q"]), wse=float(p["wse"]),
                               bc_type="ND", bc_value=slope)
            for p in nd["us_min_wse_curve"]]

    kwse = db.one("SELECT scenario_index FROM materialized_kwse_runs WHERE reach_id = %s",
                  (downstream_id,), conn=conn)
    if kwse is not None:
        runs += [plan.DownstreamRun(q=int(g["q"]), wse=float(r["wse"]),
                                    bc_type="KWSE", bc_value=float(r["bc"]))
                 for g in kwse["scenario_index"] for r in g["runs"]]
    return runs


def planned(reach_id: int, *, conn: psycopg.Connection | None = None) -> Planned:
    """This reach's KWSE plan, or NotPlannable saying what is missing.

    Everything here is read at one point in time, and plan.py turns it into an
    answer that depends on nothing else — so asking twice gives the same list
    unless the network itself moved underneath.
    """
    wanted = intent.effective(reach_id, conn=conn)
    if wanted is None:
        raise NotPlannable(f"reach {reach_id} has no effective intent")
    if wanted["is_terminal"]:
        # ISU-013: nothing below it to bound a stage library with.
        raise NotPlannable(f"reach {reach_id} is terminal, so it has no downstream stages")
    if wanted["ld_ds_z_delta"] is None:
        raise NotPlannable(
            f"reach {reach_id} has no stage increment (ld_ds_z_delta) authored")

    model = db.one("SELECT model_id FROM materialized_models WHERE reach_id = %s",
                   (reach_id,), conn=conn)
    own_nd = db.one("SELECT model_id, run_identity_hash, q_set FROM materialized_nd_runs"
                    " WHERE reach_id = %s", (reach_id,), conn=conn)
    if model is None or own_nd is None:
        raise NotPlannable(f"reach {reach_id} has no materialized model and nd library")

    downstream_id = wanted["reach_to_id"]
    ds_nd = db.one("SELECT model_id, run_identity_hash FROM materialized_nd_runs"
                   " WHERE reach_id = %s", (downstream_id,), conn=conn)
    if ds_nd is None:
        raise NotPlannable(f"downstream reach {downstream_id} has no nd library")

    # Read once: each call lists the reach's run prefix in storage.
    slope = nd_slope(reach_id, own_nd["model_id"], own_nd["run_identity_hash"])

    return Planned(
        plan=plan.plan(
            q_set=list(own_nd["q_set"]),
            dz=float(wanted["ld_ds_z_delta"]),
            downstream=downstream_runs(downstream_id, conn=conn),
            nd_slope=slope,
            kwse_upper_bound=(None if wanted["kwse_upper_bound"] is None
                              else float(wanted["kwse_upper_bound"])),
        ),
        model_id=model["model_id"],
        run_identity_hash=own_nd["run_identity_hash"],
        nd_slope=slope,
        downstream_id=downstream_id,
        ds_model_id=ds_nd["model_id"],
        ds_run_identity_hash=ds_nd["run_identity_hash"],
    )


def scenario_dir(bc_type: str, bc_value: float, q: int) -> str:
    """The `<nd=…|kwse=…>/q=…` folder one scenario point implies.

    One place renders a boundary value into a folder name, so the payload
    builder, the materialization check and the hotstart references cannot drift
    apart about where a scenario lives.
    """
    downstream = (identity.nd_folder(bc_value) if bc_type == "ND"
                  else identity.kwse_folder(bc_value))
    return f"{downstream}/{identity.q_folder(q)}"
