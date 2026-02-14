#!/usr/bin/env python3
"""
Background Folder Indexer Service

Continuously scans NAS folders and updates folder_file_entries in PostgreSQL.
This allows OCR profiles to start immediately without blocking on folder scans.

Usage:
    python scripts/folder_indexer.py                    # Run once
    python scripts/folder_indexer.py --daemon           # Run continuously
    python scripts/folder_indexer.py --daemon --interval 300  # Every 5 min
    python scripts/folder_indexer.py --path /mnt/nas/...  # Index specific path
"""

import argparse
import logging
import os
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import psycopg2
from psycopg2.extras import execute_values

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Graceful shutdown
SHUTDOWN = False

ALLOWED_IMAGE_SUFFIXES: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
)


def signal_handler(signum, frame):
    global SHUTDOWN
    logger.info("🛑 Shutdown signal received, finishing current batch...")
    SHUTDOWN = True


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def get_db_connection():
    """Get PostgreSQL connection from environment."""
    pg_uri = (
        os.environ.get("OCR_PG_URI")
        or os.environ.get("OCR_PG_DSN")
        or os.environ.get("DATABASE_URL")
    )
    if not pg_uri:
        logger.error("❌ No database connection: set OCR_PG_URI, OCR_PG_DSN, or DATABASE_URL")
        return None
    try:
        conn = psycopg2.connect(pg_uri)
        conn.autocommit = True
        return conn
    except Exception as e:
        logger.error(f"❌ Database connection failed: {e}")
        return None


def scan_folder(source_path: str) -> list[tuple[str, str, str, float | None]]:
    """Scan a folder and return list of file entries."""
    path = Path(source_path)
    if not path.exists() or not path.is_dir():
        return []

    entries = []
    try:
        for entry in path.iterdir():
            if not entry.is_file():
                continue
            if entry.name in {"Thumbs.db", ".DS_Store", "desktop.ini"}:
                continue
            # Skip hidden files
            if entry.name.startswith("."):
                continue
            if entry.suffix.lower() not in ALLOWED_IMAGE_SUFFIXES:
                continue
            try:
                mtime_epoch = entry.stat().st_mtime
            except Exception:
                mtime_epoch = None
            entries.append((source_path, entry.name, str(entry), mtime_epoch))
    except PermissionError:
        logger.warning(f"⚠️ Permission denied: {source_path}")
    except Exception as e:
        logger.warning(f"⚠️ Error scanning {source_path}: {e}")

    return entries


def sync_folder_to_db(conn, source_path: str, entries: list) -> int:
    """Sync folder entries to database."""
    file_count = len(entries)
    try:
        with conn.cursor() as cur:
            # Update folder count
            cur.execute(
                """
                INSERT INTO public.folder_file_counts (source_path, file_count, last_updated)
                VALUES (%s, %s, NOW())
                ON CONFLICT (source_path)
                DO UPDATE SET file_count = EXCLUDED.file_count, last_updated = NOW()
                """,
                (source_path, file_count),
            )

            # Delete old entries
            cur.execute(
                "DELETE FROM public.folder_file_entries WHERE source_path = %s",
                (source_path,),
            )

            # Insert new entries in batch
            if entries:
                execute_values(
                    cur,
                    """
                    INSERT INTO public.folder_file_entries
                        (source_path, file_name, full_path, mtime_epoch)
                    VALUES %s
                    """,
                    entries,
                    page_size=1000,
                )
        return file_count
    except Exception as e:
        logger.error(f"❌ DB sync failed for {source_path}: {e}")
        return 0


def get_known_source_paths(conn) -> list[str]:
    """Get all unique source_paths from OCR table that need indexing."""
    table = os.environ.get("OCR_PG_TABLE", "public.ocr_raw_texts")
    try:
        with conn.cursor() as cur:
            # Get folders from OCR table that exist in filesystem
            cur.execute(
                f"""
                SELECT DISTINCT source_path 
                FROM {table}
                WHERE source_path IS NOT NULL
                ORDER BY source_path
                """
            )
            return [row[0] for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"❌ Failed to get source paths: {e}")
        return []


def get_folders_needing_update(conn, max_age_minutes: int = 30) -> list[str]:
    """Get folders that haven't been updated recently."""
    table = os.environ.get("OCR_PG_TABLE", "public.ocr_raw_texts")
    try:
        with conn.cursor() as cur:
            # Folders in OCR table but not in folder_file_counts OR outdated
            cur.execute(
                f"""
                SELECT DISTINCT r.source_path
                FROM {table} r
                LEFT JOIN public.folder_file_counts f ON r.source_path = f.source_path
                WHERE r.source_path IS NOT NULL
                  AND (f.source_path IS NULL 
                       OR f.last_updated < NOW() - INTERVAL '%s minutes')
                ORDER BY r.source_path
                """,
                (max_age_minutes,),
            )
            return [row[0] for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"❌ Failed to get folders needing update: {e}")
        return []


def index_folder(conn, source_path: str) -> dict[str, Any]:
    """Index a single folder and return stats."""
    start = time.time()
    entries = scan_folder(source_path)
    scan_time = time.time() - start

    db_start = time.time()
    count = sync_folder_to_db(conn, source_path, entries)
    db_time = time.time() - db_start

    return {
        "path": source_path,
        "files": count,
        "scan_time": scan_time,
        "db_time": db_time,
        "total_time": scan_time + db_time,
    }


def index_folders_parallel(conn, folders: list[str], max_workers: int = 4) -> list[dict]:
    """Index multiple folders in parallel."""
    results = []

    # Scan in parallel (I/O bound)
    folder_entries = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_folder = {executor.submit(scan_folder, f): f for f in folders if not SHUTDOWN}
        for future in as_completed(future_to_folder):
            if SHUTDOWN:
                break
            folder = future_to_folder[future]
            try:
                entries = future.result()
                folder_entries[folder] = entries
            except Exception as e:
                logger.error(f"❌ Scan failed for {folder}: {e}")

    # Sync to DB sequentially (to avoid connection issues)
    for folder, entries in folder_entries.items():
        if SHUTDOWN:
            break
        start = time.time()
        count = sync_folder_to_db(conn, folder, entries)
        results.append(
            {
                "path": folder,
                "files": count,
                "total_time": time.time() - start,
            }
        )

    return results


def run_indexer(
    conn,
    specific_path: str | None = None,
    daemon: bool = False,
    interval: int = 300,
    max_age: int = 30,
    parallel_workers: int = 4,
):
    """Main indexer loop."""
    iteration = 0

    while True:
        iteration += 1
        logger.info(f"📂 Starting indexer run #{iteration}...")
        start_time = time.time()

        if specific_path:
            # Index specific path
            folders = [specific_path]
        else:
            # Get folders needing update
            folders = get_folders_needing_update(conn, max_age)

        if not folders:
            logger.info("✅ All folders are up to date")
        else:
            logger.info(f"📁 Found {len(folders)} folders to index")

            if len(folders) > 1 and parallel_workers > 1:
                results = index_folders_parallel(conn, folders, parallel_workers)
            else:
                results = []
                for folder in folders:
                    if SHUTDOWN:
                        break
                    result = index_folder(conn, folder)
                    results.append(result)
                    logger.info(
                        f"  ✓ {result['files']:>4} files in {result['total_time']:.1f}s: "
                        f"{Path(result['path']).name}"
                    )

            total_files = sum(r["files"] for r in results)
            total_time = time.time() - start_time
            logger.info(
                f"✅ Indexed {len(results)} folders, {total_files} files in {total_time:.1f}s"
            )

        if not daemon or SHUTDOWN:
            break

        # Wait for next iteration
        logger.info(f"💤 Sleeping {interval}s until next run...")
        for _ in range(interval):
            if SHUTDOWN:
                break
            time.sleep(1)

        if SHUTDOWN:
            break

    logger.info("👋 Indexer stopped")


def main():
    parser = argparse.ArgumentParser(description="Background Folder Indexer Service")
    parser.add_argument("--path", help="Specific folder path to index")
    parser.add_argument("--daemon", action="store_true", help="Run continuously")
    parser.add_argument(
        "--interval",
        type=int,
        default=300,
        help="Seconds between runs in daemon mode (default: 300)",
    )
    parser.add_argument(
        "--max-age",
        type=int,
        default=30,
        help="Max age in minutes before re-indexing (default: 30)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Parallel workers for scanning (default: 4)",
    )
    args = parser.parse_args()

    conn = get_db_connection()
    if not conn:
        sys.exit(1)

    try:
        run_indexer(
            conn,
            specific_path=args.path,
            daemon=args.daemon,
            interval=args.interval,
            max_age=args.max_age,
            parallel_workers=args.workers,
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
