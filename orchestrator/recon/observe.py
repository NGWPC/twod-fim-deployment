"""Look where intent says this reach's model should be, and record what is true.

Intent implies an identity (identity.py); the identity implies an address; this
module looks at that address. A lookup, not a search: a model built from other
inputs may sit in the bucket beside it, and it is a previous intent's leftovers,
not this reach's state — nothing here sorts candidates or picks a newest.

The materialized_models row this writes is PROOF that model intent is
materialized, stamped with the revision it proves. Finding nothing at the
address deletes the row, which retracts the proof in the same statement — this
is the only writer of that table, so recording a finished job and noticing a
deletion are one mechanism seen from two sides.

A model counts as existing when its manifest is present and sound. build_model
writes model_manifest.json last, so a half-written build has artifacts but no
manifest and is correctly invisible; a manifest that fails verification
(belongs to another reach, sits in a folder its own realization code does not
name, or its identity does not hash to what it claims) is treated as absent and
reported, never adopted.
"""

import json
import logging

import psycopg

from recon import db, identity, intent, scenarios, storage

logger = logging.getLogger(__name__)


def observe_reach(reach_id: int, *, conn: psycopg.Connection | None = None) -> dict:
    """Reconcile materialized_models for one reach against storage.

    Returns what happened, for the check to log and a notebook to show:
      predicted   the identity hash intent implies (None if no intent)
      found       the model_id adopted, or None
      changed     whether the table was altered
      refused     verification problems, when a manifest was found but not trusted
    """
    wanted = intent.effective(reach_id, conn=conn)
    if wanted is None:
        # No intent, nothing to be materialized; retract any stale proof.
        removed = bool(db.query(
            "DELETE FROM materialized_models WHERE reach_id = %s RETURNING reach_id",
            (reach_id,), conn=conn))
        return {"reach_id": reach_id, "predicted": None, "found": None,
                "changed": removed, "note": "no effective intent"}

    _, predicted = identity.model_identity(wanted)
    base = storage.model_base_path(reach_id)

    found_model_id, refused = None, []
    for name in storage.list_subfolders(base, prefix=f"{predicted}_"):
        manifest = storage.read_json(f"{base}/{name}/{storage.MANIFEST_FILENAME}")
        if manifest is None:
            continue  # build not finished; the manifest is written last
        problems = identity.verify_manifest(manifest, reach_id, name)
        if problems:
            refused.append({"folder": name, "problems": problems})
            logger.warning("refused manifest at %s/%s: %s", base, name, problems)
            continue
        found_model_id = name
        break

    before = db.one("SELECT model_id, applied_revision FROM materialized_models WHERE reach_id = %s",
                    (reach_id,), conn=conn)

    if found_model_id is None:
        removed = bool(db.query(
            "DELETE FROM materialized_models WHERE reach_id = %s RETURNING reach_id",
            (reach_id,), conn=conn))
        return {"reach_id": reach_id, "predicted": predicted, "found": None,
                "changed": removed, "refused": refused,
                "was": before["model_id"] if before else None}

    identity_hash, _, domain_code = found_model_id.partition("_")
    db.query(
        """
        INSERT INTO materialized_models (reach_id, identity_hash, domain_code, applied_revision, confirmed_at)
        VALUES (%s, %s, %s, %s, now())
        ON CONFLICT (reach_id) DO UPDATE SET
            identity_hash = EXCLUDED.identity_hash,
            domain_code = EXCLUDED.domain_code,
            applied_revision = EXCLUDED.applied_revision,
            confirmed_at = now()
        """,
        (reach_id, identity_hash, domain_code, wanted["revision"]),
        conn=conn,
    )
    changed = before is None or before["model_id"] != found_model_id \
        or before["applied_revision"] != wanted["revision"]
    return {"reach_id": reach_id, "predicted": predicted, "found": found_model_id,
            "changed": changed, "refused": refused,
            "was": before["model_id"] if before else None}


def observe_nd_runs(reach_id: int, *, conn: psycopg.Connection | None = None) -> dict:
    """Reconcile materialized_nd_runs for one reach against storage.

    Lookup down to the run identity, listing the rest of the way. Intent fixes
    the address down to model identity and run identity, so getting there is a
    prediction. Below that, nothing is intent's to say: the job derives the
    slope itself from the reach's own DEM, and the adaptive step algorithm
    decides which discharges are hydraulically distinct enough to keep — so the
    loop reads both back and judges them, via storage.nd_library_path for the
    slope and the q= listing below it.

    Judged how: the library must SPAN the authored discharge range. Density
    (the `ld_q_*` deltas guide.md also calls for) is not checked by default,
    though the job now accepts those deltas as inputs (as of the solver
    generalization PR) — tightening this to a real density check is a
    follow-up, not a blocker.

    Anything short of a whole, verified library writes no row. A row is proof,
    and proof of a partial library is not a smaller proof — it is none.

    Returns what happened, for the check to log and a notebook to show.
    """
    out: dict = {"reach_id": reach_id, "step": "nd", "found": None, "changed": False}
    before = db.one("SELECT run_identity_hash, q_set, applied_revision FROM materialized_nd_runs"
                    " WHERE reach_id = %s", (reach_id,), conn=conn)

    def retract(note: str) -> dict:
        removed = bool(db.query(
            "DELETE FROM materialized_nd_runs WHERE reach_id = %s RETURNING reach_id",
            (reach_id,), conn=conn))
        return {**out, "changed": removed, "note": note}

    wanted = intent.effective(reach_id, conn=conn)
    if wanted is None:
        return retract("no effective intent")

    # Runs are addressed under the model they were run against, so a reach
    # whose model intent is not itself materialized has nowhere to look. The
    # model rung will be the gap in that case anyway.
    _, predicted_model = identity.model_identity(wanted)
    model = db.one("SELECT identity_hash, model_id FROM materialized_models WHERE reach_id = %s",
                   (reach_id,), conn=conn)
    if model is None:
        return retract("no materialized model")
    if model["identity_hash"] != predicted_model:
        return retract(f"materialized model {model['identity_hash']} is not the "
                       f"{predicted_model} intent now implies")

    _, run_hash = identity.run_identity(wanted)
    library = storage.nd_library_path(reach_id, model["model_id"], run_hash)
    if library is None:
        # Either nothing has been written yet, or more than one nd= folder is
        # there and none of them can be called the library. storage logs which.
        return retract("no single nd=<slope> folder to read")
    out.update({"predicted": run_hash, "library": library})

    discharges = sorted(
        q for q in (identity.parse_q_folder(n) for n in storage.list_subfolders(library))
        if q is not None
    )
    if not discharges:
        return retract("no scenarios in library")

    lower, upper = wanted["q_lower_bound"], wanted["q_upper_bound"]
    if lower is None or upper is None:
        return retract("discharge range is unauthored, so nothing can satisfy it")
    if min(discharges) > lower or max(discharges) < upper:
        return retract(f"library spans {min(discharges)}-{max(discharges)}, "
                       f"intent asks for {lower}-{upper}")

    # Every scenario must be readable and sound before any of them counts. The
    # job publishes the max-q run last, so a library caught mid-publish usually
    # fails the span check above and never reaches this loop.
    curve, refused = [], []
    # The realization directory as it appears under the run identity:
    # `<nd|kwse>=<value>/q=<value>`. A scenario manifest claims one of these in
    # its scenario_code, and verification is that claim against this location.
    nd_folder = library.rsplit("/", 1)[-1]
    for q in discharges:
        scenario_dir = f"{nd_folder}/{identity.q_folder(q)}"
        path = f"{library}/{identity.q_folder(q)}/{storage.SCENARIO_MANIFEST_FILENAME}"
        manifest = storage.read_json(path)
        if manifest is None:
            return {**retract(f"scenario q={q} has no manifest yet"), "refused": refused}
        problems = identity.verify_scenario_manifest(
            manifest, reach_id, run_hash, model["model_id"], scenario_dir)
        if problems:
            refused.append({"q": q, "problems": problems})
            logger.warning("refused scenario manifest at %s: %s", path, problems)
            return {**retract(f"scenario q={q} refused"), "refused": refused}
        curve.append({"q": q, "wse": float(manifest["properties"]["nominal_wse"])})

    # The values the reach ABOVE will need, at this reach's upstream end. One
    # ND run per discharge, so the minimum WSE at a discharge is simply that
    # run's — the curve gains other members only when KWSE runs join it.
    us_wse_max = max(point["wse"] for point in curve)

    db.query(
        """
        INSERT INTO materialized_nd_runs
            (reach_id, model_id, run_identity_hash, q_set,
             us_wse_max, us_min_wse_curve, applied_revision, confirmed_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (reach_id) DO UPDATE SET
            model_id            = EXCLUDED.model_id,
            run_identity_hash   = EXCLUDED.run_identity_hash,
            q_set               = EXCLUDED.q_set,
            us_wse_max          = EXCLUDED.us_wse_max,
            us_min_wse_curve    = EXCLUDED.us_min_wse_curve,
            applied_revision    = EXCLUDED.applied_revision,
            confirmed_at        = now()
        """,
        (reach_id, model["model_id"], run_hash, discharges,
         us_wse_max, json.dumps(curve), wanted["revision"]),
        conn=conn,
    )
    changed = (before is None
               or before["run_identity_hash"] != run_hash
               or list(before["q_set"]) != discharges
               or before["applied_revision"] != wanted["revision"])
    return {**out, "found": f"{len(discharges)} scenarios", "q_set": discharges,
            "us_wse_max": us_wse_max, "changed": changed, "refused": refused}


def observe_kwse_runs(reach_id: int, *, conn: psycopg.Connection | None = None) -> dict:
    """Reconcile materialized_kwse_runs for one reach against storage.

    The address is a prediction all the way down, unlike the nd case. Intent
    fixes the model and run identity, and the loop itself chose every stage
    target, so there is nothing here to discover by listing — each scenario is
    looked up at the exact folder the plan names.

    WHAT IS ASKED FOR IS THE PLAN, NOT THE GRID. DR-033 skips a stage target
    with no downstream run within Δz/2, so a check that looked for every stage
    between the bounds could never be satisfied: it would find the library short
    on every pass, resubmit forever with no backoff, and never let the reach
    above it start. plan.py is a function of current state, so the plan
    recomputed here is the plan the job was given, and a skipped target is
    absent from both.

    An empty plan is materialized, not pending. A reach whose whole envelope was
    skipped, or whose ceiling sat below its floor, is asking for nothing and has
    it — recording that unblocks the reach above rather than stalling it.

    Anything short of every planned scenario writes no row. A row is proof, and
    proof of a partial library is not a smaller proof, it is none.
    """
    out: dict = {"reach_id": reach_id, "step": "kwse", "found": None, "changed": False}
    before = db.one("SELECT run_identity_hash, scenario_index, applied_revision"
                    " FROM materialized_kwse_runs WHERE reach_id = %s",
                    (reach_id,), conn=conn)

    def retract(note: str) -> dict:
        removed = bool(db.query(
            "DELETE FROM materialized_kwse_runs WHERE reach_id = %s RETURNING reach_id",
            (reach_id,), conn=conn))
        return {**out, "changed": removed, "note": note}

    wanted = intent.effective(reach_id, conn=conn)
    if wanted is None:
        return retract("no effective intent")

    # Runs are addressed under the model they were run against, so a reach whose
    # model intent is not itself materialized has nowhere to look.
    _, predicted_model = identity.model_identity(wanted)
    model = db.one("SELECT identity_hash FROM materialized_models WHERE reach_id = %s",
                   (reach_id,), conn=conn)
    if model is None:
        return retract("no materialized model")
    if model["identity_hash"] != predicted_model:
        return retract(f"materialized model {model['identity_hash']} is not the "
                       f"{predicted_model} intent now implies")

    try:
        context = scenarios.planned(reach_id, conn=conn)
    except scenarios.NotPlannable as why:
        # Includes the ordinary cases: a terminal reach, or a downstream
        # neighbour still building. The gap calculation reports which.
        return retract(str(why))

    out["planned"] = len(context.plan.scenarios)
    out["skipped"] = len(context.plan.skipped)

    index: dict[int, list[dict]] = {}
    refused = []
    for scenario in context.plan.scenarios:
        folder = scenarios.scenario_dir("KWSE", scenario.z, scenario.q)
        path = storage.scenario_manifest_path(
            reach_id, context.model_id, context.run_identity_hash, folder)
        manifest = storage.read_json(path)
        if manifest is None:
            return {**retract(f"scenario kwse={scenario.z:g}/q={scenario.q} has no "
                              "manifest yet"), "refused": refused}
        problems = identity.verify_scenario_manifest(
            manifest, reach_id, context.run_identity_hash, context.model_id, folder)
        if problems:
            refused.append({"scenario": folder, "problems": problems})
            logger.warning("refused scenario manifest at %s: %s", path, problems)
            return {**retract(f"scenario {folder} refused"), "refused": refused}
        # The two stages this run carries: what it achieved at this reach's
        # upstream end, which the reach above matches against, and what was
        # imposed at its downstream end, which named the folder it sits in.
        index.setdefault(scenario.q, []).append(
            {"wse": float(manifest["properties"]["nominal_wse"]), "bc": scenario.z})

    scenario_index = [{"q": q, "runs": sorted(runs, key=lambda r: r["wse"])}
                      for q, runs in sorted(index.items())]

    db.query(
        """
        INSERT INTO materialized_kwse_runs
            (reach_id, model_id, run_identity_hash, scenario_index,
             applied_revision, confirmed_at)
        VALUES (%s, %s, %s, %s, %s, now())
        ON CONFLICT (reach_id) DO UPDATE SET
            model_id          = EXCLUDED.model_id,
            run_identity_hash = EXCLUDED.run_identity_hash,
            scenario_index    = EXCLUDED.scenario_index,
            applied_revision  = EXCLUDED.applied_revision,
            confirmed_at      = now()
        """,
        (reach_id, context.model_id, context.run_identity_hash,
         json.dumps(scenario_index), wanted["revision"]),
        conn=conn,
    )
    changed = (before is None
               or before["run_identity_hash"] != context.run_identity_hash
               or before["scenario_index"] != scenario_index
               or before["applied_revision"] != wanted["revision"])
    return {**out, "found": f"{len(context.plan.scenarios)} scenarios",
            "changed": changed, "refused": refused}
