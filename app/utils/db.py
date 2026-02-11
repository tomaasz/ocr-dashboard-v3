"""
OCR Dashboard V2 - Database Utilities
PostgreSQL connection helpers.
"""

import os
from contextlib import contextmanager


def get_pg_connection():
    """Get PostgreSQL connection if DSN is configured."""
    dsn = os.environ.get("OCR_PG_DSN")
    if not dsn:
        return None

    try:
        import psycopg2

        return psycopg2.connect(dsn)
    except Exception:
        return None


@contextmanager
def pg_cursor():
    """Context manager for PostgreSQL cursor."""
    conn = get_pg_connection()
    if conn is None:
        yield None
        return

    try:
        with conn, conn.cursor() as cur:
            yield cur
    finally:
        conn.close()


def execute_query(query: str, params: tuple = None) -> list:
    """Execute a query and return all results."""
    with pg_cursor() as cur:
        if cur is None:
            return []
        cur.execute(query, params or ())
        return cur.fetchall()


def execute_single(query: str, params: tuple = None) -> tuple | None:
    """Execute a query and return single result."""
    with pg_cursor() as cur:
        if cur is None:
            return None
        cur.execute(query, params or ())
        return cur.fetchone()


def execute_write(query: str, params: tuple = None) -> int:
    """Execute a write query and return affected row count."""
    with pg_cursor() as cur:
        if cur is None:
            return 0
        cur.execute(query, params or ())
        return int(cur.rowcount or 0)
