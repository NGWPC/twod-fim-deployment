"""Look at storage, and make current_state say what is really there.

The first step of every check, and the only way anything is ever recorded. A
job's return value is not evidence: a job can report success and leave nothing
behind, and a file can vanish with no job involved at all. So the loop believes
its eyes.

Because it is the only writer of what exists, one function decides what
"exists" means, and recording a finished job and noticing a deletion are the
same code running in two directions. See reconciliation-loop.md, "Tracking
Storage Changes and Staleness".

A model counts as existing when its manifest is present. build_model writes
that file last, so a half-written model has artifacts but no manifest, and is
correctly invisible.
"""

import re

import psycopg

from recon import db, storage

MODEL_ID_RE = re.compile(
    r"^(?P<identity_hash>[0-9a-f]{8})_(?P<domain_code>N\d+S\d+E\d+W\d+)$"
)


def _models_in_storage(reach_id: int) -> list[dict]:
    """Every complete model in storage for this reach, newest last.

    Folder names are parsed rather than opened: the paths are self-documenting
    by design (guide.md), so the name is a fact, not a guess. The manifest is
    still read, because it carries what the path cannot: twod_fim_version, which
    is the provenance a selective rollback needs, and created_at, which orders
    rebuilds.
    """
    base = storage.model_base_path(reach_id)
    found = []
    for name in storage.list_subfolders(base):
        parsed = MODEL_ID_RE.match(name)
        if parsed is None:
            continue  # not a model folder; leave whatever it is alone
        manifest = storage.read_json(f"{base}/{name}/{storage.MANIFEST_FILENAME}")
        if manifest is None:
            continue  # build never finished; the model does not exist yet
        found.append(
            {
                "model_id": name,
                "identity_hash": parsed["identity_hash"],
                "domain_code": parsed["domain_code"],
                "build_model_version": manifest.get("twod_fim_version"),
                "created_at": manifest.get("created_at") or "",
            }
        )
    found.sort(key=lambda m: m["created_at"])
    return found


def observe_reach(reach_id: int, *, conn: psycopg.Connection | None = None) -> dict:
    """Reconcile current_state for one reach against storage.

    Returns what was seen and what changed, so a caller can log it and a
    notebook can show it.

    When several models exist — a rebuild under a new identity, say — the newest
    by build time is the one recorded. current_state holds one model per reach,
    so it can only name one; the others stay in storage until a lifecycle policy
    or a person removes them.
    """
    models = _models_in_storage(reach_id)
    before = db.one(
        "SELECT identity_hash, domain_code, model_id FROM current_state WHERE reach_id = %s",
        (reach_id,),
        conn=conn,
    )

    if not models:
        # Nothing there. If we previously said there was, that is a deletion,
        # and the gap it opens is what rebuilds the model.
        removed = bool(
            db.query(
                "DELETE FROM current_state WHERE reach_id = %s RETURNING reach_id",
                (reach_id,),
                conn=conn,
            )
        )
        return {"reach_id": reach_id, "model": None, "changed": removed,
                "was": before["model_id"] if before else None}

    newest = models[-1]
    db.query(
        """
        INSERT INTO current_state (reach_id, identity_hash, domain_code,
                                   build_model_version, confirmed_at)
        VALUES (%s, %s, %s, %s, now())
        ON CONFLICT (reach_id) DO UPDATE SET
            identity_hash = EXCLUDED.identity_hash,
            domain_code = EXCLUDED.domain_code,
            build_model_version = EXCLUDED.build_model_version,
            confirmed_at = now()
        """,
        (reach_id, newest["identity_hash"], newest["domain_code"],
         newest["build_model_version"]),
        conn=conn,
    )
    changed = before is None or before["model_id"] != newest["model_id"]
    return {"reach_id": reach_id, "model": newest["model_id"], "changed": changed,
            "was": before["model_id"] if before else None,
            "others_in_storage": [m["model_id"] for m in models[:-1]]}
