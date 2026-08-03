import psycopg
from psycopg.rows import dict_row

from orchestrator.config import settings


class StateStore:
    """DB layer for the reconciliation loop. Operates on the twodfim schema."""

    def __init__(self, connection_string: str | None = None):
        self.connection_string = connection_string or settings.pipeline_db_connection_string

    def _connect(self):
        return psycopg.connect(self.connection_string, row_factory=dict_row)

    # -- reach_network (for seeding demo data only) --

    def insert_reach(self, reach_id: int, reach_to_id: int | None,
                     is_terminal: bool = False, is_headwater: bool = False,
                     terminal_reason: str | None = None,
                     geom: str = "LINESTRING(0 0, 1 1)",
                     total_da_sqkm: float | None = None,
                     stream_order: int | None = None,
                     slope: float | None = None):
        """Insert a reach into the network. Demo/seed helper — production loads use bulk ETL."""
        _VALID_TERMINAL_REASONS = ("outlet", "lake", "coast")
        if is_terminal:
            if reach_to_id is not None:
                raise ValueError("Terminal reach cannot have a downstream (reach_to_id)")
            if terminal_reason is None:
                terminal_reason = "outlet"
            elif terminal_reason not in _VALID_TERMINAL_REASONS:
                raise ValueError(f"terminal_reason must be one of {_VALID_TERMINAL_REASONS}, got '{terminal_reason}'")
        elif terminal_reason is not None:
            raise ValueError("Non-terminal reach cannot have a terminal_reason")

        with self._connect() as conn:
            conn.execute(
                """INSERT INTO reach_network
                       (reach_id, reach_to_id, is_terminal, is_headwater, terminal_reason,
                        total_da_sqkm, stream_order, slope, geom)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, ST_GeomFromText(%s, 5070))
                   ON CONFLICT (reach_id) DO NOTHING""",
                (reach_id, reach_to_id, is_terminal, is_headwater, terminal_reason,
                 total_da_sqkm, stream_order, slope, geom),
            )
            conn.commit()

    # -- desired_state --

    def upsert_desired(self, reach_id: int, q_lower_bound: int = 0, q_upper_bound: int = 100):
        """Insert or update desired state for a reach. Revision is trigger-managed."""
        if q_lower_bound >= q_upper_bound:
            raise ValueError(f"q_lower_bound ({q_lower_bound}) must be less than q_upper_bound ({q_upper_bound})")

        with self._connect() as conn:
            conn.execute(
                """INSERT INTO desired_state (reach_id, q_lower_bound, q_upper_bound)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (reach_id) DO UPDATE
                   SET q_lower_bound = EXCLUDED.q_lower_bound,
                       q_upper_bound = EXCLUDED.q_upper_bound""",
                (reach_id, q_lower_bound, q_upper_bound),
            )
            conn.commit()

    def get_desired(self, reach_id: int) -> dict | None:
        """Return the desired_state row for a reach, or None if not found."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM desired_state WHERE reach_id = %s", (reach_id,)
            ).fetchone()
        return row

    # -- current_state --

    def update_current(self, reach_id: int, identity_hash: str,
                       domain_code: str, applied_revision: int,
                       build_model_version: str | None = None):
        """Upsert current_state after a successful build. Clears old runs if
        identity changes (FK constraint). model_id is DB-generated.

        Currently hardcodes processing=FALSE. When the cascade coordinator is
        built (build → nd → kwse), this must change — the coordinator owns
        the processing lifecycle, and build completion alone should not
        release the reach."""
        with self._connect() as conn:
            # FK runs_reach_identity_fk requires clearing runs when model
            # identity changes. No-op when identity_hash is unchanged.
            conn.execute(
                "DELETE FROM runs WHERE reach_id = %s AND model_identity_hash != %s",
                (reach_id, identity_hash),
            )
            conn.execute(
                """INSERT INTO current_state
                       (reach_id, identity_hash, domain_code, processing, applied_revision, build_model_version)
                   VALUES (%s, %s, %s, FALSE, %s, %s)
                   ON CONFLICT (reach_id) DO UPDATE SET
                       identity_hash = EXCLUDED.identity_hash,
                       domain_code = EXCLUDED.domain_code,
                       processing = FALSE,
                       applied_revision = EXCLUDED.applied_revision,
                       build_model_version = EXCLUDED.build_model_version""",
                (reach_id, identity_hash, domain_code, applied_revision, build_model_version),
            )
            conn.commit()

    def set_processing(self, reach_id: int, processing: bool):
        """Set processing flag on an existing current_state row.

        Reserved for the future cascade coordinator (build → nd → kwse).
        Individual workers like process_build_model should not call this —
        the coordinator sets TRUE when claiming a reach and FALSE when the
        full cascade completes or fails.

        No-op if no current_state row exists (fresh reach).
        """
        with self._connect() as conn:
            conn.execute(
                "UPDATE current_state SET processing = %s WHERE reach_id = %s",
                (processing, reach_id),
            )
            conn.commit()

    # -- queries --

    def get_eligible_reaches(self) -> list[dict]:
        """Return reaches that are stale, not processing, and whose downstream
        is complete (or terminal). Used by the reconciliation sensor."""
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT d.reach_id, d.revision, rn.reach_to_id, rn.is_terminal
                FROM desired_state d
                JOIN reach_network rn ON d.reach_id = rn.reach_id
                LEFT JOIN current_state c ON d.reach_id = c.reach_id
                WHERE (c.applied_revision IS NULL OR c.applied_revision < d.revision)
                  AND (rn.is_terminal = TRUE
                       OR EXISTS (
                           SELECT 1 FROM current_state ds_c
                           JOIN desired_state ds_d ON ds_c.reach_id = ds_d.reach_id
                           WHERE ds_c.reach_id = rn.reach_to_id
                             AND ds_c.identity_hash IS NOT NULL
                             AND ds_c.domain_code IS NOT NULL
                             AND ds_c.applied_revision >= ds_d.revision
                       ))
                  AND COALESCE(c.processing, FALSE) = FALSE
            """).fetchall()
        return rows

    def get_reach_states(self) -> list[dict]:
        """Per-reach reconciliation status, for the smoke check.

        A reach is reconciled when current_state matches the desired
        revision and is not mid-processing.
        model_id is the DB-generated identity_hash_domain_code.
        """
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT
                    d.reach_id,
                    c.model_id,
                    COALESCE(
                        c.applied_revision >= d.revision
                        AND c.identity_hash IS NOT NULL
                        AND c.domain_code IS NOT NULL
                        AND COALESCE(c.processing, FALSE) = FALSE,
                        FALSE
                    ) AS reconciled
                FROM desired_state d
                LEFT JOIN current_state c ON d.reach_id = c.reach_id
                ORDER BY d.reach_id
            """).fetchall()
        return rows

    def reset(self):
        """Truncate all data tables. Schema and triggers are preserved."""
        with self._connect() as conn:
            conn.execute("TRUNCATE runs, current_state, desired_state, reach_network CASCADE")
            conn.commit()
