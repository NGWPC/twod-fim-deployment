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
(belongs to another reach, or its identity does not hash to what it claims) is
treated as absent and reported, never adopted.
"""

import logging

import psycopg

from recon import db, identity, intent, storage

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
        problems = identity.verify_manifest(manifest, reach_id, predicted)
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
