#!/usr/bin/env python3
"""
Manual cleanup script for OCR debug artifacts.

This script allows administrators to manually clean up old artifacts from the database.
It can be run independently of the OCR engine.

Usage:
    python scripts/cleanup_artifacts.py [--hours HOURS] [--dry-run]

Examples:
    # Clean artifacts older than 24 hours (default)
    python scripts/cleanup_artifacts.py

    # Clean artifacts older than 12 hours
    python scripts/cleanup_artifacts.py --hours 12

    # Preview what would be deleted without actually deleting
    python scripts/cleanup_artifacts.py --dry-run

    # Clean artifacts older than 48 hours with preview
    python scripts/cleanup_artifacts.py --hours 48 --dry-run
"""

import argparse
import logging
import os
import sys
from pathlib import Path

from psycopg2 import sql

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ocr_engine.ocr.engine.db_locking import DbLockingManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description="Clean up old OCR debug artifacts from the database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Delete artifacts older than this many hours (default: 24)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be deleted without actually deleting",
    )
    parser.add_argument("--force", action="store_true", help="Skip confirmation prompt")

    args = parser.parse_args()

    # Validate hours
    if args.hours < 1:
        logger.error("❌ Hours must be at least 1")
        return 1

    # Check for database connection
    pg_dsn = os.environ.get("OCR_PG_DSN")
    if not pg_dsn:
        logger.error("❌ OCR_PG_DSN environment variable not set")
        logger.info("Please set OCR_PG_DSN to your PostgreSQL connection string")
        return 1

    # Initialize database manager
    db = DbLockingManager(
        pg_table="public.ocr_raw_texts",  # Not used for cleanup
        profile_name="cleanup_script",
        enabled=True,
    )

    # Test connection
    conn = db.get_connection()
    if not conn:
        logger.error("❌ Failed to connect to database")
        return 1

    logger.info("✅ Connected to database")

    # Get count of artifacts to be deleted (preview)
    try:
        with conn.cursor() as cur:
            table_id = db._get_table_identifier(db.ARTIFACTS_TABLE)
            count_query = sql.SQL(
                """
                SELECT COUNT(*) 
                FROM {}
                WHERE created_at < NOW() - make_interval(hours => %s)
                """
            ).format(table_id)
            cur.execute(count_query, (args.hours,))
            count = cur.fetchone()[0]

            # Get total count
            total_query = sql.SQL("SELECT COUNT(*) FROM {}").format(table_id)
            cur.execute(total_query)
            total = cur.fetchone()[0]

            # Get size estimate
            size_query = sql.SQL(
                """
                SELECT pg_size_pretty(SUM(LENGTH(content))::bigint) as size
                FROM {}
                WHERE created_at < NOW() - make_interval(hours => %s)
                """
            ).format(table_id)
            cur.execute(size_query, (args.hours,))
            size_result = cur.fetchone()
            size = size_result[0] if size_result and size_result[0] else "0 bytes"
    except Exception as e:
        logger.error(f"❌ Failed to query artifacts: {e}")
        db.close()
        return 1

    # Display summary
    logger.info("\n📊 Artifact Summary:")
    logger.info(f"   Total artifacts in database: {total:,}")
    logger.info(f"   Artifacts older than {args.hours}h: {count:,}")
    logger.info(f"   Estimated size to free: {size}")
    logger.info(f"   Retention policy: Keep artifacts from last {args.hours} hours")

    if count == 0:
        logger.info("\n✅ No artifacts to clean up!")
        db.close()
        return 0

    if args.dry_run:
        logger.info(f"\n🔍 DRY RUN: Would delete {count:,} artifacts")
        logger.info("Run without --dry-run to actually delete")
        db.close()
        return 0

    # Confirm deletion
    if not args.force:
        logger.warning(f"\n⚠️  WARNING: This will permanently delete {count:,} artifacts!")
        response = input("Continue? [y/N]: ")
        if response.lower() != "y":
            logger.info("Cancelled by user")
            db.close()
            return 0

    # Perform cleanup
    logger.info(f"\n🧹 Cleaning up artifacts older than {args.hours} hours...")
    try:
        deleted_count = db.cleanup_old_artifacts(args.hours)
        logger.info(f"✅ Successfully deleted {deleted_count:,} artifacts")
        logger.info(f"💾 Freed approximately {size}")

        # Show remaining count
        with conn.cursor() as cur:
            table_id = db._get_table_identifier(db.ARTIFACTS_TABLE)
            cur.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(table_id))
            remaining = cur.fetchone()[0]
            logger.info(f"📊 Remaining artifacts: {remaining:,}")

    except Exception as e:
        logger.error(f"❌ Cleanup failed: {e}")
        db.close()
        return 1

    db.close()
    logger.info("\n✅ Cleanup completed successfully!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
