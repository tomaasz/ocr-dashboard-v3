#!/usr/bin/env python3
"""Apply all SQL migrations in scripts/migrations against OCR_PG_DSN."""

from __future__ import annotations

import os
from pathlib import Path

import psycopg2


def _get_migration_files(migrations_dir: Path) -> list[Path]:
    return sorted(p for p in migrations_dir.glob("*.sql") if p.is_file())


def main() -> int:
    dsn = os.environ.get("OCR_PG_DSN")
    if not dsn:
        print("Error: OCR_PG_DSN not set")
        return 1

    migrations_dir = Path(__file__).parent / "migrations"
    if not migrations_dir.exists():
        print(f"Error: {migrations_dir} not found")
        return 1

    migration_files = _get_migration_files(migrations_dir)
    if not migration_files:
        print(f"No migrations found in {migrations_dir}")
        return 0

    print(f"Connecting to DB... ({len(migration_files)} migrations)")
    try:
        conn = psycopg2.connect(dsn)
        conn.autocommit = True
    except Exception as exc:
        print(f"Migration failed: {exc}")
        return 1

    try:
        with conn.cursor() as cur:
            for migration in migration_files:
                sql = migration.read_text(encoding="utf-8")
                print(f"Applying migration: {migration.name}")
                cur.execute(sql)
        print("Migrations applied successfully.")
    except Exception as exc:
        print(f"Migration failed: {exc}")
        return 1
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
