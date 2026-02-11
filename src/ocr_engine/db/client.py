from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import psycopg


@dataclass(frozen=True)
class ClaimedJob:
    job_id: str
    job_dir: str
    state: str


class DbClient:
    """
    Minimal DB client (C3).
    - Connects using DATABASE_URL
    - Executes canonical queries from db/queries.sql (embedded or read)
    - Uses explicit transactions (no implicit magic)
    """

    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or os.environ.get("DATABASE_URL")
        if not self.dsn:
            raise ValueError("DATABASE_URL is not set")

    def connect(self) -> psycopg.Connection:
        # autocommit False -> explicit transactions
        return psycopg.connect(self.dsn, autocommit=False)

    @staticmethod
    def read_sql(path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def apply_schema(self, schema_sql_path: Path) -> None:
        sql = self.read_sql(schema_sql_path)
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()

    def claim_next_job(self) -> ClaimedJob | None:
        """
        Claim next READY job with SKIP LOCKED.
        Returns None if no READY jobs.
        """
        claim_sql = """
WITH picked AS (
  SELECT job_id
  FROM jobs
  WHERE state = 'READY'
  ORDER BY updated_at ASC
  FOR UPDATE SKIP LOCKED
  LIMIT 1
)
UPDATE jobs
SET state = 'RUNNING',
    updated_at = now()
WHERE job_id IN (SELECT job_id FROM picked)
RETURNING job_id::text, job_dir, state::text;
""".strip()

        with self.connect() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(claim_sql)
                    row = cur.fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        if not row:
            return None

        job_id, job_dir, state = row
        return ClaimedJob(job_id=job_id, job_dir=job_dir, state=state)

    def mark_done(self, job_id: str) -> None:
        sql = """
UPDATE jobs
SET state = 'DONE',
    updated_at = now(),
    last_error = NULL
WHERE job_id = %s;
""".strip()

        with self.connect() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(sql, (job_id,))
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def mark_failed(self, job_id: str, last_error: str) -> None:
        sql = """
UPDATE jobs
SET state = 'FAILED',
    updated_at = now(),
    last_error = %s
WHERE job_id = %s;
""".strip()

        with self.connect() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(sql, (last_error, job_id))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
