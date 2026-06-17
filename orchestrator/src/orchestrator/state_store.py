import psycopg
from psycopg.rows import dict_row

from orchestrator.config import settings


class StateStore:
    def __init__(self, connection_string: str | None = None):
        self.connection_string = connection_string or settings.pipeline_db_connection_string
        self._init_db()

    def _connect(self):
        return psycopg.connect(self.connection_string, row_factory=dict_row)

    def _init_db(self):
        with self._connect() as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS postgis")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS reach_network (
                    reach_id     INTEGER PRIMARY KEY,
                    reach_to_id  INTEGER REFERENCES reach_network(reach_id),
                    is_terminal  BOOLEAN NOT NULL DEFAULT FALSE,
                    is_headwater BOOLEAN NOT NULL DEFAULT FALSE,
                    is_lake      BOOLEAN NOT NULL DEFAULT FALSE,
                    geom         GEOMETRY
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS overrides (
                    override_id TEXT PRIMARY KEY,
                    reach_id    INTEGER NOT NULL REFERENCES reach_network(reach_id),
                    created_at  TIMESTAMP NOT NULL,
                    created_by  TEXT NOT NULL,
                    description TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS desired_state (
                    reach_id                              INTEGER PRIMARY KEY REFERENCES reach_network(reach_id),
                    min_flow                              REAL,
                    max_flow                              REAL,
                    initial_dq_step_for_nd                REAL,
                    solver                                TEXT,
                    model_domain                          GEOMETRY,
                    override_id                           TEXT REFERENCES overrides(override_id),
                    sdr_commit                            TEXT,
                    library_density_mean_stage_threshold  REAL,
                    library_density_max_stage_threshold   REAL,
                    library_density_max_stage_interval    REAL,
                    q_set                                 TEXT,
                    ds_min_kwse                           REAL,
                    ds_max_kwse                           REAL,
                    revision                              INTEGER NOT NULL DEFAULT 0,
                    updated_at                            TIMESTAMP,
                    updated_by                            TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS current_state (
                    reach_id         INTEGER PRIMARY KEY REFERENCES reach_network(reach_id),
                    model_id         TEXT NOT NULL,
                    identity_hash    TEXT NOT NULL,
                    domain_code      TEXT NOT NULL,
                    processing       BOOLEAN NOT NULL DEFAULT FALSE,
                    q_set            TEXT NOT NULL DEFAULT '[]',
                    ds_min_kwse      REAL NOT NULL DEFAULT 0,
                    ds_max_kwse      REAL NOT NULL DEFAULT 0,
                    applied_revision INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    reach_id                  INTEGER NOT NULL REFERENCES reach_network(reach_id),
                    run_identity_hash         TEXT NOT NULL,
                    model_id                  TEXT NOT NULL,
                    identity_hash             TEXT NOT NULL,
                    run_type                  TEXT NOT NULL CHECK (run_type IN ('nd', 'kwse')),
                    q_cms                     REAL NOT NULL,
                    bc_type                   TEXT NOT NULL,
                    kwse_m                    REAL,
                    depth_uri                 TEXT NOT NULL,
                    stl_nominal_wse           REAL,
                    status                    TEXT NOT NULL CHECK (status IN ('completed', 'failed', 'cancelled')),
                    started_at                TIMESTAMP,
                    completed_at              TIMESTAMP,
                    hotstart_from_run_hash    TEXT,
                    transfer_bc_from_reach_id INTEGER,
                    transfer_bc_from_run_hash TEXT,
                    PRIMARY KEY (reach_id, identity_hash, run_identity_hash, q_cms, kwse_m)
                )
            """)
            conn.commit()

    # -- reach_network (for seeding demo data only) --

    def insert_reach(self, reach_id: int, reach_to_id: int | None,
                     is_terminal: bool = False, is_headwater: bool = False):
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO reach_network (reach_id, reach_to_id, is_terminal, is_headwater)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (reach_id) DO NOTHING""",
                (reach_id, reach_to_id, is_terminal, is_headwater),
            )
            conn.commit()

    # -- desired_state --

    def upsert_desired(self, reach_id: int, revision: int = 1):
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO desired_state (reach_id, revision)
                   VALUES (%s, %s)
                   ON CONFLICT (reach_id) DO UPDATE SET revision = EXCLUDED.revision""",
                (reach_id, revision),
            )
            conn.commit()

    def get_desired(self, reach_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM desired_state WHERE reach_id = %s", (reach_id,)
            ).fetchone()
        return row

    # -- current_state --

    def update_current(self, reach_id: int, model_id: str, identity_hash: str,
                       domain_code: str, applied_revision: int):
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO current_state
                       (reach_id, model_id, identity_hash, domain_code, processing, applied_revision)
                   VALUES (%s, %s, %s, %s, FALSE, %s)
                   ON CONFLICT (reach_id) DO UPDATE SET
                       model_id = EXCLUDED.model_id,
                       identity_hash = EXCLUDED.identity_hash,
                       domain_code = EXCLUDED.domain_code,
                       processing = FALSE,
                       applied_revision = EXCLUDED.applied_revision""",
                (reach_id, model_id, identity_hash, domain_code, applied_revision),
            )
            conn.commit()

    def set_processing(self, reach_id: int, processing: bool):
        """Set processing flag on an existing current_state row.

        If no current_state row exists (fresh reach), this is a no-op.
        The reach is still eligible via applied_revision IS NULL in the query.
        """
        with self._connect() as conn:
            conn.execute(
                "UPDATE current_state SET processing = %s WHERE reach_id = %s",
                (processing, reach_id),
            )
            conn.commit()

    # -- queries --

    def get_eligible_reaches(self) -> list[dict]:
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
                             AND ds_c.model_id IS NOT NULL
                             AND ds_c.applied_revision >= ds_d.revision
                       ))
                  AND COALESCE(c.processing, FALSE) = FALSE
            """).fetchall()
        return rows

    def get_stale_reaches(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT d.reach_id, d.revision,
                       COALESCE(c.applied_revision, 0) AS applied_revision
                FROM desired_state d
                LEFT JOIN current_state c ON d.reach_id = c.reach_id
                WHERE c.applied_revision IS NULL OR c.applied_revision < d.revision
            """).fetchall()
        return rows

    def get_all_state(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT rn.reach_id, rn.reach_to_id, rn.is_terminal, rn.is_headwater,
                       d.revision,
                       c.model_id, c.identity_hash, c.domain_code,
                       c.processing, c.applied_revision
                FROM reach_network rn
                LEFT JOIN desired_state d ON rn.reach_id = d.reach_id
                LEFT JOIN current_state c ON rn.reach_id = c.reach_id
                ORDER BY rn.reach_id
            """).fetchall()
        return rows

    def reset(self):
        with self._connect() as conn:
            conn.execute("DROP TABLE IF EXISTS runs")
            conn.execute("DROP TABLE IF EXISTS current_state")
            conn.execute("DROP TABLE IF EXISTS desired_state")
            conn.execute("DROP TABLE IF EXISTS overrides")
            conn.execute("DROP TABLE IF EXISTS reach_network")
            conn.commit()
        self._init_db()
